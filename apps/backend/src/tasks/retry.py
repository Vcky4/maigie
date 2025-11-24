"""Retry mechanism framework for Celery tasks.

This module provides retry strategies and utilities for handling task retries
with exponential backoff and configurable retry logic.

Usage:
    ```python
    from .tasks.retry import exponential_backoff, retry_on_exception

    @task(bind=True)
    @retry_on_exception(TransientError, max_retries=5)
    def my_task(self, data):
        # Task implementation
        pass
    ```
"""

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

from celery import Task

from ..exceptions import TaskRetryError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def exponential_backoff(retry_count: int, base_delay: int = 60, max_delay: int = 3600, multiplier: float = 2.0) -> int:
    """Calculate exponential backoff delay for retries.

    Args:
        retry_count: Current retry attempt number (0-indexed)
        base_delay: Base delay in seconds (default: 60)
        max_delay: Maximum delay in seconds (default: 3600)
        multiplier: Exponential multiplier (default: 2.0)

    Returns:
        Delay in seconds before next retry

    Example:
        ```python
        delay = exponential_backoff(retry_count=2)  # Returns 240 (60 * 2^2)
        ```
    """
    delay = int(base_delay * (multiplier ** retry_count))
    return min(delay, max_delay)


def linear_backoff(retry_count: int, base_delay: int = 60, max_delay: int = 3600, increment: int = 60) -> int:
    """Calculate linear backoff delay for retries.

    Args:
        retry_count: Current retry attempt number (0-indexed)
        base_delay: Base delay in seconds (default: 60)
        max_delay: Maximum delay in seconds (default: 3600)
        increment: Delay increment per retry (default: 60)

    Returns:
        Delay in seconds before next retry

    Example:
        ```python
        delay = linear_backoff(retry_count=2)  # Returns 180 (60 + 60*2)
        ```
    """
    delay = base_delay + (increment * retry_count)
    return min(delay, max_delay)


def retry_on_exception(
    exception_types: type[Exception] | tuple[type[Exception], ...],
    max_retries: int = 3,
    backoff_strategy: Callable[[int], int] = exponential_backoff,
    reraise: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to automatically retry tasks on specific exceptions.

    Args:
        exception_types: Exception type(s) to catch and retry
        max_retries: Maximum number of retries
        backoff_strategy: Function to calculate retry delay
        reraise: Whether to reraise exception after max retries

    Returns:
        Decorated function with retry logic

    Example:
        ```python
        @task(bind=True)
        @retry_on_exception((ConnectionError, TimeoutError), max_retries=5)
        def fetch_data(self, url: str):
            # Will retry on ConnectionError or TimeoutError
            response = requests.get(url)
            return response.json()
        ```
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(self: Task, *args: Any, **kwargs: Any) -> T:
            try:
                return func(self, *args, **kwargs)
            except exception_types as exc:
                retry_count = self.request.retries

                if retry_count >= max_retries:
                    error_msg = f"Task {self.name} exceeded max retries ({max_retries})"
                    logger.error(error_msg, exc_info=True)
                    if reraise:
                        raise
                    raise TaskRetryError(error_msg, task_id=self.request.id) from exc

                delay = backoff_strategy(retry_count)
                logger.warning(
                    f"Task {self.name} failed, retrying in {delay}s (attempt {retry_count + 1}/{max_retries})",
                    extra={"exception": str(exc), "retry_count": retry_count},
                )

                # Retry the task
                raise self.retry(exc=exc, countdown=delay)

        return wrapper

    return decorator


def retry_with_custom_delay(
    delay_calculator: Callable[[int, Exception], int],
    max_retries: int = 3,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to retry tasks with custom delay calculation.

    Args:
        delay_calculator: Function that takes (retry_count, exception) and returns delay
        max_retries: Maximum number of retries

    Returns:
        Decorated function with custom retry logic

    Example:
        ```python
        def custom_delay(retry_count, exc):
            if isinstance(exc, RateLimitError):
                return exc.retry_after
            return exponential_backoff(retry_count)

        @task(bind=True)
        @retry_with_custom_delay(custom_delay, max_retries=5)
        def api_call(self, endpoint):
            # Custom retry logic based on exception type
            pass
        ```
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(self: Task, *args: Any, **kwargs: Any) -> T:
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                retry_count = self.request.retries

                if retry_count >= max_retries:
                    error_msg = f"Task {self.name} exceeded max retries ({max_retries})"
                    logger.error(error_msg, exc_info=True)
                    raise TaskRetryError(error_msg, task_id=self.request.id) from exc

                delay = delay_calculator(retry_count, exc)
                logger.warning(
                    f"Task {self.name} failed, retrying in {delay}s (attempt {retry_count + 1}/{max_retries})",
                    extra={"exception": str(exc), "retry_count": retry_count},
                )

                raise self.retry(exc=exc, countdown=delay)

        return wrapper

    return decorator

