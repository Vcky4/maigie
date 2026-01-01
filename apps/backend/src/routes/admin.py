"""
Admin routes for user management.

This module handles admin-only operations including:
- Listing all users
- Viewing user details
- Updating user information
- Deleting users
- Managing user roles and status

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from prisma import Prisma
from prisma.models import User

from ..dependencies import AdminUser, DBDep
from ..utils.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ==========================================
#  REQUEST/RESPONSE MODELS
# ==========================================


class UserUpdateRequest(BaseModel):
    """Request model for updating user information."""

    name: Optional[str] = None
    email: Optional[EmailStr] = None
    tier: Optional[str] = None
    role: Optional[str] = None
    isActive: Optional[bool] = None
    isOnboarded: Optional[bool] = None


class UserListResponse(BaseModel):
    """Response model for user list."""

    users: list[dict]
    total: int
    page: int
    pageSize: int
    totalPages: int


class UserDetailResponse(BaseModel):
    """Response model for user details."""

    id: str
    email: str
    name: Optional[str]
    tier: str
    role: str
    isActive: bool
    isOnboarded: bool
    provider: Optional[str]
    stripeCustomerId: Optional[str]
    stripeSubscriptionStatus: Optional[str]
    creditsUsed: int
    creditsHardCap: Optional[int]
    creditsUsedToday: Optional[int]
    creditsDailyLimit: Optional[int]
    createdAt: str
    updatedAt: str


# ==========================================
#  USER MANAGEMENT ENDPOINTS
# ==========================================


@router.get("/users", response_model=UserListResponse)
async def list_users(
    admin_user: AdminUser,
    db: DBDep,
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by email or name"),
    tier: Optional[str] = Query(None, description="Filter by tier"),
    role: Optional[str] = Query(None, description="Filter by role"),
    isActive: Optional[bool] = Query(None, description="Filter by active status"),
):
    """
    List all users with pagination and filtering.

    Only accessible by admin users.
    """
    # Build where clause
    where: dict = {}

    if search:
        where["OR"] = [
            {"email": {"contains": search, "mode": "insensitive"}},
            {"name": {"contains": search, "mode": "insensitive"}},
        ]

    if tier:
        where["tier"] = tier.upper()

    if role:
        where["role"] = role.upper()

    if isActive is not None:
        where["isActive"] = isActive

    # Count total matching users
    total = await db.user.count(where=where)

    # Calculate pagination
    skip = (page - 1) * pageSize
    total_pages = (total + pageSize - 1) // pageSize

    # Fetch paginated users
    users = await db.user.find_many(
        where=where,
        skip=skip,
        take=pageSize,
        order={"createdAt": "desc"},
        include={"preferences": True},
    )

    # Format users for response
    user_list = []
    for user in users:
        user_list.append(
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "tier": str(user.tier),
                "role": str(user.role),
                "isActive": user.isActive,
                "isOnboarded": user.isOnboarded,
                "createdAt": user.createdAt.isoformat(),
                "updatedAt": user.updatedAt.isoformat(),
            }
        )

    return {
        "users": user_list,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "totalPages": total_pages,
    }


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_details(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Get detailed information about a specific user.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(
        where={"id": user_id},
        include={"preferences": True},
    )

    if not user:
        raise ResourceNotFoundError("User", user_id)

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "tier": str(user.tier),
        "role": str(user.role),
        "isActive": user.isActive,
        "isOnboarded": user.isOnboarded,
        "provider": user.provider,
        "stripeCustomerId": user.stripeCustomerId,
        "stripeSubscriptionStatus": user.stripeSubscriptionStatus,
        "creditsUsed": user.creditsUsed or 0,
        "creditsHardCap": user.creditsHardCap,
        "creditsUsedToday": user.creditsUsedToday,
        "creditsDailyLimit": user.creditsDailyLimit,
        "createdAt": user.createdAt.isoformat(),
        "updatedAt": user.updatedAt.isoformat(),
    }


@router.put("/users/{user_id}", response_model=UserDetailResponse)
async def update_user(
    user_id: str,
    update_data: UserUpdateRequest,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Update user information.

    Only accessible by admin users.
    """
    # Check if user exists
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from removing their own admin role
    if user_id == admin_user.id and update_data.role and update_data.role.upper() != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin role",
        )

    # Build update data
    update_dict: dict = {}
    if update_data.name is not None:
        update_dict["name"] = update_data.name
    if update_data.email is not None:
        # Check if email already exists for another user
        existing_user = await db.user.find_unique(where={"email": update_data.email})
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )
        update_dict["email"] = update_data.email
    if update_data.tier is not None:
        update_dict["tier"] = update_data.tier.upper()
    if update_data.role is not None:
        update_dict["role"] = update_data.role.upper()
    if update_data.isActive is not None:
        update_dict["isActive"] = update_data.isActive
    if update_data.isOnboarded is not None:
        update_dict["isOnboarded"] = update_data.isOnboarded

    # Update user
    updated_user = await db.user.update(
        where={"id": user_id},
        data=update_dict,
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} updated user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Delete a user permanently.

    Only accessible by admin users.
    """
    # Check if user exists
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from deleting themselves
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    # Delete user (cascade will handle related records)
    await db.user.delete(where={"id": user_id})

    logger.info(f"Admin {admin_user.email} deleted user {user_id}")

    return None


@router.post("/users/{user_id}/activate", response_model=UserDetailResponse)
async def activate_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Activate a user account.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"isActive": True},
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} activated user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }


@router.post("/users/{user_id}/deactivate", response_model=UserDetailResponse)
async def deactivate_user(
    user_id: str,
    admin_user: AdminUser,
    db: DBDep,
):
    """
    Deactivate a user account.

    Only accessible by admin users.
    """
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise ResourceNotFoundError("User", user_id)

    # Prevent admin from deactivating themselves
    if user_id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    updated_user = await db.user.update(
        where={"id": user_id},
        data={"isActive": False},
        include={"preferences": True},
    )

    logger.info(f"Admin {admin_user.email} deactivated user {user_id}")

    return {
        "id": updated_user.id,
        "email": updated_user.email,
        "name": updated_user.name,
        "tier": str(updated_user.tier),
        "role": str(updated_user.role),
        "isActive": updated_user.isActive,
        "isOnboarded": updated_user.isOnboarded,
        "provider": updated_user.provider,
        "stripeCustomerId": updated_user.stripeCustomerId,
        "stripeSubscriptionStatus": updated_user.stripeSubscriptionStatus,
        "creditsUsed": updated_user.creditsUsed or 0,
        "creditsHardCap": updated_user.creditsHardCap,
        "creditsUsedToday": updated_user.creditsUsedToday,
        "creditsDailyLimit": updated_user.creditsDailyLimit,
        "createdAt": updated_user.createdAt.isoformat(),
        "updatedAt": updated_user.updatedAt.isoformat(),
    }
