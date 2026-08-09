"""Celery task: Generate weekly reflections.

Schedule: Weekly Sunday at 04:00 UTC | Queue: heavy

Uses paginated batch processing to avoid loading all users into memory.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50


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
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import reflection_service

    logger.info("Weekly reflections task started")

    generated = 0
    total = 0
    skip = 0

    while True:
        profiles = await repo.list_active_profiles(skip=skip, take=_BATCH_SIZE)
        if not profiles:
            break

        total += len(profiles)

        for profile in profiles:
            user_id = profile.user_id
            try:
                await reflection_service.generate_reflection(user_id=user_id, type="WEEKLY")
                generated += 1
            except Exception:
                logger.exception(f"Failed to generate reflection for user {user_id}")

        skip += _BATCH_SIZE
        if len(profiles) < _BATCH_SIZE:
            break

    logger.info(f"Weekly reflections generated for {generated}/{total} learner(s)")
