"""Which rungs of the goal ladder have ever fired, and on what.

Read-only. Written to answer a specific question about **phase 8**: it wants to correlate which intervention
actually helped. Three actions times two triggers is six combinations, and if only one has ever fired there is
nothing to compare — no number of learner answers would create the contrast. So the blocker may not be answers
at all.

## Two derived fields, derived here the way the app derives them

`Goal` carries **neither** of these as a column, and selecting them fails:

  - **`dateAuthority`** — `goal_metrics.date_authority()` reads it from `prepId`: a goal attached to a
    preparation has a date it cannot move.
  - **`extendedCount`** — counted from `GoalScheduleChange` rows where `newDate > previousDate`. The
    *system* count filters to `reason = 'system_extended'`, and the two must not be conflated: the wire
    field includes the learner's own edits, while the ladder's extension budget counts only its own.

    python scripts/db_direct.py python scripts/debug/check_ladder_variance.py
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


def _url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


COMBOS = """
    SELECT action, trigger, count(*) AS fired, count("learnerResponse") AS answered
    FROM "GoalLifecycleAction"
    GROUP BY action, trigger
    ORDER BY fired DESC
"""

#: The goals the ladder has acted on, with the two derived fields it decides from.
ACTED_ON = """
    SELECT g.title,
           CASE WHEN g."prepId" IS NOT NULL THEN 'external' ELSE 'learner' END AS authority,
           g.status,
           g."targetDate" AS due,
           round(g.progress) AS prog,
           coalesce(x.system_extended, 0) AS system_extended
    FROM "Goal" g
    JOIN (SELECT DISTINCT "goalId" FROM "GoalLifecycleAction") a ON a."goalId" = g.id
    LEFT JOIN (
        SELECT "goalId", count(*) AS system_extended
        FROM "GoalScheduleChange"
        WHERE "newDate" > "previousDate" AND reason = 'system_extended'
        GROUP BY "goalId"
    ) x ON x."goalId" = g.id
    ORDER BY g."targetDate"
"""

#: Goals the ladder could act on in future — the population that would supply variance.
POPULATION = """
    SELECT CASE WHEN g."prepId" IS NOT NULL THEN 'external' ELSE 'learner' END AS authority,
           count(*) AS goals,
           sum(CASE WHEN g."targetDate" < now()::timestamp THEN 1 ELSE 0 END) AS overdue,
           sum(CASE WHEN coalesce(x.system_extended, 0) >= 3 THEN 1 ELSE 0 END) AS budget_spent
    FROM "Goal" g
    LEFT JOIN (
        SELECT "goalId", count(*) AS system_extended
        FROM "GoalScheduleChange"
        WHERE "newDate" > "previousDate" AND reason = 'system_extended'
        GROUP BY "goalId"
    ) x ON x."goalId" = g.id
    WHERE g.status = 'ACTIVE' AND g."targetDate" IS NOT NULL
    GROUP BY authority
"""


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)

    async with engine.connect() as conn:
        print("--- ladder combinations that have fired (3 actions x 2 triggers = 6 possible) ---")
        combos = (await conn.execute(text(COMBOS))).all()
        for row in combos:
            print(
                f"  {row.action:<18} {row.trigger:<18} fired={row.fired:<4} answered={row.answered}"
            )
        print(f"\n  {len(combos)} of 6 combinations have ever fired.")
        if len(combos) <= 1:
            print(
                "  **This, not the answer count, is phase 8's blocker.** It correlates which rung helped;\n"
                "  with one rung there is nothing to correlate, and answers would not create the contrast."
            )
        print()

        print("--- the goals it acted on ---")
        for row in (await conn.execute(text(ACTED_ON))).all():
            due = row.due.strftime("%Y-%m-%d") if row.due else "none"
            print(
                f"  authority={row.authority:<9} due={due}  progress={row.prog:>3}%"
                f"  system-extended={row.system_extended}  {row.status:<9}  {row.title[:40]}"
            )
        print()

        print("--- the population that would supply future variance ---")
        for row in (await conn.execute(text(POPULATION))).all():
            print(
                f"  authority={row.authority:<9} active with a deadline={row.goals:<4}"
                f" overdue={row.overdue:<4} extension budget spent={row.budget_spent}"
            )
        print(
            "\n  A `learner` goal not yet overdue and with budget left is what produces `extended`;\n"
            "  an `external` one produces `warned`. Neither has fired, so both are untested paths."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
