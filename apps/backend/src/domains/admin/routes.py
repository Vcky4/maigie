"""
Admin domain — API routes.

Platform administration: user management, health, stats, content.
Requires staff role.

Mounted at: /api/v1/admin
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from src.shared.auth import StaffUser, SuperAdminUser
from src.shared.database import check_db_health, db
from src.shared.infrastructure import cache

from . import models

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ===========================================================================
# Health & Stats
# ===========================================================================


@router.get("/health", response_model=models.HealthCheckResponse)
async def admin_health(admin_user: StaffUser):
    """Detailed system health (staff only)."""
    from src.config import get_settings

    settings = get_settings()
    db_health = await check_db_health()
    cache_health = await cache.health_check()

    # Worker health
    worker_health = {"status": "unknown"}
    try:
        from src.workers.manager import check_worker_health

        worker_health = await check_worker_health()
    except Exception:
        worker_health = {"status": "unavailable"}

    return models.HealthCheckResponse(
        database=db_health,
        cache=cache_health,
        workers=worker_health,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
    )


@router.get("/stats", response_model=models.AdminStatsResponse)
async def admin_stats(admin_user: StaffUser):
    """Platform statistics overview."""
    total_users = await db.user.count(where={"role": "USER"})
    active_users = await db.user.count(where={"role": "USER", "isActive": True})
    premium_users = await db.user.count(
        where={"tier": {"in": ["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]}}
    )
    total_courses = await db.course.count()
    total_spaces = await db.circle.count()
    total_messages = await db.chatmessage.count()

    return models.AdminStatsResponse(
        totalUsers=total_users,
        activeUsers=active_users,
        premiumUsers=premium_users,
        totalCourses=total_courses,
        totalSpaces=total_spaces,
        totalMessages=total_messages,
    )


# ===========================================================================
# User Management
# ===========================================================================


@router.get("/users", response_model=list[models.AdminUserResponse])
async def list_users(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    tier: str | None = Query(None),
):
    """List all users (staff only)."""
    where: dict = {"role": "USER"}
    if search:
        where["OR"] = [
            {"email": {"contains": search, "mode": "insensitive"}},
            {"name": {"contains": search, "mode": "insensitive"}},
        ]
    if tier:
        where["tier"] = tier

    users = await db.user.find_many(
        where=where,
        skip=(page - 1) * pageSize,
        take=pageSize,
        order={"createdAt": "desc"},
    )
    return users


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: str, admin_user: SuperAdminUser):
    """Deactivate a user account (super admin only)."""
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.user.update(where={"id": user_id}, data={"isActive": False})
    return {"status": "deactivated", "userId": user_id}


@router.post("/users/{user_id}/activate")
async def activate_user(user_id: str, admin_user: SuperAdminUser):
    """Reactivate a user account (super admin only)."""
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.user.update(where={"id": user_id}, data={"isActive": True})
    return {"status": "activated", "userId": user_id}


@router.post("/staff/role")
async def update_staff_role(body: models.StaffRoleUpdateRequest, admin_user: SuperAdminUser):
    """Update a user's admin staff role (super admin only)."""
    user = await db.user.find_unique(where={"id": body.userId})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "ADMIN":
        raise HTTPException(status_code=400, detail="User is not an admin")

    await db.user.update(
        where={"id": body.userId}, data={"adminStaffRole": body.staffRole}
    )
    return {"status": "updated", "userId": body.userId, "staffRole": body.staffRole}
