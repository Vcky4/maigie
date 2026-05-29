"""
Seat service — manages per-(User, Circle) PLUS_SEAT assignments.

Provides read APIs (get_seat_tier, list_seats) and mutation APIs
(assign_seat, unassign_seat, reassign_seat) with strict validation order:
    auth → OWNER/ADMIN → target is member → seat availability → atomic mutation

All mutations execute in a single Prisma transaction so reassign has no
observable intermediate state.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from prisma import Prisma

from src.core.database import db as default_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error codes (match the catalog in the design doc)
# ---------------------------------------------------------------------------

SEAT_MANAGEMENT_FORBIDDEN = "SEAT_MANAGEMENT_FORBIDDEN"
TARGET_NOT_MEMBER = "TARGET_NOT_MEMBER"
INSUFFICIENT_SEATS = "INSUFFICIENT_SEATS"
TARGET_ALREADY_HAS_PLUS_SEAT = "TARGET_ALREADY_HAS_PLUS_SEAT"
TARGET_DOES_NOT_HAVE_PLUS_SEAT = "TARGET_DOES_NOT_HAVE_PLUS_SEAT"


class SeatServiceError(Exception):
    """Structured error raised by seat service operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Read APIs
# ---------------------------------------------------------------------------


async def get_seat_tier(
    user_id: str,
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> str:
    """Return the Seat_Tier for a user in a Circle.

    Returns ``"FREE_SEAT"`` for non-members or any lookup failure so that
    the tier resolver never raises — authorization errors belong to the
    per-route membership check, not the tier resolver.
    """
    client = db_client or default_db

    try:
        member = await client.circlemember.find_unique(
            where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
        )
    except Exception:
        logger.exception(
            "get_seat_tier: failed to read CircleMember for user_id=%s circle_id=%s",
            user_id,
            circle_id,
        )
        return "FREE_SEAT"

    if member is None or getattr(member, "seatTier", None) is None:
        return "FREE_SEAT"

    return str(member.seatTier)


async def list_seats(
    circle_id: str,
    actor_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """List all PLUS_SEAT assignments in a Circle.

    Only OWNER or ADMIN may call this. Returns a dict matching the
    ``SeatListResponse`` schema shape.

    Raises:
        SeatServiceError: With code SEAT_MANAGEMENT_FORBIDDEN (403) if the
            actor is not OWNER or ADMIN.
    """
    client = db_client or default_db

    # Auth check: actor must be OWNER or ADMIN
    actor_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": actor_user_id}}
    )
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage seats.",
            status_code=403,
        )

    # Fetch the Circle for pool metadata
    circle = await client.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise SeatServiceError(
            code="CIRCLE_NOT_FOUND",
            message="Circle not found.",
            status_code=404,
        )

    # Fetch all members with PLUS_SEAT
    plus_members = await client.circlemember.find_many(
        where={"circleId": circle_id, "seatTier": "PLUS_SEAT"},
        include={"user": True},
        order={"joinedAt": "asc"},
    )

    seats: list[dict[str, Any]] = []
    for idx, member in enumerate(plus_members, start=1):
        user = getattr(member, "user", None)
        seats.append(
            {
                "seatIndex": idx,
                "assignedToUserId": member.userId,
                "assignedToName": getattr(user, "name", None) if user else None,
                "seatTier": "PLUS_SEAT",
                "backedByAddonId": None,  # Task 7.1 will populate this
                "assignedAt": member.joinedAt,
            }
        )

    return {
        "circleId": circle_id,
        "seatPoolSize": circle.seatPoolSize or 0,
        "assignedSeatCount": len(seats),
        "circlePlanActive": circle.circlePlanActive or False,
        "seats": seats,
    }


# ---------------------------------------------------------------------------
# Mutation APIs
# ---------------------------------------------------------------------------


