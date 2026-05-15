"""
Push notification tasks (Celery).

Background tasks for sending FCM push notifications.
These tasks are enqueued by other services (e.g., schedule reminders,
chat messages, study tips) and processed asynchronously.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task

logger = logging.getLogger(__name__)

TASK_SEND_PUSH = "push_notifications.send_push"
TASK_SEND_PUSH_BATCH = "push_notifications.send_push_batch"
TASK_SEND_SCHEDULE_PUSH_REMINDER = "push_notifications.send_schedule_push_reminder"


@register_task(
    name=TASK_SEND_PUSH,
    description="Send a push notification to a single user",
    category="notification",
    tags=["push", "fcm"],
)
@task(name=TASK_SEND_PUSH, max_retries=3, default_retry_delay=30)
def send_push_notification_task(
    user_id: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Celery task: send push notification to a user's devices.

    Args:
        user_id: Target user ID.
        title: Notification title.
        body: Notification body.
        data: Optional data payload for client-side routing.
        image_url: Optional image URL for rich notifications.

    Returns:
        Result dict with sent/failed counts.
    """

    async def _send() -> dict[str, Any]:
        from src.core.database import db

        if not db.is_connected():
            await db.connect()

        from src.services.push_notification_service import send_push_notification

        return await send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
        )

    return run_async_in_celery(_send())


@register_task(
    name=TASK_SEND_PUSH_BATCH,
    description="Send a push notification to multiple users",
    category="notification",
    tags=["push", "fcm", "batch"],
)
@task(name=TASK_SEND_PUSH_BATCH, max_retries=2, default_retry_delay=60)
def send_push_batch_task(
    user_ids: list[str],
    title: str,
    body: str,
    data: dict[str, str] | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Celery task: send push notification to multiple users.

    Args:
        user_ids: List of target user IDs.
        title: Notification title.
        body: Notification body.
        data: Optional data payload.
        image_url: Optional image URL.

    Returns:
        Aggregate result dict.
    """

    async def _send() -> dict[str, Any]:
        from src.core.database import db

        if not db.is_connected():
            await db.connect()

        from src.services.push_notification_service import send_push_to_multiple_users

        return await send_push_to_multiple_users(
            user_ids=user_ids,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
        )

    return run_async_in_celery(_send())


@register_task(
    name=TASK_SEND_SCHEDULE_PUSH_REMINDER,
    description="Send schedule reminder push notifications (15 min before)",
    category="notification",
    tags=["push", "fcm", "schedule"],
)
@task(name=TASK_SEND_SCHEDULE_PUSH_REMINDER, max_retries=2, default_retry_delay=30)
def send_schedule_push_reminder_task() -> dict[str, Any]:
    """Celery task: check upcoming schedule blocks and send push reminders.

    Runs every 15 minutes. Finds schedule blocks starting in the next 15 minutes
    and sends push notifications to the users.

    Returns:
        Result dict with count of reminders sent.
    """
    from datetime import UTC, datetime, timedelta

    async def _send_reminders() -> dict[str, Any]:
        from src.core.database import db

        if not db.is_connected():
            await db.connect()

        from src.services.push_notification_service import send_push_notification

        now = datetime.now(UTC)
        window_start = now
        window_end = now + timedelta(minutes=15)

        # Find schedule blocks starting in the next 15 minutes
        upcoming_blocks = await db.scheduleblock.find_many(
            where={
                "startAt": {
                    "gte": window_start,
                    "lte": window_end,
                },
            },
            include={"user": True},
        )

        sent_count = 0
        for block in upcoming_blocks:
            # Format the start time nicely
            start_time = block.startAt.strftime("%I:%M %p")
            title = "📚 Study Reminder"
            body = f"{block.title} starts at {start_time}"

            data = {
                "type": "schedule_reminder",
                "schedule_id": block.id,
            }
            if block.courseId:
                data["course_id"] = block.courseId

            result = await send_push_notification(
                user_id=block.userId,
                title=title,
                body=body,
                data=data,
            )
            if result.get("sent", 0) > 0:
                sent_count += 1

        logger.info(f"Schedule push reminders sent: {sent_count}/{len(upcoming_blocks)}")
        return {"reminders_sent": sent_count, "blocks_checked": len(upcoming_blocks)}

    return run_async_in_celery(_send_reminders())
