"""Verify migration `018`: two nullable columns, and nothing else moved.

`018` adds `QuizSession.generationMs` and `StudyPlan.strategy`. It moves no data, so
the assertion that matters is that **row counts are identical on both sides** and both
columns arrive nullable with no default — a default would invent a duration nobody
measured and a strategy nobody used.

Run before and after, and diff the output:

    poetry run python scripts/db_direct.py python scripts/check_prep_018.py

Read-only.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every table `018` could plausibly disturb, plus the two it touches.
COUNTED_TABLES = (
    "QuizSession",
    "QuizAnswer",
    "QuizSessionQuestion",
    "StudyPlan",
    "StudyPlanItem",
    "ExamPrep",
    "PrepTopic",
    "PrepQuestion",
    "PrepMaterial",
    "PrepReadinessSnapshot",
    "PracticeObservation",
)

NEW_COLUMNS = (
    ("QuizSession", "generationMs"),
    ("StudyPlan", "strategy"),
)


async def main() -> None:
    from sqlalchemy import text

    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            version = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            print(f"alembic version : {version}")
            print()

            print("columns")
            print("-" * 62)
            for table, column in NEW_COLUMNS:
                row = (
                    await session.execute(
                        text(
                            "SELECT data_type, is_nullable, column_default "
                            "FROM information_schema.columns "
                            "WHERE table_name = :t AND column_name = :c"
                        ),
                        {"t": table, "c": column},
                    )
                ).first()
                if row is None:
                    print(f"  {table}.{column:<16} ABSENT")
                    continue
                data_type, nullable, default = row
                verdict = "ok" if nullable == "YES" and default is None else "UNEXPECTED"
                print(
                    f"  {table}.{column:<16} {data_type:<10} "
                    f"nullable={nullable:<4} default={default!s:<6} {verdict}"
                )
            print()

            print("row counts")
            print("-" * 62)
            for table in COUNTED_TABLES:
                count = (
                    await session.execute(text(f'SELECT count(*) FROM "{table}"'))
                ).scalar_one()
                print(f"  {table:<26} {count:>8}")
            print()

            # Both columns must be entirely NULL immediately after the migration.
            # Anything else would mean a backfill ran, which `018` deliberately does
            # not do: `0` ms reads as instantaneous, and asserting `EVEN` on plans
            # nobody measured is a claim about history rather than a record of it.
            print("backfill check (both should be 0)")
            print("-" * 62)
            for table, column in NEW_COLUMNS:
                try:
                    filled = (
                        await session.execute(
                            text(f'SELECT count(*) FROM "{table}" WHERE "{column}" IS NOT NULL')
                        )
                    ).scalar_one()
                except Exception:
                    print(f"  {table}.{column:<16} column not present yet")
                    continue
                print(f"  {table}.{column:<16} {filled:>8} non-null")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
