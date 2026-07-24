"""
Admin domain — API routes.

Platform administration: user management, health, stats, content.
Requires staff role.

Mounted at: /api/v1/admin
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from src.shared.auth import StaffUser, SuperAdminUser
from src.shared.database import check_db_health, get_session_factory
from src.shared.infrastructure import cache
from sqlalchemy import select

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
    from sqlalchemy import select, func
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course
    from src.domains.learning_spaces.db_models import Space
    from src.domains.intelligence.db_models import ChatMessage

    factory = get_session_factory()
    async with factory() as session:
        total_users = (
            await session.execute(select(func.count()).select_from(User).where(User.role == "USER"))
        ).scalar() or 0
        active_users = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == "USER", User.is_active.is_(True))
            )
        ).scalar() or 0
        premium_users = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.tier.in_(["PREMIUM_MONTHLY", "PREMIUM_YEARLY"]))
            )
        ).scalar() or 0
        total_courses = (
            await session.execute(select(func.count()).select_from(Course))
        ).scalar() or 0
        total_spaces = (
            await session.execute(select(func.count()).select_from(Space))
        ).scalar() or 0
        total_messages = (
            await session.execute(select(func.count()).select_from(ChatMessage))
        ).scalar() or 0

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
    from src.domains.identity.db_models import User as UserModel

    factory = get_session_factory()
    async with factory() as session:
        conditions = [UserModel.role == "USER"]
        if search:
            conditions.append(
                (UserModel.email.ilike(f"%{search}%")) | (UserModel.name.ilike(f"%{search}%"))
            )
        if tier:
            conditions.append(UserModel.tier == tier)

        stmt = (
            select(UserModel)
            .where(*conditions)
            .order_by(UserModel.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
        result = await session.execute(stmt)
        users = list(result.scalars().all())
    return users


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: str, admin_user: SuperAdminUser):
    """Deactivate a user account (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await repo.update(user_id, {"isActive": False})
    return {"status": "deactivated", "userId": user_id}


@router.post("/users/{user_id}/activate")
async def activate_user(user_id: str, admin_user: SuperAdminUser):
    """Reactivate a user account (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await repo.update(user_id, {"isActive": True})
    return {"status": "activated", "userId": user_id}


@router.post("/staff/role")
async def update_staff_role(body: models.StaffRoleUpdateRequest, admin_user: SuperAdminUser):
    """Update a user's admin staff role (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(body.userId)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "ADMIN":
        raise HTTPException(status_code=400, detail="User is not an admin")

    await repo.update(body.userId, {"adminStaffRole": body.staffRole})
    return {"status": "updated", "userId": body.userId, "staffRole": body.staffRole}
