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
    behaviour,
    daily_plan,
    daily_snapshots,
    engagement,
    plan_redistribution,
    prep_result,
    preparation,
    readiness_snapshots,
    recommendations,
    reflections,
    review_cards,
    study_plan_check_ins,
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
        # 01:30, after the review sweep at 01:00 and before the snapshots at 01:15 would clash — a
        # preparation moved into review tonight has no outcome yet, so the two cannot contend for the same
        # rows, but ordering them keeps the logs readable.
        "learning.remind_prep_results": {
            "task": "learning.remind_prep_results",
            "schedule": crontab(hour=1, minute=30),
            "options": {"queue": "default"},
        },
        # Just after midnight, so a day's row reflects that day's finishing state.
        # Idempotent on (prepId, capturedOn), so a retry updates rather than
        # duplicating the day.
        "learning.capture_readiness_snapshots": {
            "task": "learning.capture_readiness_snapshots",
            "schedule": crontab(hour=0, minute=30),
            "options": {"queue": "heavy"},
        },
        # Records each learner's last *finished* local day, so the hour only has to be one
        # nobody else is using — 01:00 is taken by mark_completed_preparations. Which day gets
        # written is decided per learner from their own timezone rather than by this clock,
        # because a single UTC run cannot be "end of day" everywhere at once.
        "learning.capture_daily_snapshots": {
            "task": "learning.capture_daily_snapshots",
            "schedule": crontab(hour=1, minute=15),
            "options": {"queue": "heavy"},
        },
        # Daily rather than weekly. The task asks "which plans have not been checked in
        # for seven days", from a stored timestamp, so a daily sweep gives each learner a
        # check-in seven days after their own last one instead of herding everyone onto
        # one morning — and a day the scheduler missed is picked up the next day rather
        # than a week later.
        "learning.study_plan_check_ins": {
            "task": "learning.study_plan_check_ins",
            "schedule": crontab(hour=7, minute=0),
            "options": {"queue": "default"},
        },
        # 05:00, deliberately an hour before `prepare_daily_plan`. The daily plan reads what is
        # scheduled for today, so repacking first means the learner's morning is built from the
        # corrected dates instead of ones that were about to move. Daily rather than weekly for the
        # same reason as the check-ins: the cooldown lives on the plan
        # (`lastRedistributedAt`), so each plan is reconsidered a week after its own last repack
        # rather than the whole fleet being swept on one morning.
        "learning.redistribute_drifted_plans": {
            "task": "learning.redistribute_drifted_plans",
            "schedule": crontab(hour=5, minute=0),
            "options": {"queue": "default"},
        },
    }
