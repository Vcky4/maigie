"""Scheduled task infrastructure for periodic tasks.

This module provides utilities for defining and managing scheduled tasks
using Celery Beat. All periodic tasks should be registered here.

Usage:
    ```python
    from .tasks.schedules import register_periodic_task
    from celery.schedules import crontab

    @register_periodic_task(
        name="daily_digest",
        schedule=crontab(hour=8, minute=0),  # Run daily at 8 AM
    )
    def send_daily_digest():
        # Task implementation
        pass
    ```
"""

import logging
from typing import Any, Callable

from celery.schedules import crontab, schedule
from celery.utils.log import get_task_logger

from ..core.celery_app import celery_app

logger = logging.getLogger(__name__)


def register_periodic_task(
    name: str,
    schedule: crontab | schedule,
    task: Callable | str | None = None,
    args: tuple = (),
    kwargs: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> Callable | None:
    """Register a periodic task with Celery Beat.

    Args:
        name: Unique name for the periodic task
        schedule: Celery schedule object (crontab, schedule, etc.)
        task: Task function or task name string
        args: Positional arguments to pass to task
        kwargs: Keyword arguments to pass to task
        options: Additional task options

    Returns:
        The task function if provided, None otherwise

    Example:
        ```python
        from celery.schedules import crontab

        @register_periodic_task(
            name="cleanup_old_data",
            schedule=crontab(hour=2, minute=0),  # Daily at 2 AM
        )
        def cleanup_task():
            # Cleanup implementation
            pass
        ```
    """
    if kwargs is None:
        kwargs = {}
    if options is None:
        options = {}

    # Register with Celery Beat
    celery_app.conf.beat_schedule[name] = {
        "task": task if isinstance(task, str) else (task.__name__ if task else name),
        "schedule": schedule,
        "args": args,
        "kwargs": kwargs,
        "options": options,
    }

    logger.info(f"Registered periodic task: {name} with schedule {schedule}")

    return task


def unregister_periodic_task(name: str) -> None:
    """Unregister a periodic task.

    Args:
        name: Name of the periodic task to unregister
    """
    if name in celery_app.conf.beat_schedule:
        del celery_app.conf.beat_schedule[name]
        logger.info(f"Unregistered periodic task: {name}")
    else:
        logger.warning(f"Periodic task {name} not found")


def get_periodic_tasks() -> dict[str, dict[str, Any]]:
    """Get all registered periodic tasks.

    Returns:
        Dictionary of periodic task configurations
    """
    return celery_app.conf.beat_schedule.copy()


# Common schedule presets
DAILY_AT_MIDNIGHT = crontab(hour=0, minute=0)
DAILY_AT_8AM = crontab(hour=8, minute=0)
HOURLY = schedule(run_every=3600)  # Every hour
EVERY_5_MINUTES = schedule(run_every=300)  # Every 5 minutes
EVERY_15_MINUTES = schedule(run_every=900)  # Every 15 minutes
EVERY_30_MINUTES = schedule(run_every=1800)  # Every 30 minutes

