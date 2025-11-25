"""Background tasks package.

This package provides the infrastructure for background job processing using Celery.
All feature modules should use the patterns and utilities provided here when
implementing their specific background tasks.

Key Components:
- BaseTask: Base task class with error handling
- task: Decorator for creating tasks
- Retry mechanisms: Exponential backoff, custom retry strategies
- Failed job handling: Storage and retry of failed tasks
- Scheduled tasks: Periodic task registration
- Task registry: Task discovery and metadata
- Task utilities: Status checking, result retrieval, queue management
"""

from .base import BaseTask, retry_task, task
from .failure import (
    get_failed_task,
    handle_task_failure,
    list_failed_tasks,
    retry_failed_task,
    should_retry,
    store_failed_task,
)
from .registry import (
    discover_tasks,
    get_all_tasks,
    get_task_info,
    list_tasks,
    register_task,
)
from .retry import (
    exponential_backoff,
    linear_backoff,
    retry_on_exception,
    retry_with_custom_delay,
)
from .schedules import (
    DAILY_AT_8AM,
    DAILY_AT_MIDNIGHT,
    EVERY_15_MINUTES,
    EVERY_30_MINUTES,
    EVERY_5_MINUTES,
    HOURLY,
    get_periodic_tasks,
    register_periodic_task,
    unregister_periodic_task,
)
from .utils import (
    cancel_task,
    get_active_tasks,
    get_queue_length,
    get_reserved_tasks,
    get_scheduled_tasks,
    get_task_result,
    get_task_status,
    get_worker_stats,
    purge_queue,
)

# Import examples to register them (optional - remove if not needed)
try:
    from . import examples  # noqa: F401
except ImportError:
    pass  # Examples may not be available in all environments

__all__ = [
    # Base task
    "BaseTask",
    "task",
    "retry_task",
    # Retry mechanisms
    "exponential_backoff",
    "linear_backoff",
    "retry_on_exception",
    "retry_with_custom_delay",
    # Failed job handling
    "handle_task_failure",
    "store_failed_task",
    "get_failed_task",
    "list_failed_tasks",
    "retry_failed_task",
    "should_retry",
    # Scheduled tasks
    "register_periodic_task",
    "unregister_periodic_task",
    "get_periodic_tasks",
    "DAILY_AT_MIDNIGHT",
    "DAILY_AT_8AM",
    "HOURLY",
    "EVERY_5_MINUTES",
    "EVERY_15_MINUTES",
    "EVERY_30_MINUTES",
    # Task registry
    "register_task",
    "get_task_info",
    "list_tasks",
    "get_all_tasks",
    "discover_tasks",
    # Task utilities
    "get_task_status",
    "get_task_result",
    "cancel_task",
    "get_queue_length",
    "get_active_tasks",
    "get_scheduled_tasks",
    "get_reserved_tasks",
    "get_worker_stats",
    "purge_queue",
]
