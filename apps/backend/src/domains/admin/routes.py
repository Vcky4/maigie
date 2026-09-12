"""
Admin domain — API routes.

Platform administration: user management, health, stats, content.
Requires staff role.

Mounted at: /api/v1/admin
"""

import logging
import math

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.shared.auth import StaffUser, SuperAdminUser
from src.shared.database import get_session_factory, ilike_any

from . import models
from .services.audit_service import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


_CAMEL_TO_ATTR = {
    "name": "name",
    "email": "email",
    "tier": "tier",
    "role": "role",
    "adminStaffRole": "admin_staff_role",
    "isActive": "is_active",
    "isOnboarded": "is_onboarded",
}


def _jsonable(data: dict) -> dict:
    """Coerce a small dict of field values into something the JSONB audit column accepts."""
    out: dict = {}
    for key, value in data.items():
        out[key] = value if isinstance(value, str | int | float | bool | None) else str(value)
    return out


def _user_response(user) -> "models.UserAdminResponse":
    """Build the admin user view explicitly.

    Constructed field by field rather than via ``from_attributes`` because the ORM attributes are
    snake_case (`created_at`) while the wire contract is camelCase (`createdAt`); relying on attribute
    coercion would silently drop the timestamps.
    """
    return models.UserAdminResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        tier=user.tier,
        role=user.role,
        adminStaffRole=user.admin_staff_role,
        isActive=user.is_active,
        isOnboarded=user.is_onboarded,
        createdAt=user.created_at,
        updatedAt=user.updated_at,
    )


# ===========================================================================
# Health & Stats
# ===========================================================================


@router.get("/health")
async def admin_health(admin_user: StaffUser):
    """Detailed system health (staff only).

    Returns the composite the admin System Health page reads: an ``overall`` status, per-service
    cards (``services``) from real probes, and observed LLM circuit-breaker state (``llm_models``).
    ``/system-health`` is the canonical alias of this handler.
    """
    from .services import system_health_service

    return await system_health_service.snapshot()


@router.get("/stats", response_model=models.AdminStatsResponse)
async def admin_stats(admin_user: StaffUser):
    """Platform statistics overview."""
    from sqlalchemy import func, select

    from src.domains.identity.db_models import User
    from src.domains.intelligence.db_models import ChatMessage
    from src.domains.knowledge.db_models import Course
    from src.domains.learning_spaces.db_models import Space

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


@router.get("/dashboard")
async def admin_dashboard(admin_user: StaffUser):
    """The dashboard landing overview — a single honest composite (staff only).

    Every figure traces to a persisted row: retention from `User.last_seen_at`, AI economics from the
    real `ChatMessage` cost/revenue columns, revenue a genuine ~$0 until payment relationships exist.
    The flat `/stats` counts remain available at that path for any caller that still wants them.
    """
    from .services import dashboard_service

    return await dashboard_service.overview()


# ===========================================================================
# User Management
# ===========================================================================


