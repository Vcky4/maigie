"""Celery task: move preparations whose exam has passed into review.

Schedule: Daily at 01:00 UTC | Queue: default

**This task used to mark them `COMPLETED`.** It set that status on every preparation whose `examDate` had
passed, which asserted an outcome nobody had recorded — a learner 30 percent ready for an exam they missed
got a preparation recorded as finished. A clock is not an outcome. The task now moves them to
`AWAITING_REVIEW` and asks the learner how it went; only their answer completes the preparation. See
`prep_outcome_service`.

The task name is unchanged (`learning.mark_completed_preparations`) because it is referenced by the beat
schedule and renaming a registered task means a deploy where the beat entry points at a name no worker
answers to. The function it calls carries the accurate name.
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
    """Find preparations past their exam date and put them in front of the learner."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_mark_awaiting_review_async())
    finally:
        loop.close()


async def _mark_awaiting_review_async():
    from src.shared.database.session import ensure_db

    await ensure_db()

    from src.domains.personal_learning.services import exam_prep_service

    logger.info("Preparation review sweep started")
    count = await exam_prep_service.mark_preparations_awaiting_review()
    logger.info("Moved %d preparation(s) into review", count)
