"""
Custom exception classes and handlers.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base application exception."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppException):
    """Resource not found exception."""

    def __init__(self, resource: str, identifier: str | None = None):
        message = f"{resource} not found"
        if identifier:
            message += f": {identifier}"
        super().__init__(message, status_code=status.HTTP_404_NOT_FOUND)


class ValidationError(AppException):
    """Validation error exception."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class AuthenticationError(AppException):
    """Authentication error exception."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthorizationError(AppException):
    """Authorization error exception."""

    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(
            message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class TaskError(Exception):
    """Base exception for task-related errors."""

    def __init__(
        self, message: str, task_id: str | None = None, details: dict[str, Any] | None = None
    ):
        self.message = message
        self.task_id = task_id
        self.details = details or {}
        super().__init__(self.message)


class TaskRetryError(TaskError):
    """Exception indicating a task should be retried."""

    def __init__(
        self,
        message: str = "Task failed and should be retried",
        task_id: str | None = None,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.retry_after = retry_after
        super().__init__(message, task_id, details)


class TaskFailedError(TaskError):
    """Exception indicating a task has permanently failed."""

    def __init__(
        self,
        message: str = "Task failed permanently",
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, task_id, details)


class TaskTimeoutError(TaskError):
    """Exception indicating a task has timed out."""

    def __init__(
        self,
        message: str = "Task execution timed out",
        task_id: str | None = None,
        timeout: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.timeout = timeout
        super().__init__(message, task_id, details)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Global exception handler for AppException."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": exc.__class__.__name__,
                "details": exc.details,
            }
        },
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unhandled exceptions."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "message": "Internal server error",
                "type": "InternalServerError",
                "details": {},
            }
        },
    )
