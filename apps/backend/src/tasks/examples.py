"""Example task implementations demonstrating framework usage patterns.

These examples show how to use the background workers framework to create
tasks with retry logic, error handling, and scheduled execution. Feature
modules should use these patterns when implementing their specific tasks.

Note: These are infrastructure examples only, not feature implementations.
Specific feature tasks (reminder dispatcher, schedule generator, etc.) belong
in their respective feature modules.
"""

import logging
from typing import Any

from celery.schedules import crontab

from .base import task
from .failure import handle_task_failure
from .registry import register_task
from .retry import exponential_backoff, retry_on_exception
from .schedules import register_periodic_task

logger = logging.getLogger(__name__)


# Example 1: Basic task with retry
@register_task(
    name="example.basic_task",
    description="Example basic task with automatic retry",
    category="example",
    tags=["example", "basic"],
)
@task(name="example.basic_task", max_retries=3, bind=True)
def example_basic_task(self: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Example task demonstrating basic usage.

    Args:
        self: Bound task instance
        data: Task data

    Returns:
        Task result

    Example:
        ```python
        from .tasks.examples import example_basic_task
        result = example_basic_task.delay({"key": "value"})
        ```
    """
    logger.info(f"Processing basic task with data: {data}")
    # Simulate work
    return {"status": "completed", "processed": data}


# Example 2: Task with retry on specific exceptions
@register_task(
    name="example.retry_task",
    description="Example task with retry on connection errors",
    category="example",
    tags=["example", "retry"],
)
@task(name="example.retry_task", max_retries=5, bind=True)
@retry_on_exception((ConnectionError, TimeoutError), max_retries=5)
def example_retry_task(self: Any, url: str) -> dict[str, Any]:
    """Example task demonstrating retry on specific exceptions.

    Args:
        self: Bound task instance
        url: URL to process

    Returns:
        Task result

    Example:
        ```python
        from .tasks.examples import example_retry_task
        result = example_retry_task.delay("https://example.com")
        ```
    """
    logger.info(f"Fetching data from {url}")
    # Simulate network operation that might fail
    # In real implementation, this would make HTTP request
    return {"url": url, "status": "success"}


# Example 3: Task with custom retry delay
@register_task(
    name="example.custom_retry_task",
    description="Example task with custom retry delay",
    category="example",
    tags=["example", "custom-retry"],
)
@task(name="example.custom_retry_task", max_retries=3, bind=True, default_retry_delay=120)
def example_custom_retry_task(self: Any, data: str) -> dict[str, Any]:
    """Example task demonstrating custom retry delay.

    Args:
        self: Bound task instance
        data: Task data

    Returns:
        Task result
    """
    try:
        logger.info(f"Processing {data}")
        # Simulate work that might fail
        if data == "fail":
            raise ValueError("Simulated failure")
        return {"status": "success", "data": data}
    except ValueError as e:
        # Retry with exponential backoff
        delay = exponential_backoff(self.request.retries, base_delay=60)
        logger.warning(f"Task failed, retrying in {delay}s")
        raise self.retry(exc=e, countdown=delay)


# Example 4: Scheduled periodic task
@register_periodic_task(
    name="example.daily_cleanup",
    schedule=crontab(hour=2, minute=0),  # Daily at 2 AM
)
@register_task(
    name="example.daily_cleanup",
    description="Example scheduled task running daily",
    category="example",
    tags=["example", "scheduled", "cleanup"],
)
@task(name="example.daily_cleanup", on_failure=handle_task_failure)
def example_daily_cleanup() -> dict[str, Any]:
    """Example scheduled task demonstrating periodic execution.

    This task runs daily at 2 AM UTC.

    Returns:
        Task result

    Example:
        This task is automatically scheduled by Celery Beat.
        To run manually:
        ```python
        from .tasks.examples import example_daily_cleanup
        result = example_daily_cleanup.delay()
        ```
    """
    logger.info("Running daily cleanup task")
    # Simulate cleanup work
    return {"status": "completed", "cleaned": "example_data"}


# Example 5: Task with error handling
@register_task(
    name="example.error_handling_task",
    description="Example task demonstrating error handling",
    category="example",
    tags=["example", "error-handling"],
)
@task(name="example.error_handling_task", bind=True, on_failure=handle_task_failure)
def example_error_handling_task(self: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Example task demonstrating comprehensive error handling.

    Args:
        self: Bound task instance
        data: Task data

    Returns:
        Task result

    Raises:
        ValueError: For invalid data
    """
    try:
        if not data:
            raise ValueError("Data is required")

        logger.info(f"Processing data: {data}")
        # Simulate work
        return {"status": "success", "processed": data}
    except ValueError as e:
        logger.error(f"Task failed with validation error: {e}")
        # Don't retry validation errors
        raise
    except Exception as e:
        logger.error(f"Task failed with unexpected error: {e}")
        # Retry on unexpected errors
        raise self.retry(exc=e, countdown=60)

