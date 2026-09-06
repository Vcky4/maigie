"""Unified exception handling framework."""

from .base import (
    ConflictError,
    DeprecatedPlanError,
    ForbiddenError,
    MaigieError,
    NotFoundError,
    SubscriptionLimitError,
    TaskError,
    TaskFailedError,
    TaskRetryError,
    TaskTimeoutError,
    UnauthorizedError,
    ValidationError,
)
from .handlers import (
    maigie_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)

__all__ = [
    # Base
    "MaigieError",
    # HTTP errors
    "NotFoundError",
    "ValidationError",
    "UnauthorizedError",
    "ForbiddenError",
    "ConflictError",
    "SubscriptionLimitError",
    "DeprecatedPlanError",
    # Task errors
    "TaskError",
    "TaskRetryError",
    "TaskFailedError",
    "TaskTimeoutError",
    # Handlers
    "maigie_error_handler",
    "validation_error_handler",
    "unhandled_exception_handler",
]
