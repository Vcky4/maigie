"""
Email notification tasks (Celery).

- Morning schedule: Daily, timezone-aware (7 AM local)
- Schedule reminder: Every 15 minutes (15 min before start)
- Weekly tips: Weekly (Sunday 8 PM UTC)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task
from src.tasks.schedules import (
    EVERY_15_MINUTES,
    HOURLY,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

TASK_MORNING_SCHEDULE = "email_notifications.send_morning_schedule_emails"
TASK_SCHEDULE_REMINDER = "email_notifications.send_schedule_reminders"
TASK_WEEKLY_TIPS = "email_notifications.send_weekly_tips_emails"

# Morning email runs at 7 AM in user's local time; we check every hour
MORNING_LOCAL_HOUR = 7
REMINDER_WINDOW_MINUTES = 15


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _send_morning_schedule_emails_impl() -> dict:
    """
    Send morning schedule emails to users where it's currently 7 AM
    in their timezone. Runs hourly; each run targets users in timezones
    where local hour is 7.
    """
    from src.core.database import db
    from src.services import ai_email_service, email

    await _ensure_db_connected()
    now = datetime.now(UTC)
    sent = 0
    errors = []

    # Users with preferences (emailMorningSchedule=True, notifications=True)
    # Include user and preferences
    users = await db.user.find_many(
        where={"isActive": True},
        include={
            "preferences": True,
        },
    )

    for user in users:
        try:
            prefs = user.preferences
            if prefs and not getattr(prefs, "emailMorningSchedule", True):
                continue
            if prefs and not getattr(prefs, "notifications", True):
                continue

            tz_str = (getattr(prefs, "timezone", None) if prefs else None) or "UTC"
            try:
                tz = ZoneInfo(tz_str)
            except Exception:
                tz = ZoneInfo("UTC")

            local_now = now.astimezone(tz)
            if local_now.hour != MORNING_LOCAL_HOUR:
                continue

            # Fetch today's schedules (in user's local date)
            local_date_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            local_date_end = local_date_start + timedelta(days=1)
            # Convert to UTC for DB query
            utc_start = local_date_start.astimezone(UTC)
            utc_end = local_date_end.astimezone(UTC)

            blocks = await db.scheduleblock.find_many(
                where={
                    "userId": user.id,
                    "startAt": {"gte": utc_start, "lt": utc_end},
                },
                order={"startAt": "asc"},
            )

            schedules_today = [
                {
                    "title": b.title,
                    "time": b.startAt.astimezone(tz).strftime("%I:%M %p"),
                }
                for b in blocks
            ]
            date_label = local_now.strftime("Here's your day for %A, %b %d")

            subject, template_data = await ai_email_service.draft_morning_schedule_email(
                db_client=db,
                user=user,
                schedules_today=schedules_today,
                date_label=date_label,
            )

            await email.send_morning_schedule_email(
                email=user.email,
                name=user.name,
                subject=subject,
                template_data=template_data,
            )
            sent += 1
        except Exception as e:
            logger.exception("Morning schedule email failed for user %s: %s", user.id, e)
            errors.append({"userId": user.id, "error": str(e)})

    return {"sent": sent, "errors": errors}


async def _send_schedule_reminders_impl() -> dict:
    """
    Find ScheduleBlocks starting in the next 15 minutes and send reminders.
    """
    from src.core.database import db
    from src.services import ai_email_service, email

    await _ensure_db_connected()
    now = datetime.now(UTC)
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)
    sent = 0
    errors = []

    blocks = await db.scheduleblock.find_many(
        where={
            "startAt": {"gte": now, "lte": window_end},
        },
        include={"user": {"include": {"preferences": True}}},
    )

    for block in blocks:
        try:
            user = block.user
            if not user or not user.email:
                continue
            prefs = user.preferences
            if prefs and not getattr(prefs, "emailScheduleReminder", True):
                continue
            if prefs and not getattr(prefs, "notifications", True):
                continue

            tz_str = (getattr(prefs, "timezone", None) if prefs else None) or "UTC"
            try:
                tz = ZoneInfo(tz_str)
            except Exception:
                tz = ZoneInfo("UTC")

            schedule_time = block.startAt.astimezone(tz).strftime("%I:%M %p on %A, %b %d")
            schedule_description = (block.description or "")[:200]

            subject, template_data = await ai_email_service.draft_schedule_reminder_email(
                schedule_title=block.title,
                schedule_time=schedule_time,
                schedule_description=schedule_description or None,
                user_name=user.name or "",
            )

            template_data["schedule_title"] = block.title
            template_data["schedule_time"] = schedule_time
            template_data["schedule_description"] = schedule_description or None

            await email.send_schedule_reminder_email(
                email=user.email,
                name=user.name,
                subject=subject,
                template_data=template_data,
            )
            sent += 1
        except Exception as e:
            logger.exception("Schedule reminder failed for block %s: %s", block.id, e)
            errors.append({"blockId": block.id, "error": str(e)})

    return {"sent": sent, "errors": errors}


async def _send_weekly_tips_emails_impl() -> dict:
    """Send weekly tips to users with emailWeeklyTips enabled."""
    from src.core.database import db
    from src.services import ai_email_service, email

    await _ensure_db_connected()
    sent = 0
    errors = []

    users = await db.user.find_many(
        where={"isActive": True},
        include={"preferences": True},
    )

    for user in users:
        try:
            prefs = user.preferences
            if prefs and not getattr(prefs, "emailWeeklyTips", True):
                continue
            if prefs and not getattr(prefs, "notifications", True):
                continue

            subject, template_data = await ai_email_service.draft_weekly_tips_email(
                db_client=db,
                user=user,
            )

            await email.send_weekly_tips_email(
                email=user.email,
                name=user.name,
                subject=subject,
                template_data=template_data,
            )
            sent += 1
        except Exception as e:
            logger.exception("Weekly tips email failed for user %s: %s", user.id, e)
            errors.append({"userId": user.id, "error": str(e)})

    return {"sent": sent, "errors": errors}


@register_task(
    name=TASK_MORNING_SCHEDULE,
    description="Send morning schedule emails (timezone-aware, 7 AM local)",
    category="email",
    tags=["email", "schedule", "notification"],
)
@task(name=TASK_MORNING_SCHEDULE, bind=True, max_retries=2)
def send_morning_schedule_emails_task(self) -> dict:
    return run_async_in_celery(_send_morning_schedule_emails_impl())


@register_task(
    name=TASK_SCHEDULE_REMINDER,
    description="Send schedule reminders 15 minutes before start",
    category="email",
    tags=["email", "schedule", "reminder"],
)
@task(name=TASK_SCHEDULE_REMINDER, bind=True, max_retries=2)
def send_schedule_reminders_task(self) -> dict:
    return run_async_in_celery(_send_schedule_reminders_impl())


@register_task(
    name=TASK_WEEKLY_TIPS,
    description="Send weekly encouragement/tips emails",
    category="email",
    tags=["email", "tips", "notification"],
)
@task(name=TASK_WEEKLY_TIPS, bind=True, max_retries=2)
def send_weekly_tips_emails_task(self) -> dict:
    return run_async_in_celery(_send_weekly_tips_emails_impl())


def register_email_notification_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for email notifications."""
    from celery.schedules import crontab

    # Morning: run every hour; task filters by user timezone (7 AM local)
    register_periodic_task(
        name="email_notifications.morning_schedule.hourly",
        schedule=HOURLY,
        task=TASK_MORNING_SCHEDULE,
    )

    # Reminders: every 15 minutes
    register_periodic_task(
        name="email_notifications.schedule_reminder.every_15min",
        schedule=EVERY_15_MINUTES,
        task=TASK_SCHEDULE_REMINDER,
    )

    # Weekly tips: Sunday 8 PM UTC
    register_periodic_task(
        name="email_notifications.weekly_tips.sunday",
        schedule=crontab(hour=20, minute=0, day_of_week=0),
        task=TASK_WEEKLY_TIPS,
    )


register_email_notification_beat_tasks()
