"""Standardized error response models for consistent API error handling."""

from typing import Optional

from pydantic import BaseModel, Field


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
    
    status_code: int = Field(
        ...,
        description="HTTP status code",
        example=403,
    )
    
    code: str = Field(
        ...,
        description="Application-specific error code",
        example="SUBSCRIPTION_LIMIT_EXCEEDED",
    )
    
    message: str = Field(
        ...,
        description="User-friendly error message",
        example="This feature requires a Premium subscription",
    )
    
    detail: Optional[str] = Field(
        None,
        description="Internal error details for debugging (may be excluded in production)",
        example="User attempted to access Voice AI feature with Basic subscription",
    )
    
    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "status_code": 403,
                "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
                "message": "This feature requires a Premium subscription",
                "detail": "User attempted to access Voice AI feature with Basic subscription"
            }
        }

