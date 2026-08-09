"""
Trial Expiry — Hourly task that expires overdue trials.

Gracefully expires trials that have passed their end date and generates
trial summaries for those users.
"""

import asyncio
import logging
from datetime import UTC

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="personal_learning.trial_expiry",
    queue="default",
    max_retries=2,
    time_limit=120,
    soft_time_limit=90,
)
def expire_trials():
    """
    Hourly: Expire trials that have passed their end date.

    Gracefully degrades PLUS features back to FREE without data loss.
    """
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run_trial_expiry())
        return result
    finally:
        loop.close()


async def _run_trial_expiry() -> dict:
    from src.shared.database.session import ensure_db

    await ensure_db()
    """Core async logic for trial expiry."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from src.domains.personal_learning.db_models import LearningProfile
    from src.domains.personal_learning.services import trial_service
    from src.shared.database.session import get_session

    now = datetime.now(UTC)
    total_expired = 0

    # Find profiles with expired trials (trial_ends_at < now AND last_trial_ended_at is NULL)
    async with get_session() as session:
        stmt = (
            select(LearningProfile.user_id)
            .where(LearningProfile.trial_ends_at.isnot(None))
            .where(LearningProfile.trial_ends_at < now)
            .where(LearningProfile.last_trial_ended_at.is_(None))
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        try:
            await trial_service.expire_trial(user_id)
            total_expired += 1
            logger.info(f"Trial expired for user {user_id}")
        except Exception as e:
            logger.error(f"Error expiring trial for {user_id}: {e}")

    if total_expired > 0:
        logger.info(f"Trial expiry complete: {total_expired} trials expired")

    return {"total_expired": total_expired}
