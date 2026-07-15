"""DEPRECATED — Re-exports from src.shared.exceptions for backward compatibility.

Legacy services import from here. New code should import from src.shared.exceptions directly.
"""

from src.shared.exceptions import (
    MaigieError as AppException,
    NotFoundError,
    ValidationError,
    UnauthorizedError as AuthenticationError,
    ForbiddenError as AuthorizationError,
    TaskError,
    TaskRetryError,
    TaskFailedError,
    TaskTimeoutError,
    maigie_error_handler as app_exception_handler,
    unhandled_exception_handler as general_exception_handler,
)

__all__ = [
    "AppException",
    "NotFoundError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "TaskError",
    "TaskRetryError",
    "TaskFailedError",
    "TaskTimeoutError",
    "app_exception_handler",
    "general_exception_handler",
]
