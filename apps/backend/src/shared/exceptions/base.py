"""
Unified exception hierarchy for the Maigie backend.

All domain-level errors should inherit from MaigieError. This provides
consistent error codes, HTTP status mapping, and structured responses.
"""

from typing import Any

from fastapi import status


class MaigieError(Exception):
    """Base exception for all Maigie application errors.

    Attributes:
        message: User-facing error message.
        status_code: HTTP status code.
        code: Machine-readable error code for clients.
        detail: Optional internal detail (hidden in production).
    """

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        code: str = "INTERNAL_ERROR",
        detail: str | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Common error types
# ---------------------------------------------------------------------------


class NotFoundError(MaigieError):
    """Resource not found (404)."""

    def __init__(self, resource: str, resource_id: str | None = None, detail: str | None = None):
        msg = f"{resource} not found" + (f": {resource_id}" if resource_id else "")
        super().__init__(msg, status.HTTP_404_NOT_FOUND, "NOT_FOUND", detail)


class ValidationError(MaigieError):
    """Request validation failed (422)."""

    def __init__(self, message: str = "Validation failed", detail: str | None = None):
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", detail)


class UnauthorizedError(MaigieError):
    """Authentication required (401)."""

    def __init__(self, message: str = "Authentication required", detail: str | None = None):
        super().__init__(message, status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", detail)


class ForbiddenError(MaigieError):
    """Insufficient permissions (403)."""

    def __init__(self, message: str = "Permission denied", detail: str | None = None):
        super().__init__(message, status.HTTP_403_FORBIDDEN, "FORBIDDEN", detail)


class ConflictError(MaigieError):
    """Resource conflict (409)."""

    def __init__(self, message: str = "Resource conflict", detail: str | None = None):
        super().__init__(message, status.HTTP_409_CONFLICT, "CONFLICT", detail)


class SubscriptionLimitError(MaigieError):
    """Feature requires a paid plan (403)."""

    def __init__(
        self,
        message: str = "This feature requires a paid plan",
        detail: str | None = None,
    ):
        super().__init__(message, status.HTTP_403_FORBIDDEN, "SUBSCRIPTION_LIMIT", detail)


class DeprecatedPlanError(MaigieError):
    """Deprecated plan referenced (410)."""

    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(message, status.HTTP_410_GONE, code, detail)


# ---------------------------------------------------------------------------
# Task-related errors (for Celery workers)
# ---------------------------------------------------------------------------


class TaskError(Exception):
    """Base exception for background task errors."""

    def __init__(
        self, message: str, task_id: str | None = None, details: dict[str, Any] | None = None
    ):
        self.message = message
        self.task_id = task_id
        self.details = details or {}
        super().__init__(self.message)


class TaskRetryError(TaskError):
    """Task should be retried."""

    def __init__(
        self, message: str = "Task should be retried", retry_after: int | None = None, **kwargs
    ):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class TaskFailedError(TaskError):
    """Task has permanently failed."""

    pass


class TaskTimeoutError(TaskError):
    """Task timed out."""

    def __init__(self, message: str = "Task timed out", timeout: int | None = None, **kwargs):
        self.timeout = timeout
        super().__init__(message, **kwargs)
