"""Read-only pre/post-flight for migration 016.

Verifies what the migration should and should not have changed, rather than
trusting alembic's exit code. Run it before and compare the output after.

Connects over the **session-mode pooler (port 5432)**, not transaction mode
(6543): the latter is unreliable for DDL and for prepared statements, and the
direct host publishes only an AAAA record so it is unreachable from here.

    poetry run python scripts/check_prep_016.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Existing environment wins, so the caller can point this at a different database
# without editing .env.
load_dotenv()

# The columns 016 adds, as (table, column).
NEW_COLUMNS = [
    ("ExamPrep", "targetReadiness"),
    ("PrepTopic", "category"),
    ("PrepTopic", "targetMastery"),
    ("PrepReadinessSnapshot", "targetPercent"),
]

# Tables 016 must not touch a single row of. It only adds nullable columns, so
# every count here has to be identical before and after.
ROW_COUNT_TABLES = [
    "ExamPrep",
    "PrepTopic",
    "PrepMaterial",
    "PrepQuestion",
    "QuizSession",
    "QuizAnswer",
    "PrepReadinessSnapshot",
]


def ddl_url() -> str:
    """A DDL-safe URL: direct host if reachable, session-mode pooler otherwise.

    Never transaction mode (6543), which cannot be trusted for DDL or for reading
    ``information_schema`` with prepared statements.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db_direct import resolve

    url, description = resolve()
    print(f"connecting via {description}\n")
    return url


async def main() -> None:
    engine = create_async_engine(ddl_url())
    async with engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        print(f"alembic_version : {version}")

        print("\ncolumns added by 016:")
        for table, column in NEW_COLUMNS:
            exists = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                )
            ).scalar_one()
            print(f"  {table}.{column:<17} {'present' if exists else 'absent'}")

        print("\nrow counts (must be unchanged — 016 moves no data):")
        for table in ROW_COUNT_TABLES:
            try:
                count = (await conn.execute(text(f'SELECT COUNT(*) FROM "{table}"'))).scalar_one()
                print(f"  {table:<22} {count}")
            except Exception as e:  # noqa: BLE001 - a missing table is a finding
                print(f"  {table:<22} unavailable ({type(e).__name__})")

        # Nullable with no server default is the whole point: a default would
        # invent a goal the learner never set.
        print("\nnullability and defaults:")
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, column_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE (table_name, column_name) IN "
                    "(('ExamPrep','targetReadiness'),('PrepTopic','category'),"
                    "('PrepTopic','targetMastery'),"
                    "('PrepReadinessSnapshot','targetPercent')) "
                    "ORDER BY table_name, column_name"
                )
            )
        ).all()
        for table, column, nullable, default in rows:
            print(f"  {table}.{column:<17} nullable={nullable} default={default!r}")
        if not rows:
            print("  (none yet)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
