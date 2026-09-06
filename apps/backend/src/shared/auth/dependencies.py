"""
FastAPI dependency injection for authentication and authorization.

Provides reusable dependencies that protect routes by validating JWT tokens
and enforcing role requirements.

Entitlement is deliberately not here — see the note where `require_premium` used to be.

Usage:
    from src.shared.auth import CurrentUser, StaffUser

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

from src.domains.identity.db_models import User
from src.shared.database import get_session_factory

from .jwt import decode_access_token

logger = logging.getLogger(__name__)

# Bearer token extraction
_security = HTTPBearer()
# Optional variant — does not 403 when the Authorization header is missing.
_security_optional = HTTPBearer(auto_error=False)

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


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security_optional)],
    request: Request,
) -> User | None:
    """
    Return the authenticated User if a valid token is present, otherwise None.

    Use this on endpoints that anyone can call but that should give richer
    responses when the caller is signed in (e.g. previewing your own share
    link before publishing).
    """
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        email: str | None = payload.get("sub")
        if not email:
            return None
    except JWTError:
        return None

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return None
    return user


# Type alias for optional auth
OptionalCurrentUser = Annotated[User | None, Depends(get_current_user_optional)]


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    if _get_staff_role(current_user) not in _STAFF_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return current_user


async def get_super_admin_user(current_user: CurrentUser) -> User:
    """Require SUPER_ADMIN role (users, billing, staff management)."""
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if _get_staff_role(current_user) != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current_user


# Reusable type aliases
StaffUser = Annotated[User, Depends(get_staff_user)]
SuperAdminUser = Annotated[User, Depends(get_super_admin_user)]


# ---------------------------------------------------------------------------
# Tier-based access — deleted
# ---------------------------------------------------------------------------
#
# `PAID_TIERS`, `require_premium` and `PremiumUser` lived here and were wired to zero endpoints: a
# working gate that gated nothing, in a codebase where four mechanisms already disagreed about what
# "paid" meant. This was the third of the four, and the only one whose answer nobody consumed.
#
# It was also wrong in the way that mattered. It matched a six-tier tuple against `User.tier` and
# knew nothing about trials, so had anything ever depended on it, a trialling learner would have been
# refused a feature that `feature_tier_service` was granting them in the same request.
#
# Replaced by `billing.services.entitlement_service.resolve`, which is the one resolver
# (MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision B). A route needing Plus asks
# `feature_tier_service.check_capability` for the specific capability and returns an
# `UpgradeRequiredDetail`, rather than asking a dependency whether the learner is generically paid —
# the response conventions want to name what is locked and what unlocking it is worth.


# ---------------------------------------------------------------------------
# Context-specific access (Learning Space membership, etc.)
# ---------------------------------------------------------------------------


async def require_space_membership(
    current_user: CurrentUser,
    space_id: str | None = Query(None, alias="spaceId"),
) -> User:
    """Require membership of a Learning Space when ``spaceId`` is supplied.

    With no ``spaceId`` this is a personal-context request and passes through.

    This previously carried a ``TODO`` and returned unconditionally, so a dependency
    named ``require_space_membership`` enforced nothing. It was exported from
    ``shared.auth`` alongside the working guards, which made it look ready to use: any
    endpoint that adopted it would have been silently unprotected. Nothing had adopted
    it yet, so no endpoint was exposed, but the trap is worth removing rather than
    documenting. ``SpaceMember`` has existed in SQLAlchemy for some time.
    """
    if not space_id:
        return current_user

    from src.domains.learning_spaces.db_models import SpaceMember

    factory = get_session_factory()
    async with factory() as session:
        membership = (
            await session.execute(
                select(SpaceMember.id).where(
                    SpaceMember.space_id == space_id,
                    SpaceMember.user_id == current_user.id,
                )
            )
        ).first()

    if membership is None:
        # 404 rather than 403: whether a given space exists is not something a
        # non-member should be able to probe.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SPACE_NOT_FOUND", "message": "Space not found"},
        )

    return current_user


SpaceMemberUser = Annotated[User, Depends(require_space_membership)]
