"""Which learner would actually see a prompt, and why the others would not.

Read-only. `check_goal_nudge_state.py` answers "what would the sweep do" across the whole database, which
is the wrong question when a prompt fails to appear in a browser — that is always a question about **one
signed-in learner**.

    python scripts/db_direct.py python scripts/check_goal_nudge_by_user.py [email]

Prints, per learner: their active dated goals, the authority of each, and whether anything is waiting on
an answer. The two reasons a prompt does not appear for a learner who plainly has an overdue goal:

- **The deadline is external.** A goal linked to an exam preparation takes its date from the exam, and an
  external deadline that has passed is deliberately left alone — `prep_outcome_service` has already asked
  how the exam went, and asking again from the goal surface would be the same question twice in different
  words. That is `goal_lifecycle_service._act_on` returning `None`.
- **There is no deadline at all.** `is_at_risk` never fires without one, so a course goal with no target
  date is never behind.
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
    wanted_email = sys.argv[1] if len(sys.argv) > 1 else None
    engine = create_async_engine(_url(), echo=False)
    now = datetime.now(UTC)
    naive_now = now.replace(tzinfo=None)

    async with engine.connect() as conn:
        users = (
            await conn.execute(
                text(
                    'SELECT u.id, u.email, count(g.id) FROM "User" u '
                    'JOIN "Goal" g ON g."userId" = u.id AND g.status = \'ACTIVE\' '
                    "GROUP BY u.id, u.email ORDER BY count(g.id) DESC"
                )
            )
        ).all()

        for user_id, email, active in users:
            if wanted_email and email != wanted_email:
                continue
            print(f"\n=== {email} ({active} active goals) ===")

            goals = (
                await conn.execute(
                    text(
                        'SELECT id, title, "targetDate", "prepId", "metricKind" FROM "Goal" '
                        "WHERE \"userId\" = :u AND status = 'ACTIVE' ORDER BY \"targetDate\" ASC NULLS LAST"
                    ),
                    {"u": user_id},
                )
            ).all()

            for gid, title, target, prep_id, kind in goals:
                authority = "external" if prep_id else "learner"
                if target is None:
                    verdict = "no deadline -> never behind, never prompted"
                elif target < naive_now:
                    verdict = (
                        "OVERDUE but external -> left to the post-exam review, by design"
                        if authority == "external"
                        else "OVERDUE and the learner's own -> the ladder acts on this one"
                    )
                elif target < naive_now + timedelta(days=7):
                    verdict = "due within 7 days -> considered if progress is behind"
                else:
                    verdict = "deadline beyond 7 days -> outside the horizon"

                pending = (
                    await conn.execute(
                        text(
                            'SELECT action FROM "GoalLifecycleAction" WHERE "goalId" = :g '
                            'AND "learnerResponse" IS NULL ORDER BY "createdAt" DESC LIMIT 1'
                        ),
                        {"g": gid},
                    )
                ).scalar()

                when = target.strftime("%Y-%m-%d") if target else "none"
                print(f"  {title!r}")
                print(f"    deadline={when} authority={authority} kind={kind}")
                print(f"    {verdict}")
                if pending:
                    print(f"    *** pendingNudge={pending!r} — this goal WOULD show the prompt")

            preps = (
                await conn.execute(
                    text(
                        'SELECT subject, status, "examDate", "reviewDeclinedAt" FROM "ExamPrep" '
                        'WHERE "userId" = :u AND "examDate" < :now ORDER BY "examDate" DESC'
                    ),
                    {"u": user_id, "now": naive_now},
                )
            ).all()
            if preps:
                print("  preparations whose exam has passed:")
                for subject, status, exam_date, declined in preps:
                    if status == "AWAITING_REVIEW" and declined is None:
                        note = "*** WOULD show the review prompt"
                    elif declined is not None:
                        note = "the learner declined, so it will not ask again"
                    elif status == "COMPLETED":
                        # `list_preps_awaiting_review` excludes COMPLETED, and migration 050 deliberately
                        # did not backfill: these were closed by the old clock-driven sweep, and asking
                        # about them now would be asking about exams sat months ago.
                        note = "already COMPLETED — never asked about, by design (no backfill)"
                    else:
                        note = (
                            f"status={status} — the preparations sweep would move this to AWAITING_REVIEW"
                        )
                    print(f"    {subject!r} exam {exam_date:%Y-%m-%d}: {note}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
