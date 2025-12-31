"""
Custom business logic exceptions for Maigie application.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from typing import Optional

from fastapi import status


class MaigieError(Exception):
    """
    Base exception class for all Maigie application errors.

    All custom business logic exceptions should inherit from this class.
    This allows for centralized exception handling and consistent error responses.

    Attributes:
        message: User-friendly error message
        status_code: HTTP status code for the error
        code: Application-specific error code for programmatic handling
        detail: Optional internal details for debugging
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        code: str,
        detail: str | None = None,
    ):
        """
        Initialize a Maigie error.

        Args:
            message: User-friendly error message
            status_code: HTTP status code
            code: Application-specific error code
            detail: Optional internal details
        """
        self.message = message
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(self.message)


class SubscriptionLimitError(MaigieError):
    """
    Raised when a Basic User attempts to access a Premium feature.

    Examples of Premium features:
    - Voice AI sessions
    - Unlimited courses (Basic users limited to 5 courses)
    - Advanced AI features
    - Priority support

    HTTP Status: 403 Forbidden
    Error Code: SUBSCRIPTION_LIMIT_EXCEEDED
    """

    def __init__(
        self,
        message: str = "This feature requires a Premium subscription",
        detail: str | None = None,
    ):
        """
        Initialize a subscription limit error.

        Args:
            message: User-friendly error message
            detail: Optional internal details (e.g., which feature was attempted)
        """
        super().__init__(
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            code="SUBSCRIPTION_LIMIT_EXCEEDED",
            detail=detail,
        )


class ResourceNotFoundError(MaigieError):
    """
    Raised when a requested resource is not found.

    This applies to any database entity that cannot be located:
    - Course not found
    - Goal not found
    - Schedule block not found
    - User not found
    - etc.

    HTTP Status: 404 Not Found
    Error Code: RESOURCE_NOT_FOUND
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: str | None = None,
        detail: str | None = None,
    ):
        """
        Initialize a resource not found error.

        Args:
            resource_type: Type of resource (e.g., "Course", "Goal", "Schedule")
            resource_id: Optional ID of the resource that wasn't found
            detail: Optional internal details
        """
        if resource_id:
            message = f"{resource_type} with ID '{resource_id}' not found"
        else:
            message = f"{resource_type} not found"

        super().__init__(
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            code="RESOURCE_NOT_FOUND",
            detail=detail,
        )


class InternalServerError(MaigieError):
    """
    A catch-all exception for unexpected failures.

    This should be used when an unexpected error occurs that doesn't
    fit into other specific error categories. The actual error details
    should be logged server-side, but only a generic message is sent
    to the client.

    HTTP Status: 500 Internal Server Error
    Error Code: INTERNAL_SERVER_ERROR
    """

    def __init__(
        self,
        message: str = "An internal server error occurred",
        detail: str | None = None,
    ):
        """
        Initialize an internal server error.

        Args:
            message: User-friendly error message (keep generic)
            detail: Internal details for logging (not sent to client in production)
        """
        super().__init__(
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_SERVER_ERROR",
            detail=detail,
        )


class UnauthorizedError(MaigieError):
    """
    Raised when authentication is required but not provided or invalid.

    HTTP Status: 401 Unauthorized
    Error Code: UNAUTHORIZED
    """

    def __init__(
        self,
        message: str = "Authentication required",
        detail: str | None = None,
    ):
        """Initialize an unauthorized error."""
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            detail=detail,
        )


class ForbiddenError(MaigieError):
    """
    Raised when user is authenticated but lacks permission for the action.

    HTTP Status: 403 Forbidden
    Error Code: FORBIDDEN
    """

    def __init__(
        self,
        message: str = "You don't have permission to perform this action",
        detail: str | None = None,
    ):
        """Initialize a forbidden error."""
        super().__init__(
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            detail=detail,
        )


class ValidationError(MaigieError):
    """
    Raised when request data validation fails.

    HTTP Status: 422 Unprocessable Entity
    Error Code: VALIDATION_ERROR
    """

    def __init__(
        self,
        message: str = "Request validation failed",
        detail: str | None = None,
    ):
        """Initialize a validation error."""
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            detail=detail,
        )
