"""
Push notification tasks (Celery).

Background tasks for sending FCM push notifications.
- Schedule reminder: Every 15 minutes (15 min before start) — all users with active device tokens
- Generic push: On-demand single/batch notifications

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task
from src.tasks.schedules import (
    EVERY_15_MINUTES,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

TASK_SEND_PUSH = "push_notifications.send_push"
TASK_SEND_PUSH_BATCH = "push_notifications.send_push_batch"
TASK_SCHEDULE_PUSH_REMINDER = "push_notifications.send_schedule_push_reminders"

REMINDER_WINDOW_MINUTES = 15


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _send_schedule_push_reminders_impl() -> dict[str, Any]:
    """
    Find ScheduleBlocks starting in the next 15 minutes and send push reminders.

    Respects user preferences:
    - notifications must be enabled (global toggle)
    - pushScheduleReminder must be enabled
    - User must have at least one active device token
    """
    from src.core.database import db
    from src.services.push_notification_service import send_push_notification

    await _ensure_db_connected()

    now = datetime.now(UTC)
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)
    sent = 0
    skipped = 0
    errors: list[dict[str, str]] = []

    # Find schedule blocks starting in the next 15 minutes
    blocks = await db.scheduleblock.find_many(
        where={
            "startAt": {"gte": now, "lte": window_end},
        },
        include={
            "user": {
                "include": {
                    "preferences": True,
                    "deviceTokens": {"where": {"isActive": True}},
                }
            },
            "course": True,
        },
    )

    for block in blocks:
        try:
            user = block.user
            if not user:
                continue

            # Check if user has active device tokens
            device_tokens = getattr(user, "deviceTokens", None) or []
            if not device_tokens:
                skipped += 1
                continue

            # Check user preferences
            prefs = user.preferences
            if prefs and not getattr(prefs, "notifications", True):
                skipped += 1
                continue
            if prefs and not getattr(prefs, "pushScheduleReminder", True):
                skipped += 1
                continue

            # Format the start time in user's timezone
            tz_str = (getattr(prefs, "timezone", None) if prefs else None) or "UTC"
            try:
                tz = ZoneInfo(tz_str)
            except Exception:
                tz = ZoneInfo("UTC")

            local_start = block.startAt.astimezone(tz)
            start_time = local_start.strftime("%I:%M %p")

            # Build notification content
            title = "📚 Study Reminder"
            body = f"{block.title} starts at {start_time}"

            # Add course context if available
            course = getattr(block, "course", None)
            if course:
                body = f"{block.title} ({course.title}) starts at {start_time}"

            # Data payload for client-side navigation
            data: dict[str, str] = {
                "type": "schedule_reminder",
                "schedule_id": block.id,
                "start_at": block.startAt.isoformat(),
            }
            if block.courseId:
                data["course_id"] = block.courseId
            if block.topicId:
                data["topic_id"] = block.topicId

            result = await send_push_notification(
                user_id=user.id,
                title=title,
                body=body,
                data=data,
            )

            if result.get("sent", 0) > 0:
                sent += 1
            elif result.get("skipped"):
                skipped += 1

        except Exception as e:
            logger.exception("Push schedule reminder failed for block %s: %s", block.id, e)
            errors.append({"blockId": block.id, "error": str(e)})

    logger.info(
        "Push schedule reminders: sent=%d, skipped=%d, errors=%d, blocks_checked=%d",
        sent,
        skipped,
        len(errors),
        len(blocks),
    )
    return {
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "blocks_checked": len(blocks),
    }


# ============================================================================
# Celery Tasks
# ============================================================================


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
        from src.services.push_notification_service import send_push_notification

        await _ensure_db_connected()
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
        from src.services.push_notification_service import send_push_to_multiple_users

        await _ensure_db_connected()
        return await send_push_to_multiple_users(
            user_ids=user_ids,
            title=title,
            body=body,
            data=data,
            image_url=image_url,
        )

    return run_async_in_celery(_send())


@register_task(
    name=TASK_SCHEDULE_PUSH_REMINDER,
    description="Send push schedule reminders 15 minutes before start",
    category="notification",
    tags=["push", "fcm", "schedule", "reminder"],
)
@task(name=TASK_SCHEDULE_PUSH_REMINDER, bind=True, max_retries=2)
def send_schedule_push_reminders_task(self) -> dict[str, Any]:
    """Celery task: check upcoming schedule blocks and send push reminders.

    Runs every 15 minutes via Celery Beat. Finds schedule blocks starting
    in the next 15 minutes and sends push notifications to users who have:
    - Active device tokens registered
    - Push notifications enabled in preferences
    """
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping push schedule reminders (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}

    return run_async_in_celery(_send_schedule_push_reminders_impl())


# ============================================================================
# Celery Beat Registration
# ============================================================================


def register_push_notification_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for push notifications."""
    # Schedule push reminders: every 15 minutes
    register_periodic_task(
        name="push_notifications.schedule_reminder.every_15min",
        schedule=EVERY_15_MINUTES,
        task=TASK_SCHEDULE_PUSH_REMINDER,
    )


register_push_notification_beat_tasks()
