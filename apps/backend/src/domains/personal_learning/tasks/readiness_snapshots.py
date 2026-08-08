"""Celery task: capture daily readiness snapshots.

Schedule: daily at 00:30 UTC | Queue: heavy

Runs just after midnight so a day's row reflects that day's finishing state. The
writer is idempotent on ``(prepId, capturedOn)``, so a retry or an overlapping run
updates the day rather than duplicating it.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.capture_readiness_snapshots",
    queue="heavy",
    max_retries=3,
    time_limit=900,
    soft_time_limit=840,
)
def capture_readiness_snapshots():
    """Write one readiness row per unfinished preparation."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_capture_readiness_snapshots_async())
    finally:
        loop.close()


async def _capture_readiness_snapshots_async():
    from src.domains.personal_learning.services import prep_snapshot_service
    from src.shared.database.session import ensure_db

    await ensure_db()

    logger.info("Readiness snapshot task started")
    written, seen = await prep_snapshot_service.capture_all()
    logger.info(f"Readiness snapshots written for {written}/{seen} preparation(s)")
