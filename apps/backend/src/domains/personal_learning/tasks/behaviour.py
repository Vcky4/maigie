"""Celery task: Analyze learner behaviour patterns.

Schedule: Daily at 02:00 UTC | Queue: heavy
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.analyze_behaviour",
    queue="heavy",
    max_retries=3,
    time_limit=300,
    soft_time_limit=240,
)
def analyze_behaviour():
    """
    For each active learner, compute behaviour metrics and update
    LearningProfile cache. Detect dropout risk.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_analyze_behaviour_async())
    finally:
        loop.close()


async def _analyze_behaviour_async():
    from src.domains.personal_learning.services import behaviour_service
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    logger.info("Behaviour analysis task started")

    profiles = await repo.list_active_profiles()
    updated = 0

    for profile in profiles:
        user_id = profile.user_id
        try:
            # Recompute behaviour analytics and persist to profile
            await behaviour_service.analyze_behaviour(user_id)

            # Also increment maturity_days
            await repo.increment_maturity_days(user_id)

            updated += 1
        except Exception:
            logger.exception(f"Failed to analyze behaviour for user {user_id}")

    logger.info(f"Behaviour analysis completed: updated {updated}/{len(profiles)} profile(s)")
