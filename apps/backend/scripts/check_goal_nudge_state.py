"""What the goal ladder would do tonight, and whether anything is waiting on an answer.

Read-only. Run before and after `review_goal_lifecycle` to see what changed.

    python scripts/db_direct.py python scripts/check_goal_nudge_state.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def _url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)
    now = datetime.now(UTC)
    # `Goal.targetDate` and `createdAt` are `timestamp without time zone` in this database despite
    # the ORM declaring otherwise, so asyncpg refuses an aware bind parameter against them. Compared
    # naive-as-UTC, which is the same convention `goal_metrics._utc` applies on the read side.
    naive_now = now.replace(tzinfo=None)
    async with engine.connect() as conn:
        print("--- goals the ladder would consider (active, dated, deadline within 7 days or past) ---")
        rows = (
            await conn.execute(
                text(
                    'SELECT id, title, status, "targetDate", "prepId", "metricKind", progress, "createdAt" '
                    'FROM "Goal" WHERE status = \'ACTIVE\' AND "targetDate" IS NOT NULL '
                    'AND "targetDate" < :horizon ORDER BY "targetDate" ASC'
                ),
                {"horizon": naive_now + timedelta(days=7)},
            )
        ).all()
        if not rows:
            print("  none")
        for gid, title, status, target, prep_id, kind, progress, created in rows:
            authority = "external" if prep_id else "learner"
            overdue = "OVERDUE" if target and target.replace(tzinfo=None) < naive_now else "due soon"
            snaps = (
                await conn.execute(
                    text(
                        'SELECT count(*) FROM "GoalProgressSnapshot" WHERE "goalId" = :g '
                        'AND "capturedOn" >= :since'
                    ),
                    {"g": gid, "since": (now - timedelta(days=14)).date()},
                )
            ).scalar()
            print(f"  {title!r}")
            print(
                f"    {overdue} | authority={authority} | kind={kind} | stored progress={progress} "
                f"| snapshots in last 14d={snaps}"
            )
            # An extension needs two snapshots with real gain; without them the ladder asks instead.
            predicted = (
                "nothing (an external deadline that has passed is the post-exam review's job)"
                if authority == "external" and target and target.replace(tzinfo=None) < naive_now
                else "warned (external, due soon)"
                if authority == "external"
                else "extended, or asked_to_confirm if the rate cannot be measured"
            )
            print(f"    predicted action: {predicted}")

        print("\n--- actions already recorded ---")
        acted = (
            await conn.execute(
                text(
                    'SELECT g.title, a.action, a.trigger, a."learnerResponse", a."createdAt" '
                    'FROM "GoalLifecycleAction" a JOIN "Goal" g ON g.id = a."goalId" '
                    'ORDER BY a."createdAt" DESC LIMIT 10'
                )
            )
        ).all()
        if not acted:
            print("  none — nothing has been asked, so no goal has a pendingNudge")
        for title, action, trigger, response, created in acted:
            state = response or "UNANSWERED -> this is what makes pendingNudge non-null"
            print(f"  {title!r}: {action} ({trigger}) {created:%Y-%m-%d %H:%M} | {state}")

        print("\n--- deadline changes recorded ---")
        changes = (
            await conn.execute(
                text(
                    'SELECT g.title, c.reason, c."previousDate", c."newDate" '
                    'FROM "GoalScheduleChange" c JOIN "Goal" g ON g.id = c."goalId" '
                    'ORDER BY c."createdAt" DESC LIMIT 10'
                )
            )
        ).all()
        if not changes:
            print("  none")
        for title, reason, prev, new in changes:
            print(f"  {title!r}: {reason} {prev} -> {new}")

    await engine.dispose()

    # The read path the API actually serves `pendingNudge` from. Checking the rows exist is not the same
    # as checking the response carries them: `latest_unanswered_actions` only counts a goal's **most
    # recent** action, so a superseded nudge is not presented as a live question.
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.progress.repository import progress_repo

    ids = [row[0] for row in rows]
    pending = await progress_repo.latest_unanswered_actions(ids) if ids else {}
    print("\n--- pendingNudge as the goals API would publish it ---")
    if not pending:
        print("  no goal would show a prompt")
    for goal_id, action in pending.items():
        title = next((r[1] for r in rows if r[0] == goal_id), goal_id)
        print(f"  {title!r} -> pendingNudge={action!r}")


if __name__ == "__main__":
    asyncio.run(main())
