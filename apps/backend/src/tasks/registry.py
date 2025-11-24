"""Task registry and auto-discovery utilities.

This module provides task registration and discovery mechanisms to help
organize and manage tasks across the application.

Usage:
    ```python
    from .tasks.registry import register_task, get_task_info

    @register_task(name="my_task", description="Processes user data")
    @task(name="my_task")
    def process_user(user_id: str):
        pass

    # Later, get task info
    info = get_task_info("my_task")
    ```
"""

import logging
from typing import Any

from ..core.celery_app import celery_app

logger = logging.getLogger(__name__)

# Task registry: {task_name: {metadata}}
_task_registry: dict[str, dict[str, Any]] = {}


def register_task(
    name: str,
    description: str = "",
    category: str = "default",
    tags: list[str] | None = None,
    **metadata: Any,
) -> Any:
    """Decorator to register task metadata.

    Args:
        name: Task name (must match Celery task name)
        description: Task description
        category: Task category (e.g., "email", "indexing", "cleanup")
        tags: List of tags for categorization
        **metadata: Additional metadata

    Returns:
        Decorator function

    Example:
        ```python
        @register_task(
            name="send_email",
            description="Sends email notification",
            category="email",
            tags=["notification", "user"]
        )
        @task(name="send_email")
        def send_email_task(recipient: str, subject: str):
            pass
        ```
    """
    def decorator(func: Any) -> Any:
        _task_registry[name] = {
            "name": name,
            "description": description,
            "category": category,
            "tags": tags or [],
            "function": func.__name__,
            "module": func.__module__,
            **metadata,
        }
        logger.debug(f"Registered task metadata: {name}")
        return func

    return decorator


def get_task_info(task_name: str) -> dict[str, Any] | None:
    """Get metadata for a registered task.

    Args:
        task_name: Task name

    Returns:
        Task metadata dictionary or None if not found
    """
    return _task_registry.get(task_name)


def list_tasks(category: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    """List all registered tasks, optionally filtered.

    Args:
        category: Filter by category
        tag: Filter by tag

    Returns:
        List of task metadata dictionaries
    """
    tasks = list(_task_registry.values())

    if category:
        tasks = [t for t in tasks if t.get("category") == category]

    if tag:
        tasks = [t for t in tasks if tag in t.get("tags", [])]

    return tasks


def get_all_tasks() -> dict[str, dict[str, Any]]:
    """Get all registered tasks.

    Returns:
        Dictionary mapping task names to metadata
    """
    return _task_registry.copy()


def discover_tasks() -> dict[str, Any]:
    """Discover all Celery tasks in the application.

    Returns:
        Dictionary of discovered tasks with their configurations
    """
    discovered = {}
    for task_name, task in celery_app.tasks.items():
        if not task_name.startswith("celery."):  # Skip internal Celery tasks
            discovered[task_name] = {
                "name": task_name,
                "registered": task_name in _task_registry,
                "metadata": _task_registry.get(task_name),
            }
    return discovered

