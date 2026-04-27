"""
Email notification tasks (Celery).

- Morning schedule: Paid users daily (6 AM local); FREE users weekly (Monday 6 AM local) with upgrade pitch
- Schedule reminder: Every 15 minutes (15 min before start) — paid users only
- Weekly tips: Weekly (Sunday 8 PM UTC)
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
    HOURLY,
    register_periodic_task,
)

logger = logging.getLogger(__name__)

TASK_MORNING_SCHEDULE = "email_notifications.send_morning_schedule_emails"
TASK_SCHEDULE_REMINDER = "email_notifications.send_schedule_reminders"
TASK_WEEKLY_TIPS = "email_notifications.send_weekly_tips_emails"
TASK_ACCOUNT_DELETION_LIFECYCLE = "email_notifications.process_account_deletion_lifecycle"
PAID_TIERS = {
    "PREMIUM_MONTHLY",
    "PREMIUM_YEARLY",
    "STUDY_CIRCLE_MONTHLY",
    "STUDY_CIRCLE_YEARLY",
    "SQUAD_MONTHLY",
    "SQUAD_YEARLY",
}

# Morning email runs at 6 AM in user's local time; we check every hour
MORNING_LOCAL_HOUR = 6
# FREE tier: one digest per week (Monday morning local)
FREE_WEEKLY_MORNING_WEEKDAY = 0  # Monday (datetime.weekday: Mon=0 .. Sun=6)
REMINDER_WINDOW_MINUTES = 15


def _user_is_paid_tier(user: Any) -> bool:
    tier = getattr(user, "tier", None)
    return str(tier if tier is not None else "FREE") in PAID_TIERS


def _subscription_settings_url() -> str:
    from src.config import settings

    base = (settings.FRONTEND_BASE_URL or settings.FRONTEND_URL or "http://localhost:4200").rstrip(
        "/"
    )
    return f"{base}/settings?tab=subscription"


def _free_weekly_schedule_upgrade_blocks() -> dict[str, str]:
    """Short marketing block for FREE weekly digest (HTML + plain)."""
    url = _subscription_settings_url()
    html = (
        '<div style="margin: 24px 0; padding: 16px; background: linear-gradient(135deg, #eef2ff 0%, #faf5ff 100%); '
        'border-radius: 8px; border: 1px solid #e0e7ff;">'
        '<p style="margin: 0 0 8px 0; font-size: 15px; font-weight: 600; color: #3730a3;">'
        "Unlock daily schedule emails &amp; reminders</p>"
        '<p style="margin: 0; font-size: 14px; color: #4b5563; line-height: 1.5;">'
        "Maigie <strong>Plus</strong> includes a personalized morning schedule <strong>every day</strong> "
        "and a <strong>15-minute reminder</strong> before each session starts—so you never miss a block. "
        "Upgrade when you are ready.</p>"
        f'<p style="margin: 12px 0 0 0;"><a href="{url}" style="color: #4F46E5; font-weight: 600;">'
        "View plans &amp; upgrade →</a></p></div>"
    )
    plain = (
        "Unlock daily morning schedule emails and 15-minute session reminders with Maigie Plus. "
        f"View plans: {url}"
    )
    return {"upgrade_pitch_html": html, "upgrade_pitch_plain": plain}


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _send_morning_schedule_emails_impl() -> dict:
    """
    Send morning schedule digest emails at 6 AM local.

    Paid tiers: daily email for today's blocks.
    FREE: one weekly digest (Monday 6 AM local) for Mon–Sun blocks, with upgrade pitch.
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

            is_paid = _user_is_paid_tier(user)

            if is_paid:
                local_date_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                local_date_end = local_date_start + timedelta(days=1)
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
                    digest_mode="daily",
                )
            else:
                if local_now.weekday() != FREE_WEEKLY_MORNING_WEEKDAY:
                    continue

                week_start = (local_now - timedelta(days=local_now.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                week_end = week_start + timedelta(days=7)
                utc_start = week_start.astimezone(UTC)
                utc_end = week_end.astimezone(UTC)

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
                        "time": b.startAt.astimezone(tz).strftime("%a, %b %d · %I:%M %p"),
                    }
                    for b in blocks
                ]
                week_last = week_end - timedelta(days=1)
                if week_start.month == week_last.month:
                    date_label = f"Week of {week_start.strftime('%B')} {week_start.day}–{week_last.day}, {week_start.year}"
                else:
                    date_label = (
                        f"Week of {week_start.strftime('%b %d')}–{week_last.strftime('%b %d, %Y')}"
                    )

                subject, template_data = await ai_email_service.draft_morning_schedule_email(
                    db_client=db,
                    user=user,
                    schedules_today=schedules_today,
                    date_label=date_label,
                    digest_mode="weekly",
                )
                template_data.update(_free_weekly_schedule_upgrade_blocks())

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
            if str(getattr(user, "tier", "FREE")) not in PAID_TIERS:
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


