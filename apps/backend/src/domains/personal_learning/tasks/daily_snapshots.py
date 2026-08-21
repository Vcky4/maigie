"""Celery task: capture one daily learning snapshot per learner.

Schedule: daily at 01:15 UTC | Queue: heavy

Records each learner's most recently **finished** local day, not the day in progress. A single
nightly run on one UTC clock cannot capture "today" for everybody: at 01:15 UTC a learner in
Lagos is already an hour and a quarter into the next day, so storing their "today" would store
an almost empty day and mark it complete. Recording the day that just ended gives every learner
exactly one whole row per day regardless of where they are.

The writer is idempotent on ``(userId, snapshotDate)``, so a retry, an overlapping run, or the
backfill crossing a day already recorded updates the row rather than duplicating the day.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.capture_daily_snapshots",
    queue="heavy",
    max_retries=3,
    time_limit=1800,
    soft_time_limit=1740,
)
def capture_daily_snapshots():
    """Write one snapshot row per learner for their last finished day."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_capture_daily_snapshots_async())
    finally:
        loop.close()


async def _capture_daily_snapshots_async():
    from src.domains.personal_learning.services import daily_snapshot_service
    from src.shared.database.session import ensure_db

    await ensure_db()

    logger.info("Daily learning snapshot task started")
    written, seen = await daily_snapshot_service.capture_all()
    logger.info(f"Daily learning snapshots written for {written}/{seen} learner(s)")