@router.get("/users", response_model=models.UserAdminListResponse)
async def list_users(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    tier: str | None = Query(None),
    role: str | None = Query(None),
    isActive: bool | None = Query(None),
):
    """List users, paginated and filterable (staff only).

    Lists everyone by default — unlike the old handler, which was hardcoded to ``role == 'USER'`` and
    so could never show staff accounts. Pass ``role`` to narrow it.
    """
    from sqlalchemy import func

    from src.domains.identity.db_models import User as UserModel

    conditions = []
    if search:
        conditions.append(ilike_any(search, UserModel.email, UserModel.name))
    if tier:
        conditions.append(UserModel.tier == tier)
    if role:
        conditions.append(UserModel.role == role)
    if isActive is not None:
        conditions.append(UserModel.is_active.is_(isActive))

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(UserModel).where(*conditions))
        ).scalar() or 0

        stmt = (
            select(UserModel)
            .where(*conditions)
            .order_by(UserModel.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
        users = list((await session.execute(stmt)).scalars().all())

    return models.UserAdminListResponse(
        users=[_user_response(u) for u in users],
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total else 0,
    )


@router.post("/users", response_model=models.UserAdminResponse, status_code=201)
async def create_user(body: models.UserAdminCreateRequest, admin_user: SuperAdminUser):
    """Create a user (super admin only)."""
    from src.core.security import get_password_hash
    from src.domains.identity.repository import IdentityRepository
    from src.shared.exceptions import ConflictError

    repo = IdentityRepository()
    if await repo.find_by_email(body.email):
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    if body.adminStaffRole and body.adminStaffRole not in (
        "SUPER_ADMIN",
        "CONTENT_MANAGER",
    ):
        raise HTTPException(status_code=400, detail="Invalid staff role")

    try:
        user = await repo.create_user(
            email=body.email,
            password_hash=get_password_hash(body.password) if body.password else None,
            name=body.name,
            provider="email",
            is_active=body.isActive if body.isActive is not None else True,
        )
    except ConflictError:
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    extra: dict = {}
    if body.tier is not None:
        extra["tier"] = body.tier
    if body.role is not None:
        extra["role"] = body.role
    if body.adminStaffRole is not None:
        extra["adminStaffRole"] = body.adminStaffRole
    if body.isOnboarded is not None:
        extra["isOnboarded"] = body.isOnboarded
    if extra:
        user = await repo.update(user.id, extra)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="create_user",
        resource_type="user",
        resource_id=user.id,
        details={"email": body.email, "role": user.role, "tier": user.tier},
    )
    return _user_response(user)


@router.get("/users/{user_id}", response_model=models.UserAdminResponse)
async def get_user(user_id: str, admin_user: StaffUser):
    """Fetch one user (staff only)."""
    from src.domains.identity.repository import IdentityRepository

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(user)


@router.put("/users/{user_id}", response_model=models.UserAdminResponse)
async def update_user(
    user_id: str, body: models.UserAdminUpdateRequest, admin_user: SuperAdminUser
):
    """Update a user's editable fields (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.adminStaffRole and body.adminStaffRole not in (
        "SUPER_ADMIN",
        "CONTENT_MANAGER",
    ):
        raise HTTPException(status_code=400, detail="Invalid staff role")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return _user_response(user)

    before = {k: getattr(user, _CAMEL_TO_ATTR.get(k, k), None) for k in changes}
    updated = await repo.update(user_id, changes)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_user",
        resource_type="user",
        resource_id=user_id,
        details={"before": _jsonable(before), "after": _jsonable(changes)},
    )
    return _user_response(updated)


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin_user: SuperAdminUser):
    """Delete a user (super admin only).

    Starts the standard 90-day account-deletion lifecycle rather than hard-deleting the row. There is
    no hard-delete path anywhere in the codebase — scheduled deletion deactivates and anonymises, and
    `USER_DELETED` has no emitter — and "deletion is a request to forget" (`ch23-memory`) is honoured
    through that reviewed mechanism, not a raw cascade an admin endpoint invents. Reversible within the
    window via the learner's own cancel flow.
    """
    from src.domains.identity import services as identity_services
    from src.domains.identity.repository import IdentityRepository

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "ADMIN":
        raise HTTPException(status_code=400, detail="Admin accounts cannot be deleted here")

    await identity_services.request_deletion(user)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_user",
        resource_type="user",
        resource_id=user_id,
        details={"mode": "scheduled_deletion", "email": user.email},
    )
    return {"message": "Account deletion scheduled", "userId": user_id}


@router.post("/users/{user_id}/deactivate", response_model=models.UserAdminResponse)
async def deactivate_user(user_id: str, admin_user: SuperAdminUser):
    """Deactivate a user account (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    was_active = user.is_active
    updated = await repo.update(user_id, {"isActive": False})
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="deactivate_user",
        resource_type="user",
        resource_id=user_id,
        details={"isActive": {"before": was_active, "after": False}},
    )
    return _user_response(updated)


@router.post("/users/{user_id}/activate", response_model=models.UserAdminResponse)
async def activate_user(user_id: str, admin_user: SuperAdminUser):
    """Reactivate a user account (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    was_active = user.is_active
    updated = await repo.update(user_id, {"isActive": True})
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="activate_user",
        resource_type="user",
        resource_id=user_id,
        details={"isActive": {"before": was_active, "after": True}},
    )
    return _user_response(updated)


@router.post("/staff/role")
async def update_staff_role(body: models.StaffRoleUpdateRequest, admin_user: SuperAdminUser):
    """Update a user's admin staff role (super admin only)."""
    from src.domains.identity.repository import IdentityRepository

    if body.staffRole not in ("SUPER_ADMIN", "CONTENT_MANAGER"):
        raise HTTPException(status_code=400, detail="Invalid staff role")

    repo = IdentityRepository()
    user = await repo.find_by_id(body.userId)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "ADMIN":
        raise HTTPException(status_code=400, detail="User is not an admin")

    previous = user.admin_staff_role
    await repo.update(body.userId, {"adminStaffRole": body.staffRole})
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_staff_role",
        resource_type="user",
        resource_id=body.userId,
        details={"adminStaffRole": {"before": previous, "after": body.staffRole}},
    )
    return {"status": "updated", "userId": body.userId, "staffRole": body.staffRole}


