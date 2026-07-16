"""Celery task: Generate fresh discovery recommendations.

Schedule: Daily at 03:00 UTC | Queue: heavy
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.generate_recommendations",
    queue="heavy",
    max_retries=3,
    time_limit=300,
    soft_time_limit=240,
)
def generate_recommendations():
    """
    For each learner, compute fresh discovery recommendations
    based on goals, recent activity, and knowledge gaps.
    Store in DiscoveryRecommendation table.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_generate_recommendations_async())
    finally:
        loop.close()


async def _generate_recommendations_async():
    from src.domains.personal_learning.services import discovery_service
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    logger.info("Recommendations generation task started")

    profiles = await repo.list_active_profiles()
    generated = 0

    for profile in profiles:
        user_id = profile.user_id
        try:
            # Clean up old dismissed/expired recommendations
            await repo.delete_old_recommendations(user_id)

            # Generate fresh recommendations using discovery service
            await discovery_service.generate_recommendations(user_id=user_id)
            generated += 1
        except Exception:
            logger.exception(f"Failed to generate recommendations for user {user_id}")

    logger.info(f"Recommendations generated for {generated}/{len(profiles)} learner(s)")
