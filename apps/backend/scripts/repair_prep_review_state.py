"""Restore preparations that were completed without an answer back to `AWAITING_REVIEW`.

## Why this exists

A stale deployment sharing this database ran the pre-review version of
`learning.mark_completed_preparations` at 01:00 UTC and set `status = COMPLETED` on every preparation whose
exam date had passed — including the four the new review sweep had asked about seven hours earlier. See
§10.10 of `docs/implementation/adaptive-goal-lifecycle-plan.md`.

**Those rows do not heal on their own.** `list_preps_awaiting_review` excludes `COMPLETED`, so a
preparation completed in this state is terminal: it will never be asked about again, and the one datum the
whole review flow exists to collect is permanently absent.

## What it repairs, and what it deliberately does not

Only preparations that are **all** of:

  - `status = 'COMPLETED'`,
  - with **no `PrepOutcome` row** — nobody answered, so nothing is being overwritten,
  - whose `examDate` has **passed** — a preparation completed early was a legitimate learner decision,
  - and which carry a **`reviewAskedAt`** — proof the new sweep had already moved this row into review and
    asked. This is the narrow condition, and it is the point: it repairs only rows we can *show* were taken
    out of `AWAITING_REVIEW` by something else.

The last clause is why this is safe to run rather than a judgement call. The other 18 completed-without-an-
answer preparations in this database were never asked about — they were completed by the old sweep before
the review flow existed — so restoring them would put a question in front of learners about exams from
February that they have no reason to remember. Those are history, and history is not repaired by asking
about it. `--include-unasked` exists to override that, and prints a warning, because the choice belongs to
a person and not to this file.

`reviewAskedAt` and `reviewRemindersSent` are **left exactly as they are**. They record that the learner was
asked, which is true and already happened; resetting them would spend the reminder budget a second time on
someone who has already had the notification.

## Usage

Dry run by default — it prints what it would do and writes nothing:

    python scripts/db_direct.py python scripts/repair_prep_review_state.py

Apply:

    python scripts/db_direct.py python scripts/repair_prep_review_state.py --apply

**Redeploy first.** Whatever this restores tonight is completed again at 01:00 UTC by the stale worker.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

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


# `ExamPrep."examDate"` is `timestamp without time zone` in this database despite the ORM declaring
# otherwise, so asyncpg refuses an aware bind parameter against it. Compared naive-as-UTC, the same
# convention the read side applies.
CANDIDATES = """
    SELECT p.id,
           p.subject,
           p."examDate",
           p."reviewAskedAt",
           p."reviewRemindersSent",
           p."updatedAt",
           u.email
    FROM "ExamPrep" p
    JOIN "User" u ON u.id = p."userId"
    WHERE p.status = 'COMPLETED'
      AND p."examDate" < :now
      AND NOT EXISTS (SELECT 1 FROM "PrepOutcome" o WHERE o."prepId" = p.id)
      {asked_clause}
    ORDER BY p."examDate" DESC
"""

RESTORE = """
    UPDATE "ExamPrep"
       SET status = 'AWAITING_REVIEW',
           "updatedAt" = :now
     WHERE id = ANY(:ids)
       AND status = 'COMPLETED'
       AND NOT EXISTS (SELECT 1 FROM "PrepOutcome" o WHERE o."prepId" = "ExamPrep".id)
"""


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Write the change. Without it, nothing is written."
    )
    parser.add_argument(
        "--include-unasked",
        action="store_true",
        help=(
            "Also restore preparations with no reviewAskedAt — completed by the old sweep before the "
            "review flow existed. Asks learners about exams they may have long forgotten."
        ),
    )
    args = parser.parse_args()

    engine = create_async_engine(_url(), echo=False)
    now = datetime.now(UTC)
    naive_now = now.replace(tzinfo=None)

    asked_clause = "" if args.include_unasked else 'AND p."reviewAskedAt" IS NOT NULL'
    query = CANDIDATES.format(asked_clause=asked_clause)

    async with engine.begin() as conn:
        rows = (await conn.execute(text(query), {"now": naive_now})).all()

        if args.include_unasked:
            print(
                "WARNING: --include-unasked will ask learners about exams the review flow never "
                "asked about, some of them months old.\n"
            )

        if not rows:
            print("Nothing to repair: no preparation is completed, unanswered and previously asked.")
            await engine.dispose()
            return

        print(f"--- {len(rows)} preparation(s) to restore to AWAITING_REVIEW ---")
        for row in rows:
            asked = (
                f"{row.reviewAskedAt:%Y-%m-%d %H:%M}" if row.reviewAskedAt else "never"
            )
            print(
                f"  exam={row.examDate:%Y-%m-%d}"
                f"  asked={asked:<16}"
                f"  reminders={row.reviewRemindersSent}"
                f"  wrongly completed={row.updatedAt:%Y-%m-%d %H:%M}"
                f"  {row.subject!r} ({row.email})"
            )

        if not args.apply:
            print("\nDry run. Nothing written. Re-run with --apply to make the change.")
            await engine.dispose()
            return

        result = await conn.execute(
            text(RESTORE), {"ids": [row.id for row in rows], "now": naive_now}
        )
        print(f"\nRestored {result.rowcount} preparation(s) to AWAITING_REVIEW.")
        print(
            "reviewAskedAt and reviewRemindersSent left untouched — the learner was asked, and that "
            "is still true."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