# ===========================================================================
# Audit Logs
# ===========================================================================


@router.get("/audit-logs", response_model=models.AuditLogListResponse)
async def list_audit_logs(
    admin_user: SuperAdminUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    actionType: str | None = Query(None),
    resourceType: str | None = Query(None),
    adminUserId: str | None = Query(None),
    search: str | None = Query(None),
):
    """Read the trail of privileged actions (super admin only).

    The log is append-only and written by `audit_service.log_admin_action` from every mutating admin
    endpoint. Reading it is itself restricted to super admins because it names who did what to whom.
    Newest first; joined to the acting administrator's email/name for readability.
    """
    from sqlalchemy import func

    from src.domains.identity.db_models import User

    from .db_models import AuditLog

    conditions = []
    if actionType:
        conditions.append(AuditLog.action_type == actionType)
    if resourceType:
        conditions.append(AuditLog.resource_type == resourceType)
    if adminUserId:
        conditions.append(AuditLog.admin_user_id == adminUserId)
    if search:
        conditions.append(ilike_any(search, AuditLog.action_type, AuditLog.resource_type))

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(AuditLog).where(*conditions))
        ).scalar() or 0

        stmt = (
            select(AuditLog, User.email, User.name)
            .outerjoin(User, User.id == AuditLog.admin_user_id)
            .where(*conditions)
            .order_by(AuditLog.timestamp.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
        rows = (await session.execute(stmt)).all()

    logs = [
        models.AuditLogEntry(
            id=entry.id,
            timestamp=entry.timestamp,
            adminUserId=entry.admin_user_id,
            adminEmail=email or "(deleted)",
            adminName=name,
            actionType=entry.action_type,
            resourceType=entry.resource_type,
            resourceId=entry.resource_id,
            details=entry.details,
        )
        for entry, email, name in rows
    ]

    return models.AuditLogListResponse(
        logs=logs,
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total else 0,
    )


# ===========================================================================
# Entitlements (reshaped from "credits" — Decision 5)
#
# The credit endpoints the client still names (`/credits/adjust`, `/credits/reset`,
# `/credits/limits`) act on a model that no longer exists: `MAIGIE_PLUS_COMMERCIAL_PLAN.md` replaced
# credit caps with a rolling usage window and consumable passes, resolved by one authority. These
# endpoints are the successor surface. "Nothing is enforced that is not sold" runs both ways — the
# tool must not offer a lever the model no longer has.
# ===========================================================================


@router.get("/users/{user_id}/entitlement", response_model=models.AdminEntitlementView)
async def get_user_entitlement(user_id: str, admin_user: StaffUser):
    """A learner's current entitlement, usage window, voice balance, passes and points (staff only).

    Read entirely through the billing domain's own services so it cannot drift from what the learner
    is served (Decision 2): `entitlement_service.resolve` is the single authority for tier/source,
    `voice_service.resolve` for the voice meter, `points_service.balance` for points.
    """
    from sqlalchemy import select

    from src.domains.billing.db_models import PlusPass
    from src.domains.billing.services import (
        entitlement_service,
        points_service,
        voice_service,
    )
    from src.domains.identity.repository import IdentityRepository

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    entitlement = await entitlement_service.resolve(user_id)
    voice = voice_service.resolve(user, entitlement)
    points = await points_service.balance(user_id)

    factory = get_session_factory()
    async with factory() as session:
        passes = list(
            (
                await session.execute(
                    select(PlusPass)
                    .where(PlusPass.user_id == user_id)
                    .order_by(PlusPass.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    return models.AdminEntitlementView(
        tier=entitlement.tier,
        source=entitlement.source,
        expiresAt=entitlement.expires_at,
        isTrial=entitlement.is_trial,
        trialDaysRemaining=entitlement.trial_days_remaining,
        subscriptionTier=entitlement.subscription_tier,
        windowAllowance=entitlement.window_allowance,
        windowUnitsUsed=user.usage_window_units_used,
        windowStartedAt=user.usage_window_started_at,
        monthlyBackstop=entitlement.monthly_backstop,
        monthlyUnitsUsed=user.usage_month_units_used,
        voiceAvailable=voice.available,
        voiceSecondsRemaining=voice.total_seconds,
        voiceSecondsPurchased=voice.purchased_seconds,
        voiceMinutesIncluded=entitlement.voice_seconds_included // 60,
        pointsBalance=points.balance,
        activePassId=user.active_plus_pass_id,
        passes=[
            models.AdminPassView(
                id=p.id,
                productId=p.product_id,
                status=p.status,
                unitsAllowance=p.units_allowance,
                unitsUsed=p.units_used,
                durationMinutes=p.duration_minutes,
                source=p.source,
                activatedAt=p.activated_at,
                expiresAt=p.expires_at,
                endedReason=p.ended_reason,
                createdAt=p.created_at,
            )
            for p in passes
        ],
    )


@router.post("/users/{user_id}/entitlement/grant-pass", response_model=models.AdminPassView)
async def grant_comp_pass(user_id: str, body: models.GrantPassRequest, admin_user: SuperAdminUser):
    """Grant a complimentary pass into a learner's inventory (super admin only).

    Grants, does not activate — the learner starts the clock, exactly as with a purchased pass. Routed
    through `pass_service.grant` with ``source="admin_comp"`` so there is one code path that knows how
    to mint a pass, and no `PlusPurchase` is fabricated behind it. Audited, with the stated reason.
    """
    from src.domains.billing.services import pass_service
    from src.domains.identity.repository import IdentityRepository
    from src.shared.exceptions import ConflictError

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        granted = await pass_service.grant(
            user_id=user_id, product_id=body.productId, source="admin_comp"
        )
    except ConflictError as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="grant_comp_pass",
        resource_type="user",
        resource_id=user_id,
        details={
            "productId": body.productId,
            "passId": granted.id,
            "reason": body.reason,
        },
    )
    return models.AdminPassView(
        id=granted.id,
        productId=granted.product_id,
        status=granted.status,
        unitsAllowance=granted.units_allowance,
        unitsUsed=granted.units_used,
        durationMinutes=granted.duration_minutes,
        source=granted.source,
        activatedAt=granted.activated_at,
        expiresAt=granted.expires_at,
        endedReason=granted.ended_reason,
        createdAt=granted.created_at,
    )


@router.get("/users/{user_id}/summary")
async def get_user_summary(user_id: str, admin_user: StaffUser):
    """A flat operational summary for the user-summary sheet (staff only).

    Every figure is a real row for this learner: subscription state off the `User` columns, the
    "credits" block reframed onto the live usage window (Decision 5 — credit caps are retired), and
    per-user chat/course/referral counts. Framed as understanding, not judgement (`ch16`,
    `ch14-behaviour`): it reports what the account *is*, not a verdict on the learner.
    """
    from src.domains.billing.services import entitlement_service
    from src.domains.identity.db_models import User as UserModel
    from src.domains.identity.repository import IdentityRepository
    from src.domains.intelligence.db_models import ChatMessage
    from src.domains.knowledge.db_models import Course

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    entitlement = await entitlement_service.resolve(user_id)

    factory = get_session_factory()
    async with factory() as session:

        async def _scalar(stmt) -> int:
            return int((await session.execute(stmt)).scalar() or 0)

        total_messages = await _scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.user_id == user_id)
        )
        total_tokens = await _scalar(
            select(func.coalesce(func.sum(ChatMessage.token_count), 0)).where(
                ChatMessage.user_id == user_id
            )
        )
        total_cost = float(
            (
                await session.execute(
                    select(func.coalesce(func.sum(ChatMessage.cost_usd), 0.0)).where(
                        ChatMessage.user_id == user_id
                    )
                )
            ).scalar()
            or 0.0
        )
        total_revenue = float(
            (
                await session.execute(
                    select(func.coalesce(func.sum(ChatMessage.revenue_usd), 0.0)).where(
                        ChatMessage.user_id == user_id
                    )
                )
            ).scalar()
            or 0.0
        )
        total_courses = await _scalar(
            select(func.count()).select_from(Course).where(Course.user_id == user_id)
        )
        # Referrals: how many learners this user brought in, and how many qualified (the points
        # model's `referral_qualified`). The retired token/claim tables are empty.
        total_referrals = 0
        claimed_referrals = 0
        if user.referral_code:
            total_referrals = await _scalar(
                select(func.count())
                .select_from(UserModel)
                .where(UserModel.referred_by_code == user.referral_code)
            )
            try:
                from src.domains.billing.db_models import PointsLedgerEntry

                claimed_referrals = await _scalar(
                    select(func.count())
                    .select_from(PointsLedgerEntry)
                    .where(
                        PointsLedgerEntry.user_id == user_id,
                        PointsLedgerEntry.kind == "referral_qualified",
                    )
                )
            except Exception:
                logger.debug("user summary: referral points unavailable", exc_info=True)

    subscription_status = user.stripe_subscription_status or (
        user.tier if user.tier in ("PREMIUM_MONTHLY", "PREMIUM_YEARLY") else None
    )

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "tier": user.tier,
        "isActive": user.is_active,
        "subscription": {
            "status": subscription_status,
            "customerId": user.stripe_customer_id,
            "periodStart": user.subscription_current_period_start,
            "periodEnd": user.subscription_current_period_end,
        },
        # "Credits" reframed onto the live usage window (credit caps are retired, Decision 5): "used"
        # is the current window's consumed units, "hardCap" the window allowance from the one resolver.
        "credits": {
            "used": user.usage_window_units_used,
            "hardCap": entitlement.window_allowance,
        },
        "statistics": {
            "totalMessages": total_messages,
            "totalTokens": total_tokens,
            "totalCostUsd": round(total_cost, 4),
            "totalRevenueUsd": round(total_revenue, 4),
            "totalCourses": total_courses,
            "totalReferrals": total_referrals,
            "claimedReferrals": claimed_referrals,
        },
    }


# ===========================================================================
# Analytics (Phase 2 — honest, real-row metrics only)
#
# Read-only. The enhanced analytics, revenue, retention and growth surfaces the client also names are
# deliberately absent rather than fabricated — see `docs/ADMIN_DASHBOARD_PLAN.md` Phase 2 for why
# (no backing data, or per-currency revenue the client type assumes is blended).
# ===========================================================================


@router.get("/analytics", response_model=models.AdminAnalyticsResponse)
async def platform_analytics(admin_user: SuperAdminUser):
    """Platform-wide analytics: stats, top users, top and recent courses (super admin only)."""
    from .services import analytics_service

    return await analytics_service.platform_analytics()


@router.get("/analytics/users/{user_id}", response_model=models.UserDetailAnalyticsResponse)
async def user_analytics(user_id: str, admin_user: StaffUser):
    """One learner's course analytics (staff only). Framed as understanding, not judgement."""
    from src.shared.exceptions import NotFoundError

    from .services import analytics_service

    try:
        return await analytics_service.user_analytics(user_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="User not found")


@router.get("/analytics/reengagement")
async def reengagement_analytics(admin_user: StaffUser, days: int = Query(30, ge=1, le=365)):
    """Nudge/wake analytics from real notification rows (staff). 'Came back' is 0 (not attributed)."""
    from .services import reengagement_service

    return await reengagement_service.reengagement_analytics(days)


@router.get("/retention/deep-wake-config")
async def get_deep_wake_config(admin_user: StaffUser):
    """The deep-wake inactivity threshold (staff)."""
    from .services import reengagement_service

    return await reengagement_service.deep_wake_config()


@router.put("/retention/deep-wake-config")
async def update_deep_wake_config(
    body: models.DeepWakeConfigUpdateRequest, admin_user: SuperAdminUser
):
    """Set the deep-wake inactivity threshold (super admin), audited."""
    from .services import reengagement_service

    if body.max_inactive_days < 1 or body.max_inactive_days > 365:
        raise HTTPException(status_code=400, detail="max_inactive_days must be between 1 and 365")
    result = await reengagement_service.set_deep_wake_config(body.max_inactive_days)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_deep_wake_config",
        resource_type="config",
        resource_id="deepWake.maxInactiveDays",
        details={"maxInactiveDays": body.max_inactive_days},
    )
    return result


@router.post("/users/regenerate-schedules")
async def regenerate_schedules(body: models.RegenerateSchedulesRequest, admin_user: SuperAdminUser):
    """Repack drifted study plans so returning learners see fresh ones (super admin), audited."""
    from .services import reengagement_service

    result = await reengagement_service.bulk_regenerate_schedules(
        body.max_users, body.only_inactive_days
    )
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="bulk_regenerate_schedules",
        resource_type="study_plan",
        resource_id=None,
        details=result,
    )
    return result


