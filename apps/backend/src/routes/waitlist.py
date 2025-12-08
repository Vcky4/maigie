"""
Waitlist routes for Brevo integration.

Copyright (C) 2024 Maigie Team

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

import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator

from ..config import get_settings
from ..dependencies import SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/waitlist", tags=["waitlist"])

BREVO_API_URL = "https://api.brevo.com/v3/contacts"


class WaitlistRequest(BaseModel):
    """Request model for waitlist signup."""

    email: EmailStr

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        """Validate email format."""
        if not v or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
            raise ValueError("Invalid email address format")
        return v.lower().strip()


class WaitlistResponse(BaseModel):
    """Response model for waitlist signup."""

    success: bool
    contact_id: int | None = None
    message: str


@router.post("/signup", response_model=WaitlistResponse, status_code=status.HTTP_201_CREATED)
async def signup_waitlist(
    request: WaitlistRequest,
    settings: SettingsDep = None,
) -> WaitlistResponse:
    """
    Add email to waitlist via Brevo CRM.

    This endpoint handles the creation of contacts in Brevo (formerly Sendinblue).
    The API key is stored securely on the backend and never exposed to the frontend.

    Args:
        request: WaitlistRequest containing the email address
        settings: Application settings (injected dependency)

    Returns:
        WaitlistResponse indicating success or failure

    Raises:
        HTTPException: If Brevo integration is disabled or API call fails
    """
    if settings is None:
        settings = get_settings()

    # Check if Brevo integration is enabled
    if not settings.BREVO_ENABLED:
        logger.warning("Brevo integration is disabled")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist service is currently unavailable",
        )

    # Check if API key is configured
    if not settings.BREVO_API_KEY:
        logger.error("Brevo API key is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist service is not properly configured",
        )

    # Validate email format (Pydantic already does this, but double-check)
    email = request.email.lower().strip()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address format",
        )

    try:
        # Make request to Brevo API
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                BREVO_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "api-key": settings.BREVO_API_KEY,
                },
                json={"email": email},
            )

            # Handle successful creation (201) or contact already exists (204)
            if response.status_code == 201:
                data = response.json()
                contact_id = data.get("id")
                logger.info(f"Contact created successfully in Brevo: {email} (ID: {contact_id})")
                return WaitlistResponse(
                    success=True,
                    contact_id=contact_id,
                    message="Successfully added to waitlist",
                )

            if response.status_code == 204:
                # Contact already exists
                logger.info(f"Contact already exists in Brevo: {email}")
                return WaitlistResponse(
                    success=True,
                    contact_id=None,
                    message="Already on the waitlist",
                )

            # Handle errors
            if response.status_code == 400:
                error_data = response.json()
                error_message = error_data.get("message", "Invalid request")
                logger.error(f"Brevo API error (400): {error_message}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid request: {error_message}",
                )

            if response.status_code in (401, 403):
                logger.error(f"Brevo API authentication error: {response.status_code}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Waitlist service authentication failed",
                )

            # Handle other errors
            error_text = response.text
            logger.error(f"Brevo API error ({response.status_code}): {error_text}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to add to waitlist. Please try again later.",
            )

    except httpx.TimeoutException:
        logger.error(f"Timeout while creating contact in Brevo: {email}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Waitlist service timeout. Please try again later.",
        )
    except httpx.RequestError as e:
        logger.error(f"Network error while creating contact in Brevo: {email}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to connect to waitlist service. Please try again later.",
        )
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error while creating contact in Brevo: {email}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again later.",
        )
