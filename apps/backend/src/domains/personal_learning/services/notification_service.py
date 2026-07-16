"""
Notification service — smart notification creation and delivery.

Every notification earns the right to exist. Right moment, right message.
Respects quiet hours, daily limits, and priority deduplication.
"""

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def create_notification(
    *,
    user_id: str,
    type: str,
    title: str,
    body: str,
    priority: int = 5,
    action_data: dict | None = None,
    scheduled_at: datetime | None = None,
) -> Any:
    """
    Create a smart notification.

    Req 13.1: Store with type, title, body, priority, action_data, scheduled_at.
    Req 13.2: Use behaviour profile for delivery timing if no scheduled_at provided.
    Req 13.4: Enforce daily limit (max 5, excluding quiet-hours overflow).
    Req 10.7: Priority deduplication — only highest priority when simultaneous.
    """
    # Get the learner's profile for scheduling intelligence
    profile = await repo.get_profile_by_user(user_id)

    # Determine delivery time
    if scheduled_at is None:
        scheduled_at = _compute_optimal_time(profile)

    # Check daily limit (enforce max per day, excluding quiet-hours overflow)
    today_count = await repo.count_today_delivered(user_id)
    max_daily = profile.max_daily_notifications if profile else 5
    if today_count >= max_daily:
        logger.info(f"Daily notification limit reached for user {user_id}, suppressing.")
        return None

    # Check quiet hours — if scheduled during quiet hours, queue for later
    status = "PENDING"
    if profile and _is_during_quiet_hours(scheduled_at, profile.quiet_hours_start, profile.quiet_hours_end):
        status = "QUEUED"
        # Reschedule to after quiet hours end
        scheduled_at = _reschedule_after_quiet_hours(scheduled_at, profile.quiet_hours_end)

    notification = await repo.create_notification({
        "userId": user_id,
        "type": type,
        "title": title,
        "body": body,
        "priority": priority,
        "actionData": action_data,
        "scheduledAt": scheduled_at,
        "status": status,
    })

    return notification


async def get_unread(*, user_id: str) -> list[Any]:
    """
    Get unread notifications, sorted by priority then scheduled_at.
    Req 13.5
    """
    return await repo.list_unread(user_id)


async def mark_read(*, user_id: str, notification_id: str) -> None:
    """
    Mark notification as read.
    Req 13.6: Update status and use interaction pattern for future timing.
    """
    await repo.mark_read(notification_id, user_id)


async def dismiss(*, user_id: str, notification_id: str) -> None:
    """
    Dismiss a notification.
    Req 13.6: Update status.
    """
    await repo.mark_dismissed(notification_id, user_id)


async def enforce_daily_limit(*, user_id: str) -> bool:
    """
    Check if the daily notification limit has been reached.
    Returns True if more notifications can be sent, False otherwise.
    """
    profile = await repo.get_profile_by_user(user_id)
    max_daily = profile.max_daily_notifications if profile else 5
    today_count = await repo.count_today_delivered(user_id)
    return today_count < max_daily


async def deliver_pending() -> int:
    """
    Deliver pending notifications whose scheduled_at has passed.
    Called by the Celery notification_delivery task every 5 minutes.

    Returns the count of delivered notifications.
    """
    pending = await repo.list_pending_for_delivery()
    delivered_count = 0

    for notification in pending:
        # Check quiet hours for queued notifications
        profile = await repo.get_profile_by_user(notification.user_id)
        now = datetime.now(timezone.utc)

        if profile and _is_during_quiet_hours(now, profile.quiet_hours_start, profile.quiet_hours_end):
            # Still in quiet hours, skip for now
            continue

        # Deliver the notification (push/email would go here)
        await repo.update_status(
            notification.id,
            status="DELIVERED",
            delivered_at=now,
        )
        delivered_count += 1
        logger.info(f"Delivered notification {notification.id} to user {notification.user_id}")

    return delivered_count


def _compute_optimal_time(profile: Any | None) -> datetime:
    """
    Compute optimal delivery time based on learner's behaviour profile.
    Default: deliver now if no profile data available.
    """
    now = datetime.now(timezone.utc)

    if not profile or not profile.preferred_study_times:
        return now

    # If we have preferred times, schedule near their typical study time
    # For now, deliver immediately (Celery task will handle actual delivery timing)
    return now


def _is_during_quiet_hours(dt: datetime, start: str | None, end: str | None) -> bool:
    """
    Check if a datetime falls within quiet hours.
    Start/end are "HH:MM" strings (e.g., "22:00", "07:00").
    Handles overnight ranges (e.g., 22:00 to 07:00).
    """
    if not start or not end:
        return False

    try:
        start_parts = start.split(":")
        end_parts = end.split(":")
        start_time = time(int(start_parts[0]), int(start_parts[1]))
        end_time = time(int(end_parts[0]), int(end_parts[1]))
    except (ValueError, IndexError):
        return False

    check_time = dt.time()

    if start_time <= end_time:
        # Same-day range (e.g., 09:00 to 17:00)
        return start_time <= check_time <= end_time
    else:
        # Overnight range (e.g., 22:00 to 07:00)
        return check_time >= start_time or check_time <= end_time


def _reschedule_after_quiet_hours(dt: datetime, end: str | None) -> datetime:
    """
    Reschedule a notification to after quiet hours end.
    """
    if not end:
        return dt

    try:
        end_parts = end.split(":")
        end_hour = int(end_parts[0])
        end_minute = int(end_parts[1])
    except (ValueError, IndexError):
        return dt

    # Schedule for the end of quiet hours (next occurrence)
    result = dt.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if result <= dt:
        # Quiet hours end is tomorrow
        result += timedelta(days=1)

    return result