@router.get("/analytics/revenue")
async def revenue_analytics(admin_user: SuperAdminUser):
    """Revenue analytics (super admin). Subscription counts real; MRR estimated; churn 0 (no history)."""
    from .services import analytics_service

    return await analytics_service.revenue_analytics()


@router.get("/analytics/retention")
async def retention_analytics(admin_user: StaffUser):
    """Retention analytics (staff). DAU/WAU/MAU real; cohorts/adoption empty (no historical log)."""
    from .services import analytics_service

    return await analytics_service.retention_analytics()


@router.get("/analytics/growth")
async def growth_analytics(admin_user: StaffUser, days: int = Query(30, ge=1, le=365)):
    """Growth analytics (staff). Signups real; conversions 0 (untracked); referrals real."""
    from .services import analytics_service

    return await analytics_service.growth_analytics(days)


@router.get("/dashboard/charts")
async def dashboard_charts(admin_user: StaffUser, days: int = Query(14, ge=1, le=180)):
    """Daily signups and messages over a window (staff only).

    Returns the exact shape the charts consume — ``dailySignups``/``dailyMessages`` with
    ``signups``/``messages`` keys — so the period selector re-queries with no client-side remap.
    """
    from .services import analytics_service

    return await analytics_service.dashboard_charts(days)


