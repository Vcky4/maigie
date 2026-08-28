"""Verify migrations `050`-`055`: six additive migrations, and nothing that moved data.

These six shipped across five phases of the adaptive goal lifecycle and **none of them was applied
until well after the code that depends on them was merged**. The symptom was a `500` on
`GET /api/v1/learning/home` reading `column ExamPrep.reviewAskedAt does not exist`, because the ORM
declares columns the database did not have. Worth stating plainly: the test suite cannot catch this,
since every test either stubs the repository or runs against a schema built from the models.

What they add:

- `050_prep_outcome` — `PrepOutcome`, plus `reviewAskedAt` / `reviewRemindersSent` /
  `reviewDeclinedAt` on `ExamPrep`.
- `051_goal_sched_change` — `GoalScheduleChange`.
- `052_plan_redistributed` — `StudyPlan.lastRedistributedAt`.
- `053_goal_lifecycle` — `GoalLifecycleAction`, and widens `GoalScheduleChange_reason_check` to
  include `system_extended`.
- `054_notification_push` — `Notification.pushedAt`.
- `055_goal_action_answer` — `learnerResponse` / `respondedAt` on `GoalLifecycleAction`, with a CHECK
  pairing them.

**All six are additive and none backfills.** So the assertions that matter are that row counts are
identical on both sides, that the three new tables are empty, and that no preparation's `status` was
rewritten — a backfill here would have started asking learners about exams they sat months ago, which
`050`'s own docstring rejects.

The one column with a default is `reviewRemindersSent`, `NOT NULL DEFAULT 0`. On Postgres 11+ that is
a metadata-only add with no table rewrite, and every existing row must read `0` rather than `NULL`:
zero is true of a preparation nobody has been asked about.

Run before and after, and diff the output:

    python scripts/db_direct.py python scripts/check_adaptive_050_055.py

Read-only.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

NEW_TABLES = ("PrepOutcome", "GoalScheduleChange", "GoalLifecycleAction")

NEW_COLUMNS = {
    "ExamPrep": ("reviewAskedAt", "reviewRemindersSent", "reviewDeclinedAt"),
    "StudyPlan": ("lastRedistributedAt",),
    "Notification": ("pushedAt",),
    "GoalLifecycleAction": ("learnerResponse", "respondedAt"),
}

#: Every table the six migrations touch. None of them moves a row, so each count must be identical
#: before and after — the only assertion that can catch an accidental data change.
COUNTED = ("ExamPrep", "StudyPlan", "Notification", "Goal", "StudyPlanItem")


def _url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise SystemExit("DATABASE_URL is not set")
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        print(f"alembic_version: {version}")

        tables = set(
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            )
            .scalars()
            .all()
        )

        print("\ntables")
        for table in NEW_TABLES:
            print(f"  {'present' if table in tables else 'ABSENT '}  {table}")

        print("\ncolumns")
        for table, columns in NEW_COLUMNS.items():
            if table not in tables:
                print(f"  (table {table} absent)")
                continue
            rows = {
                row[0]: (row[1], row[2])
                for row in (
                    await conn.execute(
                        text(
                            "SELECT column_name, is_nullable, column_default "
                            "FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = :t"
                        ),
                        {"t": table},
                    )
                ).all()
            }
            for column in columns:
                if column not in rows:
                    print(f"  ABSENT   {table}.{column}")
                    continue
                nullable, default = rows[column]
                print(f"  present  {table}.{column}  nullable={nullable} default={default}")

        print("\nrow counts (must be identical before and after)")
        for table in COUNTED:
            if table not in tables:
                continue
            count = (await conn.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar()
            print(f"  {table}: {count}")

        print("\nnew tables are empty (no migration backfills)")
        for table in NEW_TABLES:
            if table not in tables:
                continue
            count = (await conn.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar()
            print(f"  {table}: {count}")

        print("\npreparation statuses (no row re-flagged by the migration)")
        for row in (
            await conn.execute(
                text('SELECT status, count(*) FROM "ExamPrep" GROUP BY status ORDER BY status')
            )
        ).all():
            print(f"  {row[0]}: {row[1]}")

        if "reviewRemindersSent" in {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='ExamPrep'"
                    )
                )
            ).all()
        }:
            nulls = (
                await conn.execute(
                    text('SELECT count(*) FROM "ExamPrep" WHERE "reviewRemindersSent" IS NULL')
                )
            ).scalar()
            asked = (
                await conn.execute(
                    text('SELECT count(*) FROM "ExamPrep" WHERE "reviewAskedAt" IS NOT NULL')
                )
            ).scalar()
            print(
                f"\nreviewRemindersSent null rows: {nulls} (must be 0 — the default applies to "
                f"existing rows)"
            )
            print(f"reviewAskedAt set: {asked} (0 before the sweep has ever run)")

        check = (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'GoalScheduleChange_reason_check'"
                )
            )
        ).scalar()
        print(f"\nGoalScheduleChange_reason_check: {check}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
