"""
Value Summary Generation — Daily task that generates value summaries
for PLUS subscribers 3 days before their renewal date.

Delivers learning-framed value communication before renewal.
"""

import asyncio
import logging
from datetime import UTC

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="personal_learning.value_summary_generation",
    queue="default",
    max_retries=2,
    time_limit=300,
    soft_time_limit=240,
)
def generate_value_summaries():
    """
    Daily: Generate value summaries for PLUS subscribers approaching renewal.

    Targets users whose billing period ends within 3 days.
    """
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run_value_summary_generation())
        return result
    finally:
        loop.close()


async def _run_value_summary_generation() -> dict:
    from src.shared.database.session import ensure_db

    await ensure_db()
    """Core async logic for value summary generation."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from src.domains.identity.db_models import User
    from src.domains.personal_learning.services import value_summary_service
    from src.shared.database.session import get_session

    now = datetime.now(UTC)
    target_end = now + timedelta(days=3)

    total_generated = 0
    errors = 0

    # Find PLUS subscribers whose period ends within 3 days
    async with get_session() as session:
        stmt = (
            select(User.id)
            .where(User.tier.like("PREMIUM%"))
            .where(User.is_active.is_(True))
            .where(User.credits_period_end.isnot(None))
            .where(User.credits_period_end <= target_end)
            .where(User.credits_period_end > now)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        try:
            await value_summary_service.generate_monthly_summary(user_id)
            total_generated += 1
        except Exception as e:
            errors += 1
            logger.error(f"Error generating value summary for {user_id}: {e}")

    logger.info(
        f"Value summary generation complete: {total_generated} generated, " f"{errors} errors"
    )

    return {"total_generated": total_generated, "errors": errors}