# ===========================================================================
# Re-engagement (Phase 3 — never-guilt, consent-gated)
#
# The read (`/analytics/users-at-risk`) never sends anything. The action (`/wake`) routes through the
# notifications orchestrator's one entrypoint, `create_notification`, which plans the message and
# re-checks `engagement_enabled` + per-channel consent + quiet hours + suppression at dispatch time —
# so an admin cannot email a learner who has said no. It uses the `learning.gentle_return` type, whose
# 14-day dedupe backstops any attempt to nag. Copy stays guilt-free, per the product's own rule.
# Bulk broadcast email is deliberately NOT built here — see `docs/ADMIN_DASHBOARD_PLAN.md` Phase 3.
# ===========================================================================

_WAKE_DEFAULT_BODY = "Pick up where you left off — no pressure."


@router.get("/analytics/users-at-risk", response_model=models.UsersAtRiskResponse)
async def users_at_risk(admin_user: StaffUser, limit: int = Query(50, ge=1, le=500)):
    """Inactive learners, most-inactive first (staff only). Read-only; sends nothing."""
    from .services import analytics_service

    return await analytics_service.users_at_risk(limit)


async def _wake_user(user_id: str, custom_message: str | None) -> str | None:
    """Enqueue one guilt-free return nudge. Returns the notification id, or None if deduped/declined.

    Calls the single orchestrator entrypoint so consent is enforced at dispatch. The idempotency key
    is per-user-per-day and the type's 14-day dedupe is the real backstop, so repeated wakes cannot
    spam a learner.
    """
    from datetime import UTC, datetime

    from src.domains.notifications.service import create_notification

    today = datetime.now(UTC).date().isoformat()
    notification = await create_notification(
        user_id=user_id,
        type="learning.gentle_return",
        title="Welcome back",
        body=(
            custom_message.strip()
            if custom_message and custom_message.strip()
            else _WAKE_DEFAULT_BODY
        ),
        action={"version": 1, "kind": "OPEN_HOME"},
        idempotency_key=f"admin-wake:{user_id}:{today}",
        source_domain="admin",
    )
    return notification.id if notification is not None else None


