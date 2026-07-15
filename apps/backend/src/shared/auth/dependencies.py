"""
FastAPI dependency injection for authentication and authorization.

Provides reusable dependencies that protect routes by validating JWT tokens
and enforcing role/tier requirements.

Usage:
    from src.shared.auth import CurrentUser, StaffUser, PremiumUser

    @router.get("/me")
    async def get_me(user: CurrentUser):
        return user

    @router.get("/admin/stats")
    async def admin_stats(user: StaffUser):
        ...
"""

import asyncio
import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select

from src.shared.database import get_session_factory
from src.domains.identity.db_models import User

from .jwt import decode_access_token

logger = logging.getLogger(__name__)

# Bearer token extraction
_security = HTTPBearer()

# ---------------------------------------------------------------------------
# Last-seen tracking (throttled to avoid DB spam)
# ---------------------------------------------------------------------------

_last_seen_cache: dict[str, float] = {}
_LAST_SEEN_THROTTLE_SECONDS = 300  # Update DB at most every 5 minutes per user


def _detect_platform(request: Request) -> str:
    """Detect platform from User-Agent header."""
    ua = (request.headers.get("user-agent") or "").lower()
    if "android" in ua or "okhttp" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua or "ios" in ua or "darwin" in ua:
        return "ios"
    return "web"


async def _update_last_seen(user_id: str, platform: str) -> None:
    """Update lastSeenAt, throttled to avoid excessive writes."""
    now = time.time()
    if now - _last_seen_cache.get(user_id, 0) < _LAST_SEEN_THROTTLE_SECONDS:
        return

    _last_seen_cache[user_id] = now
    try:
        from datetime import UTC, datetime
        from sqlalchemy import update

        factory = get_session_factory()
        async with factory() as session:
            stmt = (
                update(User)
                .where(User.id == user_id)
                .values(last_seen_at=datetime.now(UTC), last_seen_platform=platform)
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        pass  # Non-blocking — never fail a request for activity tracking


# ---------------------------------------------------------------------------
# Core user resolution
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_security)],
    request: Request,
) -> User:
    """Validate JWT and return the authenticated User.

    Also fires a background task to update lastSeenAt (throttled).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(credentials.credentials)
        email: str | None = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Fetch user from DB via SQLAlchemy
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # Fire-and-forget last seen update for non-admin users
    if user.role == "USER":
        platform = _detect_platform(request)
        asyncio.ensure_future(_update_last_seen(user.id, platform))

    return user


# Reusable type alias — use this in route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------


def _get_staff_role(user: User) -> str:
    """Normalize the admin staff role from a User record."""
    raw = user.admin_staff_role
    if raw is not None:
        return str(raw)
    if user.role == "ADMIN":
        return "SUPER_ADMIN"
    return "SUPER_ADMIN"


_STAFF_ROLES = frozenset({"SUPER_ADMIN", "CONTENT_MANAGER"})


async def get_staff_user(current_user: CurrentUser) -> User:
    """Require the user to be platform staff (SUPER_ADMIN or CONTENT_MANAGER)."""
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required"
        )
    if _get_staff_role(current_user) not in _STAFF_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required"
        )
    return current_user


async def get_super_admin_user(current_user: CurrentUser) -> User:
    """Require SUPER_ADMIN role (users, billing, staff management)."""
    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    if _get_staff_role(current_user) != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current_user


# Reusable type aliases
StaffUser = Annotated[User, Depends(get_staff_user)]
SuperAdminUser = Annotated[User, Depends(get_super_admin_user)]


# ---------------------------------------------------------------------------
# Tier-based access
# ---------------------------------------------------------------------------

PAID_TIERS = (
    "PREMIUM_MONTHLY",
    "PREMIUM_YEARLY",
    "STUDY_CIRCLE_MONTHLY",
    "STUDY_CIRCLE_YEARLY",
    "SQUAD_MONTHLY",
    "SQUAD_YEARLY",
)


async def require_premium(current_user: CurrentUser) -> User:
    """Require an active paid subscription."""
    if str(current_user.tier) not in PAID_TIERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature requires a paid plan. Start a free trial to unlock it.",
        )
    return current_user


PremiumUser = Annotated[User, Depends(require_premium)]


# ---------------------------------------------------------------------------
# Context-specific access (Learning Space membership, etc.)
# ---------------------------------------------------------------------------


async def require_space_membership(
    current_user: CurrentUser,
    space_id: str | None = Query(None, alias="spaceId"),
) -> User:
    """Require membership in a Learning Space (if space_id is provided).

    If no space_id is given, passes through (personal context).
    """
    if space_id:
        # TODO: Migrate CircleMember lookup to SQLAlchemy
        # For now, skip membership check (will be implemented when learning_spaces domain migrates)
        pass
    return current_user


SpaceMemberUser = Annotated[User, Depends(require_space_membership)]