async def _process_account_deletion_lifecycle_impl() -> dict:
    """
    Process account deletion reminders and final deletions.

    - 30 days before deletion: send reminder with cancel link
    - 7 days before deletion: send reminder with cancel link
    - on scheduled date (or after): delete account and send confirmation email
    """
    from src.core.database import db
    from src.services import email
    from src.services.account_deletion_service import (
        ACCOUNT_DELETION_REMINDER_30_DAYS,
        ACCOUNT_DELETION_REMINDER_7_DAYS,
        build_account_deletion_cancel_url,
        utc_now,
    )

    await _ensure_db_connected()
    now = utc_now()
    reminded_30 = 0
    reminded_7 = 0
    deleted = 0
    errors: list[dict[str, str]] = []

    users = await db.user.find_many(
        where={
            "accountDeletionScheduledFor": {"not": None},
            "accountDeletionCancelToken": {"not": None},
        }
    )
    for user in users:
        try:
            scheduled_for = getattr(user, "accountDeletionScheduledFor", None)
            cancel_token = getattr(user, "accountDeletionCancelToken", None)
            if not scheduled_for or not cancel_token:
                continue

            if scheduled_for <= now:
                user_email = user.email
                user_name = user.name
                await db.user.delete(where={"id": user.id})
                deleted += 1
                try:
                    await email.send_account_deleted_email(user_email, user_name)
                except Exception as mail_err:
                    logger.warning(
                        "Failed to send account deleted email for user %s: %s", user.id, mail_err
                    )
                continue

            cancel_url = build_account_deletion_cancel_url(cancel_token)
            days_left = max(0, int((scheduled_for - now).total_seconds() // 86400) + 1)
            scheduled_iso = scheduled_for.date().isoformat()
            reminder_30_sent = getattr(user, "accountDeletionReminder30SentAt", None)
            reminder_7_sent = getattr(user, "accountDeletionReminder7SentAt", None)

            if reminder_30_sent is None and now >= scheduled_for - timedelta(
                days=ACCOUNT_DELETION_REMINDER_30_DAYS
            ):
                await email.send_account_deletion_reminder_email(
                    user.email,
                    user.name,
                    days_left=days_left,
                    scheduled_for_iso=scheduled_iso,
                    cancel_url=cancel_url,
                )
                await db.user.update(
                    where={"id": user.id},
                    data={"accountDeletionReminder30SentAt": now},
                )
                reminded_30 += 1

            if reminder_7_sent is None and now >= scheduled_for - timedelta(
                days=ACCOUNT_DELETION_REMINDER_7_DAYS
            ):
                await email.send_account_deletion_reminder_email(
                    user.email,
                    user.name,
                    days_left=days_left,
                    scheduled_for_iso=scheduled_iso,
                    cancel_url=cancel_url,
                )
                await db.user.update(
                    where={"id": user.id},
                    data={"accountDeletionReminder7SentAt": now},
                )
                reminded_7 += 1
        except Exception as e:
            logger.exception(
                "Account deletion lifecycle processing failed for user %s: %s", user.id, e
            )
            errors.append({"userId": user.id, "error": str(e)})

    return {
        "reminded30": reminded_30,
        "reminded7": reminded_7,
        "deleted": deleted,
        "errors": errors,
    }


@register_task(
    name=TASK_MORNING_SCHEDULE,
    description="Send morning schedule emails (timezone-aware, 6 AM local)",
    category="email",
    tags=["email", "schedule", "notification"],
)
@task(name=TASK_MORNING_SCHEDULE, bind=True, max_retries=2)
def send_morning_schedule_emails_task(self) -> dict:
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping morning schedule emails (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}
    return run_async_in_celery(_send_morning_schedule_emails_impl())


@register_task(
    name=TASK_SCHEDULE_REMINDER,
    description="Send schedule reminders 15 minutes before start",
    category="email",
    tags=["email", "schedule", "reminder"],
)
@task(name=TASK_SCHEDULE_REMINDER, bind=True, max_retries=2)
def send_schedule_reminders_task(self) -> dict:
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping schedule reminders (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}
    return run_async_in_celery(_send_schedule_reminders_impl())


@register_task(
    name=TASK_WEEKLY_TIPS,
    description="Send weekly encouragement/tips emails",
    category="email",
    tags=["email", "tips", "notification"],
)
@task(name=TASK_WEEKLY_TIPS, bind=True, max_retries=2)
def send_weekly_tips_emails_task(self) -> dict:
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping weekly tips emails (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}
    return run_async_in_celery(_send_weekly_tips_emails_impl())


@register_task(
    name=TASK_ACCOUNT_DELETION_LIFECYCLE,
    description="Process account deletion reminders and due deletions",
    category="email",
    tags=["email", "account", "deletion"],
)
@task(name=TASK_ACCOUNT_DELETION_LIFECYCLE, bind=True, max_retries=2)
def process_account_deletion_lifecycle_task(self) -> dict:
    from src.config import settings

    if settings.ENVIRONMENT == "development":
        logger.info("Skipping account deletion lifecycle task (env=%s)", settings.ENVIRONMENT)
        return {"skipped": True, "reason": "development environment"}
    return run_async_in_celery(_process_account_deletion_lifecycle_impl())


def register_email_notification_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for email notifications."""
    from celery.schedules import crontab

    # Morning: run every hour; task filters by user timezone (6 AM local)
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

    # Account deletion reminders + due deletion check: hourly
    register_periodic_task(
        name="email_notifications.account_deletion.hourly",
        schedule=HOURLY,
        task=TASK_ACCOUNT_DELETION_LIFECYCLE,
    )


register_email_notification_beat_tasks()
