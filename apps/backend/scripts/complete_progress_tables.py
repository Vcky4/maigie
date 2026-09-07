"""Complete the schema of the verbatim-carried progress-domain tables.

ScheduleBlock, ReviewItem, StudySession and UserStreak are new-model tables that were carried
verbatim (pre-data + data only), so they have their columns and rows but NO primary key, indexes, or
foreign keys — pg_dump emits those in the post-data section, which the carry skipped. Their data is
correct; only the constraints are missing.

This adds, per table, exactly what the SQLAlchemy model declares: the primary key, every index, and
every foreign key (SQLAlchemy compiles the DDL, so it matches the model precisely). Foreign keys are
added after nulling any orphan value on a SET NULL link, so an FK cannot fail on legacy dangling
references. Idempotent: a table that already has a PK is skipped.

    TARGET_URL=postgresql://user@host:5432/db python scripts/complete_progress_tables.py [--commit]
Dry run by default (does everything in one transaction and rolls back).
"""

from __future__ import annotations

import asyncio
import glob
import importlib
import os
import sys


class _DryRun(Exception):
    pass


async def main() -> None:
    commit = "--commit" in sys.argv
    os.environ["DATABASE_URL"] = os.environ["TARGET_URL"]
    sys.path.insert(0, os.getcwd())

    for f in glob.glob("src/domains/*/db_models.py"):
        importlib.import_module("src.domains." + f.split("/")[2] + ".db_models")

    from sqlalchemy import text
    from sqlalchemy.schema import AddConstraint, CreateIndex

    from src.domains.progress.db_models import (
        ReviewItem,
        ScheduleBlock,
        StudySession,
        UserStreak,
    )
    from src.shared.database.session import connect_db, get_session_factory

    await connect_db()
    engine = get_session_factory().kw["bind"]

    # Order does not matter for adding constraints, but keep referenced tables' own PKs added first.
    models = [ReviewItem, UserStreak, StudySession, ScheduleBlock]

    try:
        async with engine.begin() as conn:
            for model in models:
                t = model.__table__
                name = t.name
                has_pk = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conrelid = to_regclass(:r) AND contype = 'p'"
                    ),
                    {"r": f'public."{name}"'},
                )
                if has_pk:
                    print(f"  {name}: already has a primary key — skipping")
                    continue

                pk_cols = ", ".join(f'"{c.name}"' for c in t.primary_key.columns)
                await conn.execute(
                    text(
                        f'ALTER TABLE "{name}" ADD CONSTRAINT "{name}_pkey" PRIMARY KEY ({pk_cols})'
                    )
                )

                for idx in t.indexes:
                    await conn.execute(CreateIndex(idx))

                fk_count = 0
                for fk in t.foreign_key_constraints:
                    local_col = list(fk.columns)[0].name
                    element = list(fk.elements)[0]
                    target_table = element.column.table.name
                    target_col = element.column.name
                    ondelete = (fk.ondelete or "").upper()
                    # For SET NULL links, null any orphan so the constraint cannot fail on legacy
                    # dangling references. For CASCADE links (e.g. userId) orphans should not exist
                    # (all users migrated); if one did, ADD CONSTRAINT would surface it rather than
                    # hide it, which is correct.
                    if ondelete == "SET NULL":
                        await conn.execute(
                            text(
                                f'UPDATE "{name}" SET "{local_col}" = NULL '
                                f'WHERE "{local_col}" IS NOT NULL AND NOT EXISTS '
                                f'(SELECT 1 FROM "{target_table}" x WHERE x."{target_col}" = "{name}"."{local_col}")'
                            )
                        )
                    await conn.execute(AddConstraint(fk))
                    fk_count += 1

                print(f"  {name}: added PK + {len(t.indexes)} indexes + {fk_count} FKs")

            print("\n--- verify (constraint/index counts now present) ---")
            for model in models:
                name = model.__table__.name
                pk = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint WHERE conrelid=to_regclass(:r) AND contype='p'"
                    ),
                    {"r": f'public."{name}"'},
                )
                fk = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint WHERE conrelid=to_regclass(:r) AND contype='f'"
                    ),
                    {"r": f'public."{name}"'},
                )
                ix = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename=:t"
                    ),
                    {"t": name},
                )
                print(f"  {name:14s} PK={pk} FKs={fk} indexes={ix}")

            if not commit:
                raise _DryRun()
        print("\nCOMMITTED ✅")
    except _DryRun:
        print("\nrolled back (dry run) — re-run with --commit to apply")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
