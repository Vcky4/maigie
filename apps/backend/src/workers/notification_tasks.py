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

    from src.tasks.email_notifications import send_schedule_reminders

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
