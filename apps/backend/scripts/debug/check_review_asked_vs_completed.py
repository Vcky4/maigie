"""The preparations the review sweep asked about, and what happened to them afterwards.

Read-only. Written to identify the second writer: if a preparation carries `reviewAskedAt` — meaning
the new sweep moved it to `AWAITING_REVIEW` and asked — but its status is now `COMPLETED` with no
`PrepOutcome` row, then something completed it *after* the ask, without an answer.

    python scripts/db_direct.py python scripts/debug/check_review_asked_vs_completed.py
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


ASKED = """
    SELECT p.subject, p.status, p."examDate", p."reviewAskedAt", p."updatedAt",
           p."reviewRemindersSent", u.email,
           (SELECT count(*) FROM "PrepOutcome" o WHERE o."prepId" = p.id) AS outcomes
    FROM "ExamPrep" p
    JOIN "User" u ON u.id = p."userId"
    WHERE p."reviewAskedAt" IS NOT NULL
    ORDER BY p."updatedAt"
"""

NOTIFICATIONS = """
    SELECT n.title, n."createdAt", n.status, n.priority
    FROM "Notification" n
    WHERE n.type = 'preparation_review'
    ORDER BY n."createdAt" DESC
    LIMIT 12
"""

ACTIVITY = """
    SELECT a."activityType", a.title, a."createdAt"
    FROM "ActivityFeedItem" a
    WHERE a."activityType" = 'preparation_completed'
    ORDER BY a."createdAt" DESC
    LIMIT 12
"""


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)

    async with engine.connect() as conn:
        print("--- preparations the new sweep asked about ---")
        for row in (await conn.execute(text(ASKED))).all():
            flag = (
                "   <-- COMPLETED AFTER BEING ASKED, NO ANSWER"
                if (row.status == "COMPLETED" and row.outcomes == 0)
                else ""
            )
            print(
                f"  {row.status:<16} outcomes={row.outcomes}"
                f" reminders={row.reviewRemindersSent}"
                f" exam={row.examDate:%Y-%m-%d}"
                f" asked={row.reviewAskedAt:%Y-%m-%d %H:%M}"
                f" updated={row.updatedAt:%Y-%m-%d %H:%M}"
                f"  {row.subject!r} ({row.email}){flag}"
            )

        print("\n--- preparation_review notifications ---")
        for row in (await conn.execute(text(NOTIFICATIONS))).all():
            print(
                f"  {row.createdAt:%Y-%m-%d %H:%M}  p{row.priority}  {row.status:<10} {row.title!r}"
            )

        print("\n--- 'preparation_completed' activity, which mark_completed writes ---")
        try:
            rows = (await conn.execute(text(ACTIVITY))).all()
        except Exception as exc:  # table name may differ
            print(f"  (could not read: {type(exc).__name__})")
            rows = []
        for row in rows:
            print(f"  {row.createdAt:%Y-%m-%d %H:%M}  {row.title!r}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
