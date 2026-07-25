"""Celery task: Deliver pending notifications.

Schedule: Every 5 minutes | Queue: default
"""

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="learning.notification_delivery",
    queue="default",
    max_retries=2,
    time_limit=60,
    soft_time_limit=45,
)
def notification_delivery():
    """
    Find notifications with status=PENDING and scheduled_at <= now.
    Check quiet hours. Deliver via push/email. Update status to DELIVERED.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_deliver_notifications_async())
    finally:
        loop.close()


async def _deliver_notifications_async():
    from src.shared.database.session import ensure_db
    from src.domains.personal_learning.services import notification_service

    await ensure_db()
    logger.info("Notification delivery task started")
    count = await notification_service.deliver_pending()
    if count > 0:
        logger.info(f"Delivered {count} notification(s)")
