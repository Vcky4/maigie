"""Celery task: Mark overdue preparations as completed.

Schedule: Daily at 01:00 UTC | Queue: default
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.mark_completed_preparations",
    queue="default",
    max_retries=3,
    time_limit=120,
    soft_time_limit=90,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def mark_completed_preparations():
    """
    Find preparations past target_date without manual completion.
    Mark as COMPLETED with retry on failure.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_mark_completed_async())
    finally:
        loop.close()


async def _mark_completed_async():
    from src.domains.personal_learning.services import exam_prep_service

    logger.info("Mark completed preparations task started")
    count = await exam_prep_service.mark_overdue_preparations_completed()
    logger.info(f"Marked {count} overdue preparation(s) as completed")
