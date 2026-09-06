"""
Background workers — domain-scoped Celery tasks.

Task modules are organized by domain concern:
- intelligence_tasks: AI course gen, schedule gen, recommendations
- notification_tasks: Email + push notifications
- progress_tasks: Spaced repetition, streaks, credit resets
- billing_tasks: Subscription lifecycle, account deletions

Worker entrypoint:
    celery -A src.workers.celery_app:celery_app worker -Q default,heavy

Legacy entrypoint (still works):
    celery -A src.core.celery_app:celery_app worker
"""

from .manager import (
    check_worker_health,
    get_worker_info,
    get_worker_status,
    shutdown_worker,
)


def __getattr__(name):
    """Lazy import celery_app to break the circular dependency chain."""
    if name == "celery_app":
        from src.core.celery_app import celery_app as _app

        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "celery_app",
    "check_worker_health",
    "get_worker_status",
    "get_worker_info",
    "shutdown_worker",
]
