"""
Personal Learning domain — Celery background tasks.

Registers beat schedule for proactive intelligence, behaviour analysis,
recommendation generation, reflection creation, and notification delivery.

All tasks are defined in submodules and registered via the Celery app decorator.
This package also exposes ``get_beat_schedule()`` for integration with the
global Celery beat configuration.
"""

from celery.schedules import crontab

# Import all task modules so that @celery_app.task registrations execute at import time.
from . import (  # noqa: F401
    daily_plan,
    engagement,
    behaviour,
    recommendations,
    reflections,
    preparation,
    notifications,
)


def get_beat_schedule() -> dict:
    """Return the beat schedule configuration for personal learning tasks."""
    return {
        "learning.prepare_daily_plan": {
            "task": "learning.prepare_daily_plan",
            "schedule": crontab(hour=6, minute=0),
            "options": {"queue": "heavy"},
        },
        "learning.check_declining_engagement": {
            "task": "learning.check_declining_engagement",
            "schedule": crontab(minute=0, hour="*/6"),
            "options": {"queue": "default"},
        },
        "learning.analyze_behaviour": {
            "task": "learning.analyze_behaviour",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "heavy"},
        },
        "learning.generate_recommendations": {
            "task": "learning.generate_recommendations",
            "schedule": crontab(hour=3, minute=0),
            "options": {"queue": "heavy"},
        },
        "learning.generate_reflections": {
            "task": "learning.generate_reflections",
            "schedule": crontab(hour=4, minute=0, day_of_week="sunday"),
            "options": {"queue": "heavy"},
        },
        "learning.mark_completed_preparations": {
            "task": "learning.mark_completed_preparations",
            "schedule": crontab(hour=1, minute=0),
            "options": {"queue": "default"},
        },
        "learning.notification_delivery": {
            "task": "learning.notification_delivery",
            "schedule": 300.0,  # Every 5 minutes
            "options": {"queue": "default"},
        },
    }
