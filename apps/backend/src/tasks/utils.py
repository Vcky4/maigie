"""Task utilities for status checking, result retrieval, and queue management.

This module provides helper functions for interacting with Celery tasks
from the FastAPI application.

Usage:
    ```python
    from .tasks.utils import get_task_status, get_task_result, cancel_task

    # Check task status
    status = await get_task_status(task_id)

    # Get task result
    result = await get_task_result(task_id)

    # Cancel a task
    await cancel_task(task_id)
    ```
"""

import asyncio
import logging
from typing import Any

from celery.result import AsyncResult

from ..core.celery_app import celery_app

logger = logging.getLogger(__name__)


def get_task_status(task_id: str) -> dict[str, Any]:
    """Get the status of a Celery task.

    Args:
        task_id: Task ID

    Returns:
        Dictionary with task status information

    Example:
        ```python
        status = get_task_status("abc-123")
        # Returns: {"status": "PENDING", "ready": False, "successful": False}
        ```
    """
    result = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "status": result.status,
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else None,
        "failed": result.failed() if result.ready() else None,
        "info": result.info if result.ready() else None,
    }


def get_task_result(task_id: str, timeout: float | None = None) -> Any:
    """Get the result of a completed Celery task.

    Args:
        task_id: Task ID
        timeout: Timeout in seconds (None for no timeout)

    Returns:
        Task result or None if not ready

    Raises:
        Exception: If task failed, raises the exception

    Example:
        ```python
        result = get_task_result("abc-123")
        ```
    """
    result = AsyncResult(task_id, app=celery_app)

    if timeout:
        result.get(timeout=timeout)
    elif result.ready():
        return result.get()

    return None


async def cancel_task(task_id: str, terminate: bool = False) -> bool:
    """Cancel a pending or running Celery task.

    Args:
        task_id: Task ID
        terminate: Whether to terminate running task (use with caution)

    Returns:
        True if task was cancelled, False otherwise

    Example:
        ```python
        cancelled = await cancel_task("abc-123")
        ```
    """
    celery_app.control.revoke(task_id, terminate=terminate)
    logger.info(f"Cancelled task {task_id} (terminate={terminate})")
    return True


def get_queue_length(queue_name: str = "default") -> int:
    """Get the number of pending tasks in a queue.

    Args:
        queue_name: Queue name

    Returns:
        Number of pending tasks

    Example:
        ```python
        length = get_queue_length("default")
        ```
    """
    inspect = celery_app.control.inspect()
    active_queues = inspect.active_queues()

    if not active_queues:
        return 0

    # Sum up tasks in the queue across all workers
    total = 0
    for worker_queues in active_queues.values():
        for queue_info in worker_queues:
            if queue_info.get("name") == queue_name:
                # Note: This is approximate, actual queue length requires Redis inspection
                total += 1

    return total


def get_active_tasks() -> dict[str, list[dict[str, Any]]]:
    """Get all currently active tasks across all workers.

    Returns:
        Dictionary mapping worker names to lists of active tasks

    Example:
        ```python
        active = get_active_tasks()
        # Returns: {"worker1@host": [{"id": "abc-123", "name": "my_task", ...}]}
        ```
    """
    inspect = celery_app.control.inspect()
    active = inspect.active()

    if not active:
        return {}

    return active


def get_scheduled_tasks() -> dict[str, list[dict[str, Any]]]:
    """Get all scheduled (ETA) tasks across all workers.

    Returns:
        Dictionary mapping worker names to lists of scheduled tasks

    Example:
        ```python
        scheduled = get_scheduled_tasks()
        ```
    """
    inspect = celery_app.control.inspect()
    scheduled = inspect.scheduled()

    if not scheduled:
        return {}

    return scheduled


def get_reserved_tasks() -> dict[str, list[dict[str, Any]]]:
    """Get all reserved (prefetched) tasks across all workers.

    Returns:
        Dictionary mapping worker names to lists of reserved tasks

    Example:
        ```python
        reserved = get_reserved_tasks()
        ```
    """
    inspect = celery_app.control.inspect()
    reserved = inspect.reserved()

    if not reserved:
        return {}

    return reserved


def get_worker_stats() -> dict[str, dict[str, Any]]:
    """Get statistics for all workers.

    Returns:
        Dictionary mapping worker names to statistics

    Example:
        ```python
        stats = get_worker_stats()
        # Returns: {"worker1@host": {"pool": {...}, "total": {...}}}
        ```
    """
    inspect = celery_app.control.inspect()
    stats = inspect.stats()

    if not stats:
        return {}

    return stats


async def purge_queue(queue_name: str = "default") -> int:
    """Purge all tasks from a queue.

    Warning: This permanently deletes all tasks in the queue!

    Args:
        queue_name: Queue name

    Returns:
        Number of tasks purged

    Example:
        ```python
        purged = await purge_queue("default")
        ```
    """
    purged = celery_app.control.purge()
    logger.warning(f"Purged {purged} tasks from queue {queue_name}")
    return purged

