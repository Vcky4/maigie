"""Which preparations are `COMPLETED`, and did anyone actually answer for them?

Written to settle one report: a preparation that had been moved to `AWAITING_REVIEW` showed as
`COMPLETED` in the library without any review being submitted. `COMPLETED` is supposed to be
reachable only through `submit_prep_outcome`, so a completed preparation with **no `PrepOutcome`
row** is either a second writer or a learner-facing control that should not exist in that state.

Read-only.

    python scripts/db_direct.py python scripts/debug/check_prep_completions.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def _url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT p.id,
                           p.subject,
                           p.status,
                           p."examDate",
                           p."createdAt",
                           p."updatedAt",
                           u.email,
                           (SELECT count(*) FROM "PrepOutcome" o WHERE o."prepId" = p.id) AS outcomes
                    FROM "ExamPrep" p
                    JOIN "User" u ON u.id = p."userId"
                    WHERE p.status IN ('COMPLETED', 'AWAITING_REVIEW')
                    ORDER BY p."updatedAt" DESC
                    """
                )
            )
        ).all()

        print(f"--- {len(rows)} preparation(s) completed or awaiting review ---\n")
        orphans = 0
        for row in rows:
            flag = ""
            if row.status == "COMPLETED" and row.outcomes == 0:
                flag = "   <-- COMPLETED WITH NO ANSWER"
                orphans += 1
            print(
                f"  {row.status:<16} outcomes={row.outcomes}"
                f"  exam={row.examDate:%Y-%m-%d}"
                f"  updated={row.updatedAt:%Y-%m-%d %H:%M}"
                f"  {row.subject!r} ({row.email}){flag}"
            )

        print(f"\n{orphans} completed without an answer.\n")

        outcomes = (
            await conn.execute(
                text(
                    """
                    SELECT o.attended, o."answeredAt", o."examDate", p.subject
                    FROM "PrepOutcome" o
                    JOIN "ExamPrep" p ON p.id = o."prepId"
                    ORDER BY o."answeredAt" DESC
                    """
                )
            )
        ).all()
        print(f"--- {len(outcomes)} PrepOutcome row(s) in the whole database ---")
        for row in outcomes:
            print(
                f"  {row.attended:<10} answered={row.answeredAt:%Y-%m-%d %H:%M}  {row.subject!r}"
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
