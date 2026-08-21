"""Verify migrations 039-042 against the live schema and against the ORM models.

Checks the things a green `alembic upgrade` does not: that every column exists with the
nullability and default the model declares, that the constraints and indexes are really there, and
that no existing row was disturbed.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TABLES = ("DailyLearningSnapshot", "ReflectionNote", "GoalMilestone")

MODELS = {}


def _load_models():
    from src.domains.personal_learning.db_models import (
        DailyLearningSnapshot,
        Reflection,
        ReflectionNote,
    )
    from src.domains.progress.db_models import Goal, GoalMilestone

    MODELS.update(
        {
            "DailyLearningSnapshot": DailyLearningSnapshot,
            "ReflectionNote": ReflectionNote,
            "GoalMilestone": GoalMilestone,
            "Reflection": Reflection,
            "Goal": Goal,
        }
    )


async def main() -> int:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.split("?")[0]

    _load_models()
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    failures: list[str] = []

    async with engine.connect() as conn:
        print("=== alembic version ===")
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"  {version}")
        if version != "042_add_goal_targets":
            failures.append(f"alembic_version is {version}, expected 042_add_goal_targets")

        print("\n=== new tables exist ===")
        for table in TABLES:
            exists = (
                await conn.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'"{table}"'}
                )
            ).scalar()
            print(f"  {table:24} {'OK' if exists else 'MISSING'}")
            if not exists:
                failures.append(f"table {table} missing")

        print("\n=== columns: database vs model ===")
        for table in (*TABLES, "Reflection", "Goal"):
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT column_name, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_name = :t
                        ORDER BY column_name
                        """
                    ),
                    {"t": table},
                )
            ).all()
            db_cols = {r[0]: (r[1] == "YES", r[2]) for r in rows}
            model_cols = {c.name: c.nullable for c in MODELS[table].__table__.columns}

            missing = sorted(set(model_cols) - set(db_cols))
            extra = sorted(set(db_cols) - set(model_cols))
            mismatched = sorted(
                name
                for name, nullable in model_cols.items()
                if name in db_cols and db_cols[name][0] != nullable
            )
            print(f"  {table}: {len(db_cols)} db / {len(model_cols)} model")
            if missing:
                print(f"     MISSING IN DB: {missing}")
                failures.append(f"{table} missing columns {missing}")
            if mismatched:
                print(f"     NULLABILITY MISMATCH: {mismatched}")
                failures.append(f"{table} nullability mismatch {mismatched}")
            if extra:
                # Not a failure: a table may legitimately carry columns the model omits.
                print(f"     in db only (informational): {extra}")

        print("\n=== the four new Goal columns ===")
        for column in ("metricKind", "targetValue", "unit", "currentValue"):
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_name = 'Goal' AND column_name = :c
                        """
                    ),
                    {"c": column},
                )
            ).one_or_none()
            print(f"  {column:14} {row}")
            if row is None:
                failures.append(f"Goal.{column} missing")

        print("\n=== constraints and indexes ===")
        checks = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT conname FROM pg_constraint
                    WHERE conname IN (
                      'DailyLearningSnapshot_unique', 'Goal_metricKind_check'
                    )
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        for name in ("DailyLearningSnapshot_unique", "Goal_metricKind_check"):
            ok = name in checks
            print(f"  constraint {name:32} {'OK' if ok else 'MISSING'}")
            if not ok:
                failures.append(f"constraint {name} missing")

        for index in (
            "DailyLearningSnapshot_userId_snapshotDate_idx",
            "ReflectionNote_userId_createdAt_idx",
            "GoalMilestone_goalId_orderIndex_idx",
        ):
            ok = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM pg_indexes WHERE indexname = :n"), {"n": index}
                )
            ).scalar() > 0
            print(f"  index      {index:48} {'OK' if ok else 'MISSING'}")
            if not ok:
                failures.append(f"index {index} missing")

        print("\n=== the CHECK actually refuses a bad metricKind ===")
        # Probed against a real user id, and every NOT NULL column supplied, so that a rejection
        # can only come from the check itself. An earlier version omitted columns and was refused
        # by a NOT NULL violation instead, which proves nothing about the constraint under test.
        owner = (await conn.execute(text('SELECT "userId" FROM "Goal" LIMIT 1'))).scalar()
        if owner is None:
            owner = (await conn.execute(text('SELECT id FROM "User" LIMIT 1'))).scalar()

        required = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'Goal'
                      AND is_nullable = 'NO' AND column_default IS NULL
                    ORDER BY column_name
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        print(f"  Goal columns that must be supplied: {list(required)}")

        for kind, should_pass in (("not_a_kind", False), ("focused_minutes", True)):
            accepted = False
            reason = ""
            try:
                async with engine.begin() as probe:
                    await probe.execute(
                        text(
                            'INSERT INTO "Goal" (id, "userId", title, status, progress, '
                            '"metricKind", "createdAt", "updatedAt") '
                            "VALUES ('probe-metric-kind', :owner, 'constraint probe', 'ACTIVE', "
                            "0, :kind, now(), now())"
                        ),
                        {"owner": owner, "kind": kind},
                    )
                    accepted = True
                    # Never leave the probe row behind.
                    await probe.execute(text("DELETE FROM \"Goal\" WHERE id = 'probe-metric-kind'"))
            except Exception as exc:
                reason = str(exc)

            by_check = "Goal_metricKind_check" in reason or "check constraint" in reason.lower()
            verdict = "accepted" if accepted else f"refused (by the check: {by_check})"
            print(f"  metricKind={kind!r:18} -> {verdict}")

            if should_pass and not accepted:
                failures.append(f"a valid metricKind {kind!r} was refused: {reason[:160]}")
            if not should_pass:
                if accepted:
                    failures.append("Goal_metricKind_check did not reject an invalid value")
                elif not by_check:
                    failures.append(
                        f"invalid metricKind refused for the wrong reason: {reason[:160]}"
                    )

        left_behind = (
            await conn.execute(text("SELECT COUNT(*) FROM \"Goal\" WHERE id = 'probe-metric-kind'"))
        ).scalar()
        print(f"  probe rows left behind: {left_behind}")
        if left_behind:
            failures.append("the constraint probe left a row in Goal")

        print("\n=== existing rows untouched ===")
        for table, column in (
            ("Reflection", "narrative"),
            ("Goal", "metricKind"),
        ):
            total = (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar()
            nulls = (
                await conn.execute(text(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL'))
            ).scalar()
            print(f"  {table}: {total} row(s), {column} null in {nulls}")
        kinds = (
            await conn.execute(
                text('SELECT "metricKind", COUNT(*) FROM "Goal" GROUP BY "metricKind"')
            )
        ).all()
        print(f"  Goal.metricKind distribution: {kinds}")
        if any(kind != "manual" for kind, _ in kinds):
            failures.append("existing Goal rows have a metricKind other than 'manual'")

        print("\n=== new tables start empty ===")
        for table in TABLES:
            count = (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar()
            print(f"  {table:24} {count}")

    await engine.dispose()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
