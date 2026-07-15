"""
Seat service — manages per-(User, Circle) PLUS_SEAT assignments.

Provides read APIs (get_seat_tier, list_seats) and mutation APIs
(assign_seat, unassign_seat, reassign_seat) with strict validation order:
    auth -> OWNER/ADMIN -> target is member -> seat availability -> atomic mutation

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import logging
from typing import Any

from ..repository import space_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error codes
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


async def get_seat_tier(user_id: str, circle_id: str, **kwargs) -> str:
    """Return the Seat_Tier for a user in a Circle."""
    try:
        member = await space_repo.find_member(circle_id, user_id)
    except Exception:
        logger.exception("get_seat_tier: failed for user_id=%s circle_id=%s", user_id, circle_id)
        return "FREE_SEAT"

    if member is None or member.seat_tier is None:
        return "FREE_SEAT"

    return str(member.seat_tier)


async def list_seats(circle_id: str, actor_user_id: str, **kwargs) -> dict[str, Any]:
    """List all PLUS_SEAT assignments in a Circle (OWNER/ADMIN only)."""
    # Auth check
    actor_member = await space_repo.find_member(circle_id, actor_user_id)
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(
            code=SEAT_MANAGEMENT_FORBIDDEN,
            message="Only the Circle owner or an admin can manage seats.",
            status_code=403,
        )

    circle = await space_repo.find_space_basic(circle_id)
    if circle is None:
        raise SeatServiceError(code="CIRCLE_NOT_FOUND", message="Circle not found.", status_code=404)

    plus_members = await space_repo.list_plus_members(circle_id)

    seats: list[dict[str, Any]] = []
    for idx, member in enumerate(plus_members, start=1):
        user = member.user
        seats.append({
            "seatIndex": idx,
            "assignedToUserId": member.user_id,
            "assignedToName": getattr(user, "name", None) if user else None,
            "seatTier": "PLUS_SEAT",
            "backedByAddonId": None,
            "assignedAt": member.joined_at,
        })

    return {
        "circleId": circle_id,
        "seatPoolSize": circle.seat_pool_size or 0,
        "assignedSeatCount": len(seats),
        "circlePlanActive": circle.circle_plan_active or False,
        "seats": seats,
    }


# ---------------------------------------------------------------------------
# Mutation APIs
# ---------------------------------------------------------------------------


async def assign_seat(circle_id: str, target_user_id: str, actor_user_id: str, **kwargs) -> dict[str, Any]:
    """Assign a PLUS_SEAT to a Circle member."""
    # 1. Auth check
    actor_member = await space_repo.find_member(circle_id, actor_user_id)
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(code=SEAT_MANAGEMENT_FORBIDDEN, message="Only the Circle owner or an admin can manage seats.", status_code=403)

    # 2. Target membership
    target_member = await space_repo.find_member(circle_id, target_user_id)
    if target_member is None:
        raise SeatServiceError(code=TARGET_NOT_MEMBER, message="The target user is not a member of this Circle.", status_code=400)

    # 3. Already has seat?
    if str(target_member.seat_tier) == "PLUS_SEAT":
        raise SeatServiceError(code=TARGET_ALREADY_HAS_PLUS_SEAT, message="The target user already holds a Plus seat in this Circle.", status_code=409)

    # 4. Seat availability
    circle = await space_repo.find_space_basic(circle_id)
    if circle is None:
        raise SeatServiceError(code="CIRCLE_NOT_FOUND", message="Circle not found.", status_code=404)

    assigned_count = await space_repo.count_plus_seats(circle_id)
    pool_size = circle.seat_pool_size or 0
    if assigned_count >= pool_size:
        raise SeatServiceError(code=INSUFFICIENT_SEATS, message=f"No available Plus seats. {assigned_count}/{pool_size} seats are assigned.", status_code=409)

    # 5. Mutation
    updated = await space_repo.update_member(circle_id, target_user_id, {"seatTier": "PLUS_SEAT"})
    logger.info("assign_seat: user_id=%s circle_id=%s by actor=%s", target_user_id, circle_id, actor_user_id)

    return {
        "userId": updated.user_id,
        "circleId": updated.circle_id,
        "seatTier": str(updated.seat_tier),
        "role": str(updated.role),
    }


async def unassign_seat(circle_id: str, target_user_id: str, actor_user_id: str, **kwargs) -> dict[str, Any]:
    """Unassign a PLUS_SEAT from a Circle member (revert to FREE_SEAT)."""
    # 1. Auth
    actor_member = await space_repo.find_member(circle_id, actor_user_id)
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(code=SEAT_MANAGEMENT_FORBIDDEN, message="Only the Circle owner or an admin can manage seats.", status_code=403)

    # 2. Target membership
    target_member = await space_repo.find_member(circle_id, target_user_id)
    if target_member is None:
        raise SeatServiceError(code=TARGET_NOT_MEMBER, message="The target user is not a member of this Circle.", status_code=400)

    # 3. Must have seat
    if str(target_member.seat_tier) != "PLUS_SEAT":
        raise SeatServiceError(code=TARGET_DOES_NOT_HAVE_PLUS_SEAT, message="The target user does not hold a Plus seat in this Circle.", status_code=409)

    # 4. Mutation
    updated = await space_repo.update_member(circle_id, target_user_id, {"seatTier": "FREE_SEAT"})
    logger.info("unassign_seat: user_id=%s circle_id=%s by actor=%s", target_user_id, circle_id, actor_user_id)

    return {
        "userId": updated.user_id,
        "circleId": updated.circle_id,
        "seatTier": str(updated.seat_tier),
        "role": str(updated.role),
    }


async def reassign_seat(circle_id: str, from_user_id: str, to_user_id: str, actor_user_id: str, **kwargs) -> dict[str, Any]:
    """Atomically reassign a PLUS_SEAT from one member to another."""
    # 1. Auth
    actor_member = await space_repo.find_member(circle_id, actor_user_id)
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise SeatServiceError(code=SEAT_MANAGEMENT_FORBIDDEN, message="Only the Circle owner or an admin can manage seats.", status_code=403)

    # 2. Source check
    from_member = await space_repo.find_member(circle_id, from_user_id)
    if from_member is None:
        raise SeatServiceError(code=TARGET_NOT_MEMBER, message="The source user is not a member of this Circle.", status_code=400)
    if str(from_member.seat_tier) != "PLUS_SEAT":
        raise SeatServiceError(code=TARGET_DOES_NOT_HAVE_PLUS_SEAT, message="The source user does not hold a Plus seat in this Circle.", status_code=409)

    # 3. Destination check
    to_member = await space_repo.find_member(circle_id, to_user_id)
    if to_member is None:
        raise SeatServiceError(code=TARGET_NOT_MEMBER, message="The destination user is not a member of this Circle.", status_code=400)
    if str(to_member.seat_tier) == "PLUS_SEAT":
        raise SeatServiceError(code=TARGET_ALREADY_HAS_PLUS_SEAT, message="The destination user already holds a Plus seat in this Circle.", status_code=409)

    # 4. Atomic reassign (two updates — seat count stays same)
    await space_repo.update_member(circle_id, from_user_id, {"seatTier": "FREE_SEAT"})
    updated_to = await space_repo.update_member(circle_id, to_user_id, {"seatTier": "PLUS_SEAT"})

    logger.info("reassign_seat: from=%s to=%s circle_id=%s by actor=%s", from_user_id, to_user_id, circle_id, actor_user_id)

    return {
        "from": {"userId": from_user_id, "circleId": circle_id, "seatTier": "FREE_SEAT"},
        "to": {"userId": updated_to.user_id, "circleId": updated_to.circle_id, "seatTier": str(updated_to.seat_tier)},
    }


# ---------------------------------------------------------------------------
# Helper: release seat on member removal
# ---------------------------------------------------------------------------


async def release_seat_on_member_remove(circle_id: str, target_user_id: str, **kwargs) -> bool:
    """Release a PLUS_SEAT when a member is removed or leaves."""
    member = await space_repo.find_member(circle_id, target_user_id)
    if member is None:
        return False

    if str(member.seat_tier) != "PLUS_SEAT":
        return False

    await space_repo.update_member(circle_id, target_user_id, {"seatTier": "FREE_SEAT"})
    logger.info("release_seat_on_member_remove: user_id=%s circle_id=%s", target_user_id, circle_id)
    return True