@router.post("/users/{user_id}/wake", response_model=models.WakeResponse)
async def wake_user(user_id: str, body: models.WakeRequest, admin_user: SuperAdminUser):
    """Send one learner a gentle return nudge (super admin only), consent-gated at dispatch."""
    from src.domains.identity.repository import IdentityRepository

    user = await IdentityRepository().find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    notification_id = await _wake_user(user_id, body.customMessage)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="wake_user",
        resource_type="user",
        resource_id=user_id,
        details={
            "customMessage": bool(body.customMessage),
            "notificationId": notification_id,
        },
    )
    return models.WakeResponse(status="queued", userId=user_id, notificationId=notification_id)


@router.post("/users/wake-bulk", response_model=models.WakeBulkResponse)
async def wake_users_bulk(admin_user: SuperAdminUser, limit: int = Query(50, ge=1, le=200)):
    """Send gentle return nudges to the most-inactive learners (super admin only).

    Each nudge is planned through the consent-gated orchestrator, so a learner who has engagement off
    receives nothing regardless of being on the list. Bounded by ``limit`` and by the type's 14-day
    dedupe.
    """
    from .services import analytics_service

    at_risk = await analytics_service.users_at_risk(limit)
    sent = 0
    failed = 0
    for at_risk_user in at_risk.users:
        try:
            await _wake_user(at_risk_user.userId, None)
            sent += 1
        except Exception:
            logger.warning(
                "wake-bulk: failed to enqueue for %s",
                at_risk_user.userId,
                exc_info=True,
            )
            failed += 1

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="wake_users_bulk",
        resource_type="user",
        resource_id=None,
        details={"attempted": len(at_risk.users), "sent": sent, "failed": failed},
    )
    return models.WakeBulkResponse(total=len(at_risk.users), sent=sent, failed=failed)


