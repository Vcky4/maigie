"""Would the global nudge prompt actually see a learner's pending nudge?

Read-only. The prompt reads **one page** of `ACTIVE` goals — `pageSize: 20` on both clients — and shows the
first with a `pendingNudge`. That is fine for a learner with a handful of goals and silently wrong for a
learner with more than a page of them: a nudged goal sorted past position 20 is invisible to a prompt that
never looks at page two.

This reports, per learner with an unanswered nudge: how many active goals they have, where the nudged ones
fall in the list's own default order, and therefore whether the prompt can see them.

    python scripts/db_direct.py python scripts/debug/check_nudge_visibility.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

#: What both clients' prompts ask for.
PROMPT_PAGE_SIZE = 20


def _url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


LEARNERS = """
    SELECT DISTINCT u.id, u.email
    FROM "GoalLifecycleAction" a
    JOIN "User" u ON u.id = a."userId"
    WHERE a."learnerResponse" IS NULL
"""

#: Ordered the way `GET /progress/goals` orders by default, so `position` is the real one.
GOALS = """
    SELECT g.id, g.title, g.status,
           row_number() OVER (ORDER BY g."createdAt" DESC) AS position,
           n.action AS pending
    FROM "Goal" g
    LEFT JOIN (
        SELECT a."goalId", a.action
        FROM "GoalLifecycleAction" a
        JOIN (
            SELECT "goalId", max("createdAt") AS newest
            FROM "GoalLifecycleAction" GROUP BY "goalId"
        ) latest ON latest."goalId" = a."goalId" AND latest.newest = a."createdAt"
        WHERE a."learnerResponse" IS NULL
    ) n ON n."goalId" = g.id
    WHERE g."userId" = :uid AND g.status = 'ACTIVE'
"""


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)

    async with engine.connect() as conn:
        learners = (await conn.execute(text(LEARNERS))).all()
        if not learners:
            print("No learner has an unanswered nudge.")
            await engine.dispose()
            return

        for learner in learners:
            rows = (await conn.execute(text(GOALS), {"uid": learner.id})).all()
            active = len(rows)
            nudged = [r for r in rows if r.pending]
            visible = [r for r in nudged if r.position <= PROMPT_PAGE_SIZE]

            print(f"--- {learner.email} ---")
            print(f"  active goals: {active}   prompt reads the first {PROMPT_PAGE_SIZE}")
            print(f"  goals with a pending nudge: {len(nudged)}")
            for row in nudged:
                mark = "visible" if row.position <= PROMPT_PAGE_SIZE else "PAST THE PAGE"
                print(
                    f"    position {row.position:>3}  {mark:<14} {row.pending:<18} {row.title[:44]}"
                )
            if nudged and not visible:
                print(
                    "  >>> The prompt cannot see any of them. Every nudged goal sorts past the page it\n"
                    "      reads, so the learner is never asked no matter how long they use the app."
                )
            elif nudged:
                print(
                    f"  the prompt would show: position {visible[0].position}, {visible[0].title[:46]}"
                )
            print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
