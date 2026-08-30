"""
Notification background tasks.

Email notifications (schedule reminders, weekly summaries, re-engagement)
and push notifications (FCM). Routed to 'default' queue (lightweight).
"""

import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="notifications.send_email", queue="default", time_limit=30)
def send_email_task(to_email: str, template: str, context: dict):
    """Send a transactional email."""
    import asyncio

    from src.integrations.brevo import send_template_email

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(send_template_email(to_email, template, context))
    finally:
        loop.close()


@celery_app.task(name="notifications.send_push", queue="default", time_limit=15)
def send_push_task(user_id: str, title: str, body: str, data: dict | None = None):
    """Send a push notification via FCM."""
    import asyncio

    from src.shared.infrastructure.push_notifications import send_push_to_user

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(send_push_to_user(user_id, title, body, data))
    finally:
        loop.close()


@celery_app.task(name="notifications.schedule_reminders", queue="default", time_limit=60)
def send_schedule_reminders_task():
    """Send schedule reminders to users with upcoming study blocks."""
    import asyncio

    from src.domains.progress.services.schedule_reminders import send_schedule_reminders

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(send_schedule_reminders())
    finally:
        loop.close()


@celery_app.task(name="notifications.weekly_summary", queue="default", time_limit=120)
def send_weekly_summaries_task():
    """Send weekly learning summary emails."""
    import asyncio

    from src.shared.infrastructure.email import send_weekly_summaries

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(send_weekly_summaries())
    finally:
        loop.close()


def _run_notification_coro(function_name: str) -> int:
    import asyncio

    from src.domains.notifications import dispatcher
    from src.shared.database.session import ensure_db

    async def _run() -> int:
        await ensure_db()
        return await getattr(dispatcher, function_name)()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(
    name="notifications.dispatch_mobile_push",
    queue="default",
    time_limit=60,
    soft_time_limit=50,
)
def dispatch_mobile_push_task() -> int:
    return _run_notification_coro("dispatch_due")


@celery_app.task(
    name="notifications.reconcile_expo_receipts",
    queue="default",
    time_limit=60,
    soft_time_limit=50,
)
def reconcile_expo_receipts_task() -> int:
    return _run_notification_coro("reconcile_receipts")


@celery_app.task(
    name="notifications.recover_stale_mobile_push",
    queue="default",
    time_limit=45,
    soft_time_limit=35,
)
def recover_stale_mobile_push_task() -> int:
    return _run_notification_coro("recover_stale_sending")


def get_beat_schedule() -> dict:
    return {
        "notifications.dispatch_mobile_push": {
            "task": "notifications.dispatch_mobile_push",
            "schedule": 60.0,
            "options": {"queue": "default"},
        },
        "notifications.reconcile_expo_receipts": {
            "task": "notifications.reconcile_expo_receipts",
            "schedule": 300.0,
            "options": {"queue": "default"},
        },
        "notifications.recover_stale_mobile_push": {
            "task": "notifications.recover_stale_mobile_push",
            "schedule": 300.0,
            "options": {"queue": "default"},
        },
    }
