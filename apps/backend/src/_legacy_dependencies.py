"""
Dependency injection system.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # <--- CHANGED
from jose import JWTError

from prisma import Prisma
from prisma.models import User

from .config import Settings, get_settings
from .core.database import db
from .core.security import decode_access_token
from .models.auth import TokenData

logger = logging.getLogger(__name__)

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_settings_dependency() -> Settings:
    """
    Get settings dependency (for verification/testing).
    This is a convenience function for non-FastAPI contexts.
    """
    return get_settings()


# Database dependency
async def get_db() -> Prisma:
    """Get database client dependency."""
    return db


DBDep = Annotated[Prisma, Depends(get_db)]

# --- CHANGED: Use HTTPBearer ---
# This tells Swagger UI: "Just ask for a Bearer Token string"
security = HTTPBearer()


# In-memory throttle for lastSeenAt updates (per user_id -> last_update_time)
_last_seen_cache: dict[str, float] = {}
_LAST_SEEN_THROTTLE_SECONDS = 300  # Only update DB every 5 minutes per user


def _detect_platform(request: Request) -> str:
    """Detect platform from User-Agent header."""
    ua = (request.headers.get("user-agent") or "").lower()
    if "android" in ua or "okhttp" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua or "ios" in ua or "darwin" in ua:
        return "ios"
    return "web"


async def _update_last_seen(user_id: str, platform: str) -> None:
    """Update lastSeenAt and platform, throttled to avoid DB spam."""
    now = time.time()
    last_update = _last_seen_cache.get(user_id, 0)

    if now - last_update < _LAST_SEEN_THROTTLE_SECONDS:
        return  # Skip, updated recently

    _last_seen_cache[user_id] = now

    try:
        from datetime import UTC, datetime

        await db.user.update(
            where={"id": user_id},
            data={
                "lastSeenAt": datetime.now(UTC),
                "lastSeenPlatform": platform,
            },
        )
    except Exception:
        pass  # Non-blocking, never fail a request for this


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    request: Request,
) -> User:
    """
    Validate JWT and retrieve the current user from the database.
    This is the main dependency for protecting routes.
    Also updates lastSeenAt (throttled) for activity tracking.
    """
    # Extract the token string from the Bearer object
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # 1. Decode the token using your security util
        payload = decode_access_token(token)

        # 2. Extract the subject (email)
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception

        token_data = TokenData(email=email)

    except JWTError:
        raise credentials_exception

    # 3. Fetch User from Database
    # We include preferences so we don't need a second query later
    user = await db.user.find_unique(
        where={"email": token_data.email}, include={"preferences": True}
    )

    if user is None:
        raise credentials_exception

    # Optional: Check if user is active
    if not user.isActive:
        raise HTTPException(status_code=400, detail="Inactive user")

    # 4. Update lastSeenAt (fire-and-forget, throttled to every 5 min)
    if user.role == "USER":
        platform = _detect_platform(request)
        # Don't await in the critical path for non-admin users
        import asyncio

        asyncio.ensure_future(_update_last_seen(user.id, platform))

    return user


# Create a reusable type shortcut
CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(current_user: CurrentUser) -> User:
    """
    Dependency to ensure the current user is an admin.
    Must be used after get_current_user dependency.
    """
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# Create a reusable type shortcut for admin users
AdminUser = Annotated[User, Depends(get_admin_user)]

_STAFF_PORTAL_ROLES = frozenset({"SUPER_ADMIN", "CONTENT_MANAGER"})


def admin_staff_role(user: User) -> str:
    """Normalize AdminStaffRole from Prisma user to string."""
    raw = getattr(user, "adminStaffRole", None)
    if raw is not None:
        if isinstance(raw, str):
            return raw
        return str(getattr(raw, "value", raw) or "SUPER_ADMIN")
    # Admins created before sub-roles existed: full access.
    if getattr(user, "role", None) == "ADMIN":
        return "SUPER_ADMIN"
    return "SUPER_ADMIN"


async def get_staff_admin_user(current_user: CurrentUser) -> User:
    """
    Admin portal access: super admins and content managers.
    """
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    if admin_staff_role(current_user) not in _STAFF_PORTAL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_super_admin_user(current_user: CurrentUser) -> User:
    """
    Operations that only full super admins may perform (users, billing, staff roles, etc.).
    """
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    if admin_staff_role(current_user) != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )
    return current_user


StaffAdminUser = Annotated[User, Depends(get_staff_admin_user)]
SuperAdminUser = Annotated[User, Depends(get_super_admin_user)]


PAID_TIERS = (
    "PREMIUM_MONTHLY",
    "PREMIUM_YEARLY",
    "STUDY_CIRCLE_MONTHLY",
    "STUDY_CIRCLE_YEARLY",
    "SQUAD_MONTHLY",
    "SQUAD_YEARLY",
)


async def require_premium(current_user: CurrentUser) -> User:
    """
    Dependency to ensure the current user has a paid subscription.
    Smart AI Tutor, Exam Prep, and 11labs voice require Maigie Plus or higher.
    """
    if str(current_user.tier) not in PAID_TIERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires a paid plan. Start a free trial to unlock Smart AI Tutor and Exam Prep.",
        )
    return current_user


PremiumUser = Annotated[User, Depends(require_premium)]


async def require_exam_prep_access(
    current_user: CurrentUser,
    db_client: DBDep,
    circle_id: str | None = Query(None),
) -> User:
    """
    Personal exam prep: any authenticated user (usage is limited by credits).
    Circle exam prep: circle members only.
    """
    if circle_id:
        member = await db_client.circlemember.find_first(
            where={"circleId": circle_id, "userId": current_user.id}
        )
        if not member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this circle",
            )
        return current_user

    return current_user


ExamPrepUser = Annotated[User, Depends(require_exam_prep_access)]
