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

    from src.domains.identity.emails import send_template_email

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


def _run_producer(module_path: str, function_name: str) -> dict:
    """Run one notification producer, with the database connected.

    Both producers previously opened an event loop and ran their coroutine without calling
    `ensure_db`, unlike every dispatch task below. Neither was in the beat schedule, so the
    omission never fired; scheduling them makes it a real defect, hence this shared runner.
    """
    import asyncio
    import importlib

    from src.shared.database.session import ensure_db

    target = importlib.import_module(module_path)

    async def _run() -> dict:
        await ensure_db()
        return await getattr(target, function_name)()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(name="notifications.schedule_reminders", queue="default", time_limit=120)
def send_schedule_reminders_task() -> dict:
    """Produce canonical reminders for study blocks starting soon."""
    return _run_producer(
        "src.domains.progress.services.schedule_reminders", "send_schedule_reminders"
    )


@celery_app.task(name="notifications.weekly_summary", queue="default", time_limit=300)
def send_weekly_summaries_task() -> dict:
    """Produce canonical weekly learning summaries."""
    return _run_producer("src.domains.progress.services.weekly_summary", "send_weekly_summaries")


def _run_notification_coro(function_name: str, module: str = "dispatcher") -> int:
    import asyncio
    import importlib

    from src.shared.database.session import ensure_db

    target = importlib.import_module(f"src.domains.notifications.{module}")

    async def _run() -> int:
        await ensure_db()
        return await getattr(target, function_name)()

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


@celery_app.task(
    name="notifications.dispatch_email",
    queue="default",
    time_limit=120,
    soft_time_limit=110,
)
def dispatch_notification_email_task() -> int:
    return _run_notification_coro("dispatch_due_email", module="email_dispatcher")


@celery_app.task(
    name="notifications.plan_digests",
    queue="default",
    time_limit=300,
    soft_time_limit=280,
)
def plan_notification_digests_task() -> int:
    summary = _run_producer("src.domains.notifications.digest", "plan_due_digests")
    return int(summary.get("created", 0))


def get_beat_schedule() -> dict:
    return {
        "notifications.plan_digests": {
            "task": "notifications.plan_digests",
            # Hourly, because a period closes at a different instant in every timezone and the
            # digest's unique key makes a run that has nothing to do a no-op.
            "schedule": 3600.0,
            "options": {"queue": "default"},
        },
        "notifications.schedule_reminders": {
            "task": "notifications.schedule_reminders",
            # Must match REMINDER_WINDOW_MINUTES in the producer. The idempotency key makes
            # a mismatch harmless rather than duplicating, but a slower cadence than the
            # window would still miss blocks entirely.
            "schedule": 900.0,
            "options": {"queue": "default"},
        },
        "notifications.weekly_summary": {
            "task": "notifications.weekly_summary",
            # Hourly, guarded by an ISO-week idempotency key: the first run of a new week
            # produces the summary and every later run that week replays it. This avoids
            # pinning delivery to one hour, which would miss anyone whose week has not
            # ended yet in their own timezone.
            "schedule": 3600.0,
            "options": {"queue": "default"},
        },
        "notifications.dispatch_email": {
            "task": "notifications.dispatch_email",
            # Email is not time-critical the way a push is, and a slower cadence keeps the
            # provider request rate low. Quiet-hour deferrals set their own release time.
            "schedule": 300.0,
            "options": {"queue": "default"},
        },
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
