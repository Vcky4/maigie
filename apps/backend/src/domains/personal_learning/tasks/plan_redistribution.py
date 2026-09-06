"""Repack the study plans of learners who have gone quiet.

**Rescheduling used to require the learner to be active, which is backwards.**
`study_plan_service._redistribute_plan` could only be reached two ways: the learner editing the plan's
schedule inputs, or the learner marking an item complete with more than two pending items already past
due. Both need them to open the app and do something. So the plans that had drifted furthest — belonging
to the learners who had stopped completing anything — were exactly the ones nothing ever rescheduled.

This task is the missing trigger. It decides nothing: the drift threshold and the placement arithmetic are
the service's, shared with the path a learner triggers, so a plan repacked overnight lands where it would
have landed had they ticked something off themselves.

Runs at 05:00, an hour before `learning.prepare_daily_plan`. That order matters: the daily plan reads what
is scheduled for today, so redistributing first means the learner's morning is built from the corrected
schedule rather than from dates that were about to change.

Schedule: daily at 05:00 UTC | Queue: default
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.redistribute_drifted_plans",
    queue="default",
    max_retries=2,
    time_limit=900,
    soft_time_limit=840,
)
def redistribute_drifted_plans():
    """Repack every active plan that has drifted and is off cooldown."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


async def _run() -> int:
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.services import study_plan_service

    redistributed = await study_plan_service.redistribute_drifted_plans()
    logger.info("Drifted study plans redistributed: %d", redistributed)
    return redistributed
