"""Failed job handling and dead letter queue support.

This module provides utilities for handling failed tasks, storing failure
information, and managing dead letter queues.

Usage:
    ```python
    from .tasks.failure import handle_task_failure, store_failed_task

    @task(bind=True, on_failure=handle_task_failure)
    def my_task(self, data):
        # Task implementation
        pass
    ```
"""

import json
import logging
from datetime import datetime
from typing import Any

from celery import Task

from ..core.cache import cache
from ..exceptions import TaskFailedError

logger = logging.getLogger(__name__)

FAILED_TASKS_KEY_PREFIX = "failed_tasks:"
DEAD_LETTER_QUEUE_KEY = "dead_letter_queue"


async def store_failed_task(
    task_id: str,
    task_name: str,
    exception: Exception,
    args: tuple,
    kwargs: dict[str, Any],
    traceback: str | None = None,
) -> None:
    """Store failed task information for later analysis.

    Args:
        task_id: Task ID
        task_name: Task name
        exception: Exception that caused failure
        args: Task positional arguments
        kwargs: Task keyword arguments
        traceback: Exception traceback (optional)
    """
    failure_info = {
        "task_id": task_id,
        "task_name": task_name,
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        "args": args,
        "kwargs": kwargs,
        "traceback": traceback,
        "failed_at": datetime.utcnow().isoformat(),
    }

    # Store in cache with expiration (7 days)
    key = f"{FAILED_TASKS_KEY_PREFIX}{task_id}"
    await cache.set(key, failure_info, expire=604800)  # 7 days

    # Add to dead letter queue list
    await cache.set(f"{DEAD_LETTER_QUEUE_KEY}:{task_id}", task_id, expire=604800)

    logger.error(
        f"Stored failed task {task_name} (ID: {task_id})",
        extra={"task_id": task_id, "task_name": task_name, "exception": str(exception)},
    )


async def get_failed_task(task_id: str) -> dict[str, Any] | None:
    """Retrieve failed task information.

    Args:
        task_id: Task ID

    Returns:
        Failure information dictionary or None if not found
    """
    key = f"{FAILED_TASKS_KEY_PREFIX}{task_id}"
    return await cache.get(key)


async def list_failed_tasks(limit: int = 100) -> list[dict[str, Any]]:
    """List recent failed tasks.

    Args:
        limit: Maximum number of tasks to return

    Returns:
        List of failed task information dictionaries
    """
    # Get all failed task IDs from dead letter queue
    pattern = f"{DEAD_LETTER_QUEUE_KEY}:*"
    task_ids = await cache.keys(pattern)

    # Remove prefix to get actual task IDs
    task_ids = [tid.replace(f"{DEAD_LETTER_QUEUE_KEY}:", "") for tid in task_ids]

    # Retrieve failure info for each task
    failed_tasks = []
    for task_id in task_ids[:limit]:
        failure_info = await get_failed_task(task_id)
        if failure_info:
            failed_tasks.append(failure_info)

    # Sort by failed_at (most recent first)
    failed_tasks.sort(key=lambda x: x.get("failed_at", ""), reverse=True)

    return failed_tasks


async def retry_failed_task(task_id: str, celery_app: Any) -> bool:
    """Retry a previously failed task.

    Args:
        task_id: Task ID of failed task
        celery_app: Celery application instance

    Returns:
        True if task was retried, False otherwise
    """
    failure_info = await get_failed_task(task_id)
    if not failure_info:
        logger.warning(f"Failed task {task_id} not found")
        return False

    try:
        # Retry the task with original arguments
        celery_app.send_task(
            failure_info["task_name"],
            args=failure_info["args"],
            kwargs=failure_info["kwargs"],
        )
        logger.info(f"Retried failed task {task_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to retry task {task_id}: {e}")
        return False


def handle_task_failure(
    task: Task,
    task_id: str,
    exc: Exception,
    args: tuple,
    kwargs: dict[str, Any],
    einfo: Any,
) -> None:
    """Default failure handler for tasks.

    This function is called automatically when a task fails. It stores
    failure information and logs the error.

    Args:
        task: Task instance
        task_id: Task ID
        exc: Exception that caused failure
        args: Task positional arguments
        kwargs: Task keyword arguments
        einfo: Exception info object
    """
    import asyncio

    # Get traceback if available
    traceback_str = None
    if einfo:
        import traceback
        traceback_str = "".join(traceback.format_exception(type(exc), exc, einfo.traceback))

    # Store failure information (async operation)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, schedule as a task
            asyncio.create_task(
                store_failed_task(
                    task_id=task_id,
                    task_name=task.name,
                    exception=exc,
                    args=args,
                    kwargs=kwargs,
                    traceback=traceback_str,
                )
            )
        else:
            loop.run_until_complete(
                store_failed_task(
                    task_id=task_id,
                    task_name=task.name,
                    exception=exc,
                    args=args,
                    kwargs=kwargs,
                    traceback=traceback_str,
                )
            )
    except Exception as e:
        logger.error(f"Failed to store task failure information: {e}")

    logger.error(
        f"Task {task.name} (ID: {task_id}) failed permanently",
        extra={
            "task_id": task_id,
            "task_name": task.name,
            "exception": str(exc),
            "exception_type": type(exc).__name__,
        },
        exc_info=True,
    )


def should_retry(exception: Exception, max_retries: int, current_retries: int) -> bool:
    """Determine if a task should be retried based on exception type.

    Args:
        exception: Exception that occurred
        max_retries: Maximum number of retries allowed
        current_retries: Current retry count

    Returns:
        True if task should be retried, False otherwise
    """
    # Don't retry if max retries exceeded
    if current_retries >= max_retries:
        return False

    # Don't retry on certain exception types
    non_retryable_exceptions = (TaskFailedError, KeyboardInterrupt, SystemExit)
    if isinstance(exception, non_retryable_exceptions):
        return False

    # Retry on transient errors
    retryable_exceptions = (ConnectionError, TimeoutError, OSError)
    if isinstance(exception, retryable_exceptions):
        return True

    # Default: don't retry (let task decorator handle it)
    return False

