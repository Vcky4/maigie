"""Celery task: Generate weekly reflections.

Schedule: Weekly Sunday at 04:00 UTC | Queue: heavy
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.generate_reflections",
    queue="heavy",
    max_retries=3,
    time_limit=600,
    soft_time_limit=540,
)
def generate_reflections():
    """
    For each active learner, generate an AI weekly reflection using
    the Three Layer Model. Gracefully degrade if LLM fails (deliver
    without recommendations).
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_generate_reflections_async())
    finally:
        loop.close()


async def _generate_reflections_async():
    from src.domains.personal_learning.services import reflection_service
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    logger.info("Weekly reflections task started")

    profiles = await repo.list_active_profiles()
    generated = 0

    for profile in profiles:
        user_id = profile.user_id
        try:
            await reflection_service.generate_reflection(user_id=user_id, type="WEEKLY")
            generated += 1
        except Exception:
            logger.exception(f"Failed to generate reflection for user {user_id}")

    logger.info(f"Weekly reflections generated for {generated}/{len(profiles)} learner(s)")
