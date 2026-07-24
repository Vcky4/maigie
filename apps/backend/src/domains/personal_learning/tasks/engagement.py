"""Celery task: Check for declining engagement and send nudges.

Schedule: Every 6 hours | Queue: default

Uses paginated batch processing to avoid loading all users into memory.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50


@celery_app.task(
    name="learning.check_declining_engagement",
    queue="default",
    max_retries=2,
    time_limit=120,
    soft_time_limit=90,
)
def check_declining_engagement():
    """
    Find learners with 3+ consecutive days of declining activity.
    Create a gentle nudge notification (no guilt language) with a low-effort action.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_check_engagement_async())
    finally:
        loop.close()


async def _check_engagement_async():
    from src.domains.personal_learning.services import notification_service
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    logger.info("Engagement check task started")

    nudge_count = 0
    skip = 0

    while True:
        profiles = await repo.list_declining_engagement_profiles(
            min_declining_days=3, skip=skip, take=_BATCH_SIZE
        )
        if not profiles:
            break

        for profile in profiles:
            user_id = profile.user_id
            try:
                await notification_service.create_notification(
                    user_id=user_id,
                    type="ENGAGEMENT_NUDGE",
                    title="Quick review?",
                    body="A 2-minute flashcard session can keep your momentum going.",
                    priority=2,
                    action_data={"type": "navigate", "target": "/flashcards/due"},
                )
                nudge_count += 1
            except Exception:
                logger.exception(f"Failed to send engagement nudge to user {user_id}")

        skip += _BATCH_SIZE
        if len(profiles) < _BATCH_SIZE:
            break

    logger.info(f"Engagement check completed: sent {nudge_count} nudge(s)")
