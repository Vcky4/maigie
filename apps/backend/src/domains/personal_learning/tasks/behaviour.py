"""Celery task: Analyze learner behaviour patterns.

Schedule: Daily at 02:00 UTC | Queue: heavy

Uses paginated batch processing to avoid loading all users into memory.
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50


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
    from src.shared.database.session import ensure_db

    await ensure_db()
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.domains.personal_learning.services import behaviour_service

    logger.info("Behaviour analysis task started")

    updated = 0
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
                # Recompute behaviour analytics and persist to profile.
                #
                # `analyze_behaviour` loads its own evidence. It used to *require* a
                # `sessions` argument that this call never passed, so every
                # iteration raised `TypeError` straight into the `except` below:
                # the task logged "updated 0/N" and every learner's behaviour
                # columns stayed NULL for as long as this has existed. The
                # swallowing `except` is what kept it invisible.
                await behaviour_service.analyze_behaviour(user_id=user_id)

                # Also increment maturity_days
                await repo.increment_maturity_days(user_id)

                updated += 1
            except Exception:
                logger.exception(f"Failed to analyze behaviour for user {user_id}")

        skip += _BATCH_SIZE
        if len(profiles) < _BATCH_SIZE:
            break

    logger.info(f"Behaviour analysis completed: updated {updated}/{total} profile(s)")
