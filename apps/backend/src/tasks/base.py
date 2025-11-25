"""Base task classes and decorators for Celery tasks.

This module provides base classes and decorators that establish consistent
patterns for creating background tasks. All feature modules should use these
patterns when implementing their specific tasks.

Usage:
    ```python
    from .tasks.base import BaseTask, task

    @task(name="my_task", max_retries=3)
    def my_background_task(arg1: str, arg2: int) -> dict:
        # Task implementation
        return {"result": "success"}
    ```
"""

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from celery import Task
from celery.exceptions import Retry

from ..core.celery_app import celery_app
from ..exceptions import TaskError, TaskRetryError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseTask(Task):
    """Base task class with common error handling and logging.

    All tasks should inherit from this class or use the @task decorator
    which automatically applies this base class.

    Features:
    - Automatic error logging
    - Consistent error handling
    - Task metadata tracking
    """

    def on_failure(self, exc: Exception, task_id: str, args: tuple, kwargs: dict[str, Any], einfo: Any) -> None:
        """Called when task fails.

        Args:
            exc: Exception that caused the failure
            task_id: Task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
            einfo: Exception info
        """
        logger.error(
            f"Task {self.name} (ID: {task_id}) failed",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "args": args,
                "kwargs": kwargs,
                "exception": str(exc),
                "exception_type": type(exc).__name__,
            },
            exc_info=True,
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval: Any, task_id: str, args: tuple, kwargs: dict[str, Any]) -> None:
        """Called when task succeeds.

        Args:
            retval: Task return value
            task_id: Task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
        """
        logger.info(
            f"Task {self.name} (ID: {task_id}) completed successfully",
            extra={
                "task_id": task_id,
                "task_name": self.name,
            },
        )
        super().on_success(retval, task_id, args, kwargs)

    def on_retry(self, exc: Exception, task_id: str, args: tuple, kwargs: dict[str, Any], einfo: Any) -> None:
        """Called when task is retried.

        Args:
            exc: Exception that triggered the retry
            task_id: Task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
            einfo: Exception info
        """
        logger.warning(
            f"Task {self.name} (ID: {task_id}) retrying",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "retry_count": self.request.retries,
                "max_retries": self.max_retries,
                "exception": str(exc),
            },
        )
        super().on_retry(exc, task_id, args, kwargs, einfo)


def task(
    name: str | None = None,
    base: type[Task] = BaseTask,
    bind: bool = False,
    max_retries: int = 3,
    default_retry_delay: int = 60,
    **kwargs: Any,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator factory for creating Celery tasks with consistent patterns.

    This decorator applies the BaseTask class and provides consistent
    configuration for all tasks in the application.

    Args:
        name: Task name (defaults to function name)
        base: Base task class (defaults to BaseTask)
        bind: Whether to bind task instance (for accessing self.request)
        max_retries: Maximum number of retry attempts
        default_retry_delay: Default delay between retries (seconds)
        **kwargs: Additional Celery task options

    Returns:
        Decorated function registered as Celery task

    Example:
        ```python
        @task(name="process_user_data", max_retries=5)
        def process_user(user_id: str):
            # Task implementation
            pass
        ```
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # Generate task name if not provided
        task_name = name or f"tasks.{func.__module__}.{func.__name__}"

        # Register task with Celery
        registered_task = celery_app.task(
            name=task_name,
            base=base,
            bind=bind,
            max_retries=max_retries,
            default_retry_delay=default_retry_delay,
            **kwargs,
        )(func)

        logger.debug(f"Registered task: {task_name}")
        return registered_task

    return decorator


def retry_task(
    task_instance: Task,
    exc: Exception | None = None,
    countdown: int | None = None,
    max_retries: int | None = None,
) -> None:
    """Helper function to retry a task with proper error handling.

    Args:
        task_instance: Bound task instance (from @task(bind=True))
        exc: Exception that triggered the retry
        countdown: Seconds to wait before retry (None for default)
        max_retries: Override max retries for this retry

    Raises:
        Retry: Celery retry exception
        TaskRetryError: If retry limit exceeded

    Example:
        ```python
        @task(bind=True, max_retries=3)
        def my_task(self, data):
            try:
                # Process data
                pass
            except TransientError as e:
                retry_task(self, exc=e, countdown=60)
        ```
    """
    if max_retries is None:
        max_retries = task_instance.max_retries

    if task_instance.request.retries >= max_retries:
        error_msg = f"Task {task_instance.name} exceeded max retries ({max_retries})"
        logger.error(error_msg)
        raise TaskRetryError(error_msg, task_id=task_instance.request.id)

    if countdown is None:
        countdown = task_instance.default_retry_delay

    raise Retry(exc=exc, countdown=countdown)