async def assign_seat(
    circle_id: str,
    target_user_id: str,
    actor_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Assign a PLUS_SEAT to a Circle member.

    Validation order:
        1. Auth: actor must be OWNER or ADMIN → SEAT_MANAGEMENT_FORBIDDEN (403)
        2. Target must be a member → TARGET_NOT_MEMBER (400)
        3. Target must not already hold a PLUS_SEAT → TARGET_ALREADY_HAS_PLUS_SEAT (409)
        4. Seat pool must have availability → INSUFFICIENT_SEATS (409)
        5. Atomic mutation

    Returns the updated member record as a dict.
    """
    client = db_client or default_db

    # 1. Auth check
    actor_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": actor_user_id}}
    )
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage seats.",
            status_code=403,
        )

    # 2. Target membership check
    target_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}}
    )
    if target_member is None:
        raise SeatServiceError(
            code=TARGET_NOT_MEMBER,
            message="The target user is not a member of this Circle.",
            status_code=400,
        )

    # 3. Target must not already have PLUS_SEAT
    if str(target_member.seatTier) == "PLUS_SEAT":
        raise SeatServiceError(
            code=TARGET_ALREADY_HAS_PLUS_SEAT,
            message="The target user already holds a Plus seat in this Circle.",
            status_code=409,
        )

    # 4. Seat availability check
    circle = await client.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise SeatServiceError(
            code="CIRCLE_NOT_FOUND",
            message="Circle not found.",
            status_code=404,
        )

    assigned_count = await client.circlemember.count(
        where={"circleId": circle_id, "seatTier": "PLUS_SEAT"}
    )
    pool_size = circle.seatPoolSize or 0
    if assigned_count >= pool_size:
        raise SeatServiceError(
            code=INSUFFICIENT_SEATS,
            message=(
                f"No available Plus seats. "
                f"{assigned_count}/{pool_size} seats are assigned."
            ),
            status_code=409,
        )

    # 5. Atomic mutation
    updated = await client.circlemember.update(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}},
        data={"seatTier": "PLUS_SEAT"},
    )

    logger.info(
        "assign_seat: user_id=%s circle_id=%s by actor=%s",
        target_user_id,
        circle_id,
        actor_user_id,
    )

    return {
        "userId": updated.userId,
        "circleId": updated.circleId,
        "seatTier": str(updated.seatTier),
        "role": str(updated.role),
    }


async def unassign_seat(
    circle_id: str,
    target_user_id: str,
    actor_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Unassign a PLUS_SEAT from a Circle member (revert to FREE_SEAT).

    Validation order:
        1. Auth: actor must be OWNER or ADMIN → SEAT_MANAGEMENT_FORBIDDEN (403)
        2. Target must be a member → TARGET_NOT_MEMBER (400)
        3. Target must currently hold a PLUS_SEAT → TARGET_DOES_NOT_HAVE_PLUS_SEAT (409)
        4. Atomic mutation

    Returns the updated member record as a dict.
    """
    client = db_client or default_db

    # 1. Auth check
    actor_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": actor_user_id}}
    )
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage seats.",
            status_code=403,
        )

    # 2. Target membership check
    target_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}}
    )
    if target_member is None:
        raise SeatServiceError(
            code=TARGET_NOT_MEMBER,
            message="The target user is not a member of this Circle.",
            status_code=400,
        )

    # 3. Target must currently hold PLUS_SEAT
    if str(target_member.seatTier) != "PLUS_SEAT":
        raise SeatServiceError(
            code=TARGET_DOES_NOT_HAVE_PLUS_SEAT,
            message="The target user does not hold a Plus seat in this Circle.",
            status_code=409,
        )

    # 4. Atomic mutation
    updated = await client.circlemember.update(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}},
        data={"seatTier": "FREE_SEAT"},
    )

    logger.info(
        "unassign_seat: user_id=%s circle_id=%s by actor=%s",
        target_user_id,
        circle_id,
        actor_user_id,
    )

    return {
        "userId": updated.userId,
        "circleId": updated.circleId,
        "seatTier": str(updated.seatTier),
        "role": str(updated.role),
    }


