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

from .celery_app import celery_app
from .manager import (
    check_worker_health,
    get_worker_info,
    get_worker_status,
    shutdown_worker,
)

__all__ = [
    "celery_app",
    "check_worker_health",
    "get_worker_status",
    "get_worker_info",
    "shutdown_worker",
]
