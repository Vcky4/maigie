"""Weekly study-plan check-in notifications.

Backs the create wizard's "Weekly Maigie check-in" option, which until now set a toggle
that nothing read.

Runs daily rather than weekly on purpose. The work is "which plans have not had a
check-in in seven days", answered from `StudyPlan.lastCheckInAt`, so a daily sweep gives
each learner a check-in roughly seven days after their own last one instead of herding
everybody onto one fixed morning. It also means a day the scheduler missed is picked up
the next day rather than waiting a full week.

Schedule: daily at 07:00 UTC | Queue: default
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.study_plan_check_ins",
    queue="default",
    max_retries=2,
    time_limit=600,
    soft_time_limit=540,
)
def study_plan_check_ins():
    """Create this week's check-in for each plan that asked for one and is due."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


async def _run() -> int:
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.services import study_plan_service

    sent = await study_plan_service.run_weekly_check_ins()
    logger.info("Study plan check-ins sent: %d", sent)
    return sent