async def reassign_seat(
    circle_id: str,
    from_user_id: str,
    to_user_id: str,
    actor_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Atomically reassign a PLUS_SEAT from one member to another.

    Performs unassign + assign in a single transaction with no observable
    intermediate state. The seat pool count does not change.

    Validation order:
        1. Auth: actor must be OWNER or ADMIN → SEAT_MANAGEMENT_FORBIDDEN (403)
        2. Source must be a member with PLUS_SEAT → TARGET_NOT_MEMBER / TARGET_DOES_NOT_HAVE_PLUS_SEAT
        3. Destination must be a member without PLUS_SEAT → TARGET_NOT_MEMBER / TARGET_ALREADY_HAS_PLUS_SEAT
        4. Atomic transaction (unassign source + assign destination)

    Returns a dict with both updated member records.
    """
    client = db_client or default_db

    # 1. Auth check
    actor_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": actor_user_id}}
    )
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage seats.",
            status_code=403,
        )

    # 2. Source membership + seat check
    from_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": from_user_id}}
    )
    if from_member is None:
        raise SeatServiceError(
            code=TARGET_NOT_MEMBER,
            message="The source user is not a member of this Circle.",
            status_code=400,
        )
    if str(from_member.seatTier) != "PLUS_SEAT":
        raise SeatServiceError(
            code=TARGET_DOES_NOT_HAVE_PLUS_SEAT,
            message="The source user does not hold a Plus seat in this Circle.",
            status_code=409,
        )

    # 3. Destination membership + seat check
    to_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": to_user_id}}
    )
    if to_member is None:
        raise SeatServiceError(
            code=TARGET_NOT_MEMBER,
            message="The destination user is not a member of this Circle.",
            status_code=400,
        )
    if str(to_member.seatTier) == "PLUS_SEAT":
        raise SeatServiceError(
            code=TARGET_ALREADY_HAS_PLUS_SEAT,
            message="The destination user already holds a Plus seat in this Circle.",
            status_code=409,
        )

    # 4. Atomic transaction: unassign from source, assign to destination
    async with client.tx() as tx:
        updated_from = await tx.circlemember.update(
            where={"circleId_userId": {"circleId": circle_id, "userId": from_user_id}},
            data={"seatTier": "FREE_SEAT"},
        )
        updated_to = await tx.circlemember.update(
            where={"circleId_userId": {"circleId": circle_id, "userId": to_user_id}},
            data={"seatTier": "PLUS_SEAT"},
        )

    logger.info(
        "reassign_seat: from=%s to=%s circle_id=%s by actor=%s",
        from_user_id,
        to_user_id,
        circle_id,
        actor_user_id,
    )

    return {
        "from": {
            "userId": updated_from.userId,
            "circleId": updated_from.circleId,
            "seatTier": str(updated_from.seatTier),
        },
        "to": {
            "userId": updated_to.userId,
            "circleId": updated_to.circleId,
            "seatTier": str(updated_to.seatTier),
        },
    }


# ---------------------------------------------------------------------------
# Helper: release seat on member removal
# ---------------------------------------------------------------------------


async def release_seat_on_member_remove(
    circle_id: str,
    target_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> bool:
    """Release a PLUS_SEAT when a member is removed or leaves.

    Called by circle_service member-remove/leave paths and by
    moderation_service on platform-wide ban.

    Returns True if a PLUS_SEAT was released, False if the member had
    FREE_SEAT or was not found.
    """
    client = db_client or default_db

    member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}}
    )
    if member is None:
        return False

    if str(member.seatTier) != "PLUS_SEAT":
        return False

    # Revert to FREE_SEAT before the member row is deleted by the caller
    await client.circlemember.update(
        where={"circleId_userId": {"circleId": circle_id, "userId": target_user_id}},
        data={"seatTier": "FREE_SEAT"},
    )

    logger.info(
        "release_seat_on_member_remove: user_id=%s circle_id=%s",
        target_user_id,
        circle_id,
    )
    return True


# ---------------------------------------------------------------------------
# Seat pool reconciliation (Task 3.8)
# ---------------------------------------------------------------------------

# Number of PLUS_SEATs included in an active Circle Plan
PLAN_INCLUDED_SEATS = 4


async def reconcile_seat_pool_on_addon_change(
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Recompute ``seatPoolSize`` and trim excess PLUS_SEATs if needed.

    Called at plan/add-on period end or when an add-on is cancelled.

    Logic:
        1. Count active add-on seats (non-cancelled CircleSeatAddon rows)
        2. If Circle has active plan, pool = PLAN_INCLUDED_SEATS + addon_count
           else pool = addon_count
        3. If assigned PLUS_SEATs > new pool, unassign the most recently
           assigned seats (by joinedAt desc) and notify affected members
        4. Update Circle.seatPoolSize

    Returns a summary dict with the new pool size and any unassigned users.
    """
    client = db_client or default_db

    circle = await client.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise SeatServiceError(
            code="CIRCLE_NOT_FOUND",
            message="Circle not found.",
            status_code=404,
        )

    # Count active add-on seats
    active_addons = await client.circleseataddon.count(
        where={
            "circleId": circle_id,
            "status": {"in": ["ACTIVE", "TRIALING"]},
        }
    )

    # Compute new pool size
    plan_seats = PLAN_INCLUDED_SEATS if circle.circlePlanActive else 0
    new_pool_size = plan_seats + active_addons

    # Get currently assigned PLUS_SEATs ordered by joinedAt desc (most recent first)
    assigned_members = await client.circlemember.find_many(
        where={"circleId": circle_id, "seatTier": "PLUS_SEAT"},
        order={"joinedAt": "desc"},
    )
    assigned_count = len(assigned_members)

    unassigned_users: list[str] = []

    # If assigned exceeds new pool, trim the most recently assigned
    if assigned_count > new_pool_size:
        excess = assigned_count - new_pool_size
        members_to_unassign = assigned_members[:excess]

        for member in members_to_unassign:
            # Don't unassign the OWNER if they have a plan seat
            if str(member.role) == "OWNER" and circle.circlePlanActive:
                continue
            await client.circlemember.update(
                where={"circleId_userId": {"circleId": circle_id, "userId": member.userId}},
                data={"seatTier": "FREE_SEAT"},
            )
            unassigned_users.append(member.userId)

    # Update the Circle's seatPoolSize
    await client.circle.update(
        where={"id": circle_id},
        data={"seatPoolSize": new_pool_size},
    )

    logger.info(
        "reconcile_seat_pool: circle_id=%s new_pool=%d unassigned=%d",
        circle_id,
        new_pool_size,
        len(unassigned_users),
    )

    return {
        "circleId": circle_id,
        "newSeatPoolSize": new_pool_size,
        "previousAssignedCount": assigned_count,
        "unassignedUsers": unassigned_users,
    }


async def activate_circle_plan_seats(
    circle_id: str,
    owner_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Set up seat pool when a Circle Plan activates.

    On ``circlePlanActive`` transition to true:
        - Ensure owner.seatTier == PLUS_SEAT (seat 1 auto-assigned)
        - Set seatPoolSize to include the 4 plan seats + any existing add-ons

    Called by circle_billing_service on plan activation.
    """
    client = db_client or default_db

    # Ensure owner has PLUS_SEAT
    owner_member = await client.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": owner_user_id}}
    )
    if owner_member and str(owner_member.seatTier) != "PLUS_SEAT":
        await client.circlemember.update(
            where={"circleId_userId": {"circleId": circle_id, "userId": owner_user_id}},
            data={"seatTier": "PLUS_SEAT"},
        )

    # Count active add-ons
    active_addons = await client.circleseataddon.count(
        where={
            "circleId": circle_id,
            "status": {"in": ["ACTIVE", "TRIALING"]},
        }
    )

    new_pool_size = PLAN_INCLUDED_SEATS + active_addons

    # Update Circle
    await client.circle.update(
        where={"id": circle_id},
        data={
            "circlePlanActive": True,
            "seatPoolSize": new_pool_size,
        },
    )

    logger.info(
        "activate_circle_plan_seats: circle_id=%s pool=%d owner=%s",
        circle_id,
        new_pool_size,
        owner_user_id,
    )

    return {
        "circleId": circle_id,
        "seatPoolSize": new_pool_size,
        "ownerSeatTier": "PLUS_SEAT",
    }


async def deactivate_circle_plan_seats(
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Revert non-add-on PLUS_SEATs when a Circle Plan expires.

    On plan cancel / dunning expiry at period end:
        - Set circlePlanActive = false
        - Recompute seatPoolSize (only add-on seats remain)
        - Revert PLUS_SEATs that exceed the new pool to FREE_SEAT

    Called by circle_billing_service at period end.
    """
    client = db_client or default_db

    # Mark plan as inactive
    await client.circle.update(
        where={"id": circle_id},
        data={"circlePlanActive": False},
    )

    # Reconcile will handle the rest
    return await reconcile_seat_pool_on_addon_change(circle_id, db_client=client)
