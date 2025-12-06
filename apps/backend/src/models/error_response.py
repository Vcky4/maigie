"""
Standardized error response models for consistent API error handling.

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

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """
    Standardized error response model for all API errors.

    This model ensures consistent error formatting across the entire API,
    making it easier for clients to handle errors predictably.

    Attributes:
        status_code: HTTP status code (e.g., 400, 403, 404, 500)
        code: Application-specific error code for programmatic handling
        message: User-friendly error message safe for display
        detail: Optional internal details for debugging (excluded in production)
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status_code": 403,
                "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
                "message": "This feature requires a Premium subscription",
                "detail": "User attempted to access Voice AI feature with Basic subscription",
            }
        }
    )

    status_code: int = Field(
        ...,
        description="HTTP status code",
        json_schema_extra={"example": 403},
    )

    code: str = Field(
        ...,
        description="Application-specific error code",
        json_schema_extra={"example": "SUBSCRIPTION_LIMIT_EXCEEDED"},
    )

    message: str = Field(
        ...,
        description="User-friendly error message",
        json_schema_extra={"example": "This feature requires a Premium subscription"},
    )

    detail: str | None = Field(
        None,
        description="Internal error details for debugging (may be excluded in production)",
        json_schema_extra={
            "example": "User attempted to access Voice AI feature with Basic subscription"
        },
    )
