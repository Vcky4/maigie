"""Celery task: Generate fresh discovery recommendations.

Schedule: Daily at 03:00 UTC | Queue: heavy

Uses paginated batch processing to avoid loading all users into memory.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)

#: Decision M's dormancy stop. No proactive generation for a learner with nothing in the preceding
#: seven days.
#:
#: The fan-out used to be every profile that existed, which meant this task generated tonight's
#: recommendation for someone who last opened the app in March — $0.64/month each, on learners who
#: would never see it. **That is not proactive, it is a standing order nobody placed.**
#:
#: Seven days rather than one or thirty because it has to survive an ordinary gap. A learner who
#: studies at weekends is not dormant on a Wednesday, and a month is long enough that the spend is
#: back to being untargeted.
PROACTIVE_ACTIVITY_WINDOW_DAYS = 7


def _dormancy_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(days=PROACTIVE_ACTIVITY_WINDOW_DAYS)


_BATCH_SIZE = 50


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
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import discovery_service

    logger.info("Recommendations generation task started")

    generated = 0
    total = 0
    skip = 0

    while True:
        profiles = await repo.list_active_profiles(
            skip=skip, take=_BATCH_SIZE, active_since=_dormancy_cutoff()
        )
        if not profiles:
            break

        total += len(profiles)

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

        skip += _BATCH_SIZE
        if len(profiles) < _BATCH_SIZE:
            break

    logger.info(f"Recommendations generated for {generated}/{total} learner(s)")
