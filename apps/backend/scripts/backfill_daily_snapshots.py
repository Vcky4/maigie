"""Reconstruct 90 days of `DailyLearningSnapshot` rows.

Migration 039 creates the table empty, so every Reflect trend starts with no history and every
growth curve is a single point. Most of that history is recoverable, which the initial audit had
assumed it was not: `focusedMinutes`, `sessionsCompleted`, `activeDay`, `cardsReviewed`,
`recallPercent`, `topicsCompleted` and `effortScore` all reduce to event rows with timestamps,
and consistency is a pure function of the session set, so each of those recomputes **exactly**
for any past day.

Mastery is the one approximation, and it is worth stating precisely rather than glossing.
`overallMasteryPercent` and `subjectMastery` are rebuilt from `Topic.completedAt`, which means
the denominator is *today's* topic count, so topics added since make earlier progress look
smaller; and a topic completed then later reopened has had its `completedAt` cleared, so it
reads as never completed. Both distortions push the same way — reconstructed mastery understates
the past — so a rebuilt trend never invents growth that did not happen. Those rows are flagged
`reconstructed` and the client footnotes them (Decision P).

Why a script rather than part of migration 039: ninety days times every learner is a long job
that can be interrupted and needs to be observable and resumable. A migration offers no dry run
and no way to do half of it and continue.

**Idempotent, and safe to re-run.** Days that already have a row are skipped, not rewritten — a
row the nightly task wrote measured mastery against the topic count as it stood that day, which
is exact, and letting a reconstruction overwrite it would downgrade a real measurement to an
estimate.

A learner can legitimately come back with rows full of nulls. If nothing was recorded for a day,
that is the true answer for that day.

Usage::

    python scripts/backfill_daily_snapshots.py                       # dry run, report only
    python scripts/backfill_daily_snapshots.py --apply
    python scripts/backfill_daily_snapshots.py --apply --user-id abc123
    python scripts/backfill_daily_snapshots.py --apply --days 30
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domains.personal_learning.repository import (  # noqa: E402
    personal_learning_repo as repo,
)
from src.domains.personal_learning.services import (  # noqa: E402
    daily_snapshot_service as snapshots,
)
from src.shared.database.session import connect_db, disconnect_db  # noqa: E402

_BATCH_SIZE = 100


async def _iter_user_ids(user_id: str | None) -> list[str]:
    if user_id:
        return [user_id]

    user_ids: list[str] = []
    skip = 0
    while True:
        profiles = await repo.list_active_profiles(skip=skip, take=_BATCH_SIZE)
        if not profiles:
            break
        user_ids.extend(profile.user_id for profile in profiles)
        if len(profiles) < _BATCH_SIZE:
            break
        skip += _BATCH_SIZE
    return user_ids


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write rows (default is a dry run)")
    parser.add_argument("--user-id", help="restrict to one learner")
    parser.add_argument(
        "--days",
        type=int,
        default=snapshots.BACKFILL_DAYS,
        help=f"days of history to reconstruct (default {snapshots.BACKFILL_DAYS})",
    )
    args = parser.parse_args()

    await connect_db()
    try:
        user_ids = await _iter_user_ids(args.user_id)
        print(f"learners:           {len(user_ids)}")
        print(f"days per learner:   {args.days}")
        print()

        if not args.apply:
            # A dry run reports what is missing without writing, by asking each learner's
            # window what it already holds. Cheap, and it is the only way to see the size of
            # the job before committing to it.
            total_missing = 0
            for user_id in user_ids:
                existing = await repo.list_daily_snapshots(user_id, since=_earliest_day(args.days))
                missing = max(0, args.days - len(existing))
                total_missing += missing
                if missing:
                    print(f"  {user_id[:8]}…: {missing} day(s) missing")
            print()
            print(f"would write roughly {total_missing} row(s)")
            print("Dry run. Re-run with --apply to write.")
            return 0

        written = 0
        failed: list[tuple[str, str]] = []
        for user_id in user_ids:
            try:
                rows = await snapshots.backfill_for_user(user_id=user_id, days=args.days)
                written += rows
                print(f"  {user_id[:8]}…: {rows} row(s)")
            except Exception as exc:  # noqa: BLE001 - one learner must not stop the run
                failed.append((user_id, f"{type(exc).__name__}: {exc}"))
                print(f"  {user_id[:8]}…: FAILED — {type(exc).__name__}: {exc}")

        print()
        print(f"rows written: {written}")
        print(f"failed:       {len(failed)}")
        for user_id, reason in failed:
            print(f"  {user_id}: {reason}")
        return 1 if failed else 0
    finally:
        await disconnect_db()


def _earliest_day(days: int):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).date()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
