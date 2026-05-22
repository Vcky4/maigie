"""
Device token registration routes for push notifications (FCM).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from prisma import Client as PrismaClient
from src.dependencies import CurrentUser
from src.utils.dependencies import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/device-tokens", tags=["device-tokens"])


class RegisterTokenRequest(BaseModel):
    """Request body for registering a device token."""

    token: str = Field(..., min_length=1, description="FCM registration token")
    platform: str = Field(..., pattern="^(ANDROID|IOS|WEB)$", description="Device platform")
    device_id: str | None = Field(None, description="Optional device identifier for deduplication")


class RegisterTokenResponse(BaseModel):
    """Response after registering a device token."""

    id: str
    token: str
    platform: str
    is_active: bool
    message: str


class UnregisterTokenRequest(BaseModel):
    """Request body for unregistering a device token."""

    token: str = Field(..., min_length=1, description="FCM registration token to remove")


@router.post("", response_model=RegisterTokenResponse, status_code=status.HTTP_201_CREATED)
async def register_device_token(
    body: RegisterTokenRequest,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Register a device token for push notifications.

    If the token already exists for this user, it will be reactivated.
    If the token exists for a different user, it will be reassigned
    (a device can only belong to one user at a time).
    """
    try:
        # Check if token already exists
        existing = await db.devicetoken.find_unique(where={"token": body.token})

        if existing:
            if existing.userId == current_user.id:
                # Same user — just reactivate if needed
                if not existing.isActive:
                    updated = await db.devicetoken.update(
                        where={"token": body.token},
                        data={"isActive": True, "platform": body.platform},
                    )
                    return RegisterTokenResponse(
                        id=updated.id,
                        token=updated.token,
                        platform=str(updated.platform),
                        is_active=updated.isActive,
                        message="Device token reactivated",
                    )
                return RegisterTokenResponse(
                    id=existing.id,
                    token=existing.token,
                    platform=str(existing.platform),
                    is_active=existing.isActive,
                    message="Device token already registered",
                )
            else:
                # Different user — reassign token (device changed hands / re-login)
                updated = await db.devicetoken.update(
                    where={"token": body.token},
                    data={
                        "userId": current_user.id,
                        "platform": body.platform,
                        "deviceId": body.device_id,
                        "isActive": True,
                    },
                )
                return RegisterTokenResponse(
                    id=updated.id,
                    token=updated.token,
                    platform=str(updated.platform),
                    is_active=updated.isActive,
                    message="Device token reassigned to current user",
                )

        # Create new token
        device_token = await db.devicetoken.create(
            data={
                "userId": current_user.id,
                "token": body.token,
                "platform": body.platform,
                "deviceId": body.device_id,
                "isActive": True,
            }
        )

        logger.info(
            f"Device token registered for user {current_user.id} " f"(platform={body.platform})"
        )

        return RegisterTokenResponse(
            id=device_token.id,
            token=device_token.token,
            platform=str(device_token.platform),
            is_active=device_token.isActive,
            message="Device token registered successfully",
        )

    except Exception as e:
        logger.error(f"Error registering device token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register device token",
        )


@router.delete("", status_code=status.HTTP_200_OK)
async def unregister_device_token(
    body: UnregisterTokenRequest,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Unregister a device token (e.g., on logout).

    Marks the token as inactive rather than deleting it,
    so we can track device history.
    """
    try:
        existing = await db.devicetoken.find_unique(where={"token": body.token})

        if not existing:
            return {"message": "Token not found (already removed)"}

        if existing.userId != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token does not belong to this user",
            )

        await db.devicetoken.update(
            where={"token": body.token},
            data={"isActive": False},
        )

        logger.info(f"Device token unregistered for user {current_user.id}")
        return {"message": "Device token unregistered successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unregistering device token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unregister device token",
        )
