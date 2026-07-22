"""
Retention Check — Daily task that evaluates churn risk for PLUS subscribers.

Calculates risk scores and triggers retention interventions when score > 0.7.
"""

import asyncio
import logging

from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="personal_learning.retention_check",
    queue="default",
    max_retries=2,
    time_limit=300,
    soft_time_limit=240,
)
def check_retention():
    """
    Daily: Evaluate churn risk for all PLUS subscribers.

    Triggers retention interventions for users with risk > 0.7.
    """
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run_retention_check())
        return result
    finally:
        loop.close()


async def _run_retention_check() -> dict:
    """Core async logic for retention check."""
    from src.domains.personal_learning.repository import PersonalLearningRepository
    from src.domains.personal_learning.services import retention_service
    from src.shared.database.session import get_session
    from sqlalchemy import select
    from src.domains.identity.db_models import User

    total_checked = 0
    interventions_triggered = 0

    # Get all PLUS subscribers
    async with get_session() as session:
        stmt = (
            select(User.id)
            .where(User.tier.like("PREMIUM%"))
            .where(User.is_active.is_(True))
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    for user_id in user_ids:
        try:
            total_checked += 1
            intervention = await retention_service.evaluate_retention_intervention(user_id)
            if intervention:
                interventions_triggered += 1
                logger.info(
                    f"Retention intervention triggered for {user_id}: "
                    f"type={intervention.intervention_type}"
                )
        except Exception as e:
            logger.error(f"Error checking retention for {user_id}: {e}")

    logger.info(
        f"Retention check complete: {total_checked} checked, "
        f"{interventions_triggered} interventions triggered"
    )

    return {
        "total_checked": total_checked,
        "interventions_triggered": interventions_triggered,
    }
