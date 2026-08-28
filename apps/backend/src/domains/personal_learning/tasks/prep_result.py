"""Celery task: ask for the mark on an exam already reviewed.

Schedule: Daily at 01:30 UTC | Queue: default

**The one thing standing between the review and readiness calibration.** The post-exam review collects how the
exam felt straight away, which is right — a mark arrives weeks later, and demanding one up front would collect
neither. So `resultValue` is a separate optional write that both clients render a field for, and until this
task nothing ever pointed at it.

01:30 rather than 01:00: `mark_completed_preparations` owns 01:00 and this reads rows that sweep may have just
created. Nothing breaks if they overlap — a preparation moved to `AWAITING_REVIEW` tonight has no outcome yet,
so it is not in this query at all — but sequencing them costs nothing and keeps the logs legible.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.remind_prep_results",
    queue="default",
    max_retries=3,
    time_limit=120,
    soft_time_limit=90,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def remind_prep_results():
    """Ask, at most twice, for a mark the learner may or may not have yet."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_remind_async())
    finally:
        loop.close()


async def _remind_async():
    from src.shared.database.session import ensure_db

    await ensure_db()

    from src.domains.personal_learning.services import prep_outcome_service

    logger.info("Exam result reminder sweep started")
    asked = await prep_outcome_service.remind_about_missing_results()
    logger.info("Asked %d learner(s) for an exam result", asked)