# ===========================================================================
# System — health and AI task/action-log reads (Phase 4)
#
# Read-only. System/LLM config (write) is deliberately not built here: the client's `SystemConfig`
# shape references the retired credit model, the real `SystemConfig` table is a generic key/value
# store needing a reshaped contract, and config writes touch runtime settings — see
# `docs/ADMIN_DASHBOARD_PLAN.md` Phase 4.
# ===========================================================================


@router.get("/system-health")
async def system_health(admin_user: StaffUser):
    """Detailed system health (staff only). The canonical name; `/health` is the older alias."""
    return await admin_health(admin_user)


@router.get("/ai-agent-tasks", response_model=models.AiAgentTaskListResponse)
async def list_ai_agent_tasks(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    taskType: str | None = Query(None),
    status: str | None = Query(None),
    userId: str | None = Query(None),
):
    """List AI agent tasks, newest first (staff only)."""
    from src.domains.intelligence.db_models import AIAgentTask

    conditions = []
    if taskType:
        conditions.append(AIAgentTask.task_type == taskType)
    if status:
        conditions.append(AIAgentTask.status == status)
    if userId:
        conditions.append(AIAgentTask.user_id == userId)

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(AIAgentTask).where(*conditions))
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(AIAgentTask)
                    .where(*conditions)
                    .order_by(AIAgentTask.created_at.desc())
                    .offset((page - 1) * pageSize)
                    .limit(pageSize)
                )
            )
            .scalars()
            .all()
        )

    return models.AiAgentTaskListResponse(
        items=[
            models.AiAgentTaskItem(
                id=r.id,
                userId=r.user_id,
                taskType=r.task_type,
                status=r.status,
                priority=r.priority,
                title=r.title,
                message=r.message,
                actionData=r.action_data,
                scheduledAt=r.scheduled_at,
                sentAt=r.sent_at,
                dismissedAt=r.dismissed_at,
                createdAt=r.created_at,
                updatedAt=r.updated_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total else 0,
    )


@router.get("/ai-action-logs", response_model=models.AiActionLogListResponse)
async def list_ai_action_logs(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    actionType: str | None = Query(None),
    status: str | None = Query(None),
):
    """List AI action-log entries, newest first (staff only)."""
    from src.domains.intelligence.db_models import AIActionLog

    conditions = []
    if actionType:
        conditions.append(AIActionLog.action_type == actionType)
    if status:
        conditions.append(AIActionLog.status == status)

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(AIActionLog).where(*conditions))
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(AIActionLog)
                    .where(*conditions)
                    .order_by(AIActionLog.created_at.desc())
                    .offset((page - 1) * pageSize)
                    .limit(pageSize)
                )
            )
            .scalars()
            .all()
        )

    return models.AiActionLogListResponse(
        items=[
            models.AiActionLogItem(
                id=r.id,
                messageId=r.message_id,
                actionType=r.action_type,
                actionData=r.action_data,
                status=r.status,
                error=r.error,
                createdAt=r.created_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        pageSize=pageSize,
        totalPages=math.ceil(total / pageSize) if total else 0,
    )


# ===========================================================================
# Courses (admin view over the knowledge domain)
# ===========================================================================


@router.get("/courses", response_model=models.AdminCourseListResponse)
async def list_all_courses(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    userId: str | None = Query(None),
    difficulty: str | None = Query(None),
    isAIGenerated: bool | None = Query(None),
    archived: bool | None = Query(None),
    search: str | None = Query(None),
):
    """List courses across learners, paginated and filterable (staff only)."""
    from .services import courses_service

    return await courses_service.list_courses(
        page=page,
        page_size=pageSize,
        user_id=userId,
        difficulty=difficulty,
        is_ai_generated=isAIGenerated,
        archived=archived,
        search=search,
    )


@router.get("/courses/{course_id}", response_model=models.AdminCourseDetail)
async def get_course_details(course_id: str, admin_user: StaffUser):
    """A course with its modules and topics (staff only)."""
    from src.shared.exceptions import NotFoundError

    from .services import courses_service

    try:
        return await courses_service.course_detail(course_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str, admin_user: SuperAdminUser):
    """Delete a course and its modules/topics (super admin only), audited.

    A hard delete — a course is a learner's own artifact, and removing it is a request to forget it;
    `Module`/`Topic` cascade from `Course`.
    """
    from src.shared.exceptions import NotFoundError

    from .services import courses_service

    try:
        await courses_service.delete_course(course_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="delete_course",
        resource_type="course",
        resource_id=course_id,
        details=None,
    )
    return {"message": "Course deleted", "courseId": course_id}


# ===========================================================================
# Chat monitoring (aggregate + metadata only — no message content; Decision 6)
# ===========================================================================


@router.get("/chat/sessions", response_model=models.ChatSessionListResponse)
async def list_chat_sessions(
    admin_user: SuperAdminUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    userId: str | None = Query(None),
    search: str | None = Query(None),
):
    """Chat sessions with counts/cost (super admin). Metadata only — no message content is returned."""
    from .services import chat_monitoring_service

    return await chat_monitoring_service.list_chat_sessions(
        page=page, page_size=pageSize, user_id=userId, search=search
    )


@router.get("/chat/stats", response_model=models.ChatStatisticsResponse)
async def chat_statistics(admin_user: SuperAdminUser):
    """Aggregate chat/AI statistics (super admin). No message content."""
    from .services import chat_monitoring_service

    return await chat_monitoring_service.chat_statistics()


# ===========================================================================
# Staff
# ===========================================================================


def _staff_member(user) -> "models.StaffMember":
    return models.StaffMember(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        adminStaffRole=user.admin_staff_role,
        isActive=user.is_active,
    )


@router.get("/staff", response_model=list[models.StaffMember])
async def list_staff(admin_user: SuperAdminUser):
    """List platform staff (role == ADMIN), super admin only."""
    from src.domains.identity.db_models import User as UserModel

    factory = get_session_factory()
    async with factory() as session:
        users = list(
            (
                await session.execute(
                    select(UserModel).where(UserModel.role == "ADMIN").order_by(UserModel.email)
                )
            )
            .scalars()
            .all()
        )
    return [_staff_member(u) for u in users]


@router.patch("/staff/{user_id}", response_model=models.StaffMember)
async def update_staff_member(
    user_id: str, body: models.StaffRoleUpdateBody, admin_user: SuperAdminUser
):
    """Set a staff member's admin role (super admin only), audited."""
    from src.domains.identity.repository import IdentityRepository

    if body.adminStaffRole not in ("SUPER_ADMIN", "CONTENT_MANAGER"):
        raise HTTPException(status_code=400, detail="Invalid staff role")

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "ADMIN":
        raise HTTPException(status_code=400, detail="User is not an admin")

    previous = user.admin_staff_role
    updated = await repo.update(user_id, {"adminStaffRole": body.adminStaffRole})
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_staff_role",
        resource_type="user",
        resource_id=user_id,
        details={"adminStaffRole": {"before": previous, "after": body.adminStaffRole}},
    )
    return _staff_member(updated)


# ===========================================================================
# Referrals (reshaped onto the points model)
# ===========================================================================


@router.get("/referrals", response_model=models.ReferralListResponse)
async def list_referrals(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    referrerId: str | None = Query(None),
    isClaimed: bool | None = Query(None),
):
    """Referral rewards, read from the points ledger (staff)."""
    from .services import referrals_service

    return await referrals_service.list_referrals(
        page=page, page_size=pageSize, referrer_id=referrerId, is_claimed=isClaimed
    )


@router.get("/referrals/stats", response_model=models.ReferralStatistics)
async def referral_statistics(admin_user: StaffUser):
    """Referral statistics from the points ledger (staff)."""
    from .services import referrals_service

    return await referrals_service.referral_statistics()


# ===========================================================================
# System / LLM configuration (SystemConfig key/value; no secrets)
# ===========================================================================


@router.get("/config", response_model=models.SystemConfigResponse)
async def get_system_config(admin_user: SuperAdminUser):
    """Operational config: maintenance mode + feature flags (super admin). creditLimits is retired."""
    from .services import config_service

    return await config_service.get_system_config()


@router.put("/config", response_model=models.SystemConfigResponse)
async def update_system_config(body: models.SystemConfigUpdateRequest, admin_user: SuperAdminUser):
    """Update maintenance mode / feature flags (super admin), audited."""
    from .services import config_service

    result = await config_service.update_system_config(body)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_system_config",
        resource_type="config",
        resource_id="system",
        details={
            "maintenanceMode": body.maintenanceMode,
            "featureFlags": body.featureFlags,
        },
    )
    return result


@router.get("/llm-config")
async def get_llm_config(admin_user: SuperAdminUser) -> dict[str, str]:
    """Stored LLM routing preferences (super admin). Non-secret key/values only."""
    from .services import config_service

    return await config_service.get_llm_config()


@router.put("/llm-config")
async def update_llm_config(body: dict[str, str], admin_user: SuperAdminUser) -> dict[str, str]:
    """Set LLM routing preferences (super admin), audited. Never stores secrets."""
    from .services import config_service

    result = await config_service.update_llm_config(body)
    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_llm_config",
        resource_type="config",
        resource_id="llm",
        details={"keys": sorted(body.keys())},
    )
    return result
