"""Celery application factory for background task processing.

This module provides a production-ready Celery application configured with
Redis as the message broker and result backend. It integrates seamlessly with
the FastAPI application lifecycle and follows the same patterns as cache.py.

Usage:
    ```python
    from .core.celery_app import celery_app

    @celery_app.task
    def my_background_task(arg1, arg2):
        # Task implementation
        return result
    ```
"""

import logging
from typing import Any

from celery import Celery
from celery.signals import setup_logging

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Create and configure Celery application.

    Args:
        settings: Application settings. If None, will fetch from get_settings().

    Returns:
        Configured Celery application instance
    """
    if settings is None:
        settings = get_settings()

    # Create Celery app
    celery_app = Celery(
        "maigie",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )

    # Configure Celery
    celery_app.conf.update(
        task_serializer=settings.CELERY_TASK_SERIALIZER,
        accept_content=settings.CELERY_ACCEPT_CONTENT,
        result_serializer=settings.CELERY_RESULT_SERIALIZER,
        timezone=settings.CELERY_TIMEZONE,
        enable_utc=settings.CELERY_ENABLE_UTC,
        task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
        task_acks_late=settings.CELERY_TASK_ACKS_LATE,
        task_reject_on_worker_lost=settings.CELERY_TASK_REJECT_ON_WORKER_LOST,
        worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
        task_default_queue=settings.CELERY_TASK_DEFAULT_QUEUE,
        task_default_exchange=settings.CELERY_TASK_DEFAULT_EXCHANGE,
        task_default_routing_key=settings.CELERY_TASK_DEFAULT_ROUTING_KEY,
        result_expires=settings.CELERY_RESULT_EXPIRES,
        # Task routing
        task_routes={
            "tasks.*": {"queue": "default"},
        },
        # Task time limits
        task_time_limit=300,  # Hard time limit (5 minutes)
        task_soft_time_limit=240,  # Soft time limit (4 minutes)
        # Worker settings
        worker_max_tasks_per_child=1000,  # Restart worker after N tasks
        worker_disable_rate_limits=False,
    )

    # Configure logging
    @setup_logging.connect
    def config_loggers(*args: Any, **kwargs: Any) -> None:
        """Configure Celery logging."""
        # Celery will use the root logger configuration
        pass

    logger.info("Celery application configured successfully")
    return celery_app


# Global Celery app instance
celery_app = create_celery_app()


def get_celery_app() -> Celery:
    """Get Celery application instance for dependency injection.

    Returns:
        Global Celery application instance
    """
    return celery_app

