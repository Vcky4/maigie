"""
Celery application factory for the new domain-driven architecture.

This is a thin wrapper that re-exports the existing celery_app instance
from core/, ensuring backward compatibility while task modules migrate
to the new domain-scoped structure.

Worker entrypoint: celery -A src.workers.celery_app:celery_app worker
"""

import sys


class _LazyModule(type(sys.modules[__name__])):
    """Lazy module that defers celery_app import to avoid circular dependency."""

    def __getattr__(self, name):
        if name == "celery_app":
            from src.core.celery_app import celery_app as _app

            return _app
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


sys.modules[__name__].__class__ = _LazyModule


def get_celery_app():
    """Lazy accessor to avoid circular import."""
    from src.core.celery_app import get_celery_app as _get

    return _get()
