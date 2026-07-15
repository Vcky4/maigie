"""
Celery application factory for the new domain-driven architecture.

This is a thin wrapper that re-exports the existing celery_app instance
from core/, ensuring backward compatibility while task modules migrate
to the new domain-scoped structure.

Worker entrypoint: celery -A src.workers.celery_app:celery_app worker
"""

# Re-export the global Celery instance
from src.core.celery_app import celery_app, get_celery_app

__all__ = ["celery_app", "get_celery_app"]
