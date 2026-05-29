"""
Circle seat management routes.

GET /circles/{id}/seats           — List all PLUS_SEATs
POST /circles/{id}/seats/assign   — Assign a PLUS_SEAT to a member
POST /circles/{id}/seats/unassign — Unassign a PLUS_SEAT from a member
POST /circles/{id}/seats/reassign — Reassign a PLUS_SEAT between members

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.dependencies import CurrentUser, db
from src.schemas.circle import SeatAssignRequest, SeatReassignRequest
from src.services.seat_service import (
    SeatServiceError,
    assign_seat,
    list_seats,
    reassign_seat,
    unassign_seat,
)

router = APIRouter(prefix="/api/v1/circles", tags=["circle-seats"])


@router.get("/{circle_id}/seats")
async def get_seats(
    circle_id: str,
    current_user: CurrentUser,
):
    """List all PLUS_SEAT assignments in a Circle (OWNER/ADMIN only)."""
    try:
        return await list_seats(circle_id, current_user.id, db_client=db)
    except SeatServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.post("/{circle_id}/seats/assign")
async def assign(
    circle_id: str,
    body: SeatAssignRequest,
    current_user: CurrentUser,
):
    """Assign a PLUS_SEAT to a Circle member."""
    try:
        return await assign_seat(
            circle_id, body.target_user_id, current_user.id, db_client=db
        )
    except SeatServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.post("/{circle_id}/seats/unassign")
async def unassign(
    circle_id: str,
    body: SeatAssignRequest,
    current_user: CurrentUser,
):
    """Unassign a PLUS_SEAT from a Circle member (revert to FREE_SEAT)."""
    try:
        return await unassign_seat(
            circle_id, body.target_user_id, current_user.id, db_client=db
        )
    except SeatServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.post("/{circle_id}/seats/reassign")
async def reassign(
    circle_id: str,
    body: SeatReassignRequest,
    current_user: CurrentUser,
):
    """Atomically reassign a PLUS_SEAT from one member to another."""
    try:
        return await reassign_seat(
            circle_id,
            body.from_user_id,
            body.to_user_id,
            current_user.id,
            db_client=db,
        )
    except SeatServiceError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


# ---------------------------------------------------------------------------
# Per-member usage analytics (Task 11.1)
# ---------------------------------------------------------------------------


@router.get("/{circle_id}/usage/members")
async def get_per_member_usage(
    circle_id: str,
    current_user: CurrentUser,
    window: int = 30,
):
    """Get per-member AI usage for a Circle (OWNER/ADMIN only).

    Free Circles return only request_count and active_days.
    Plan Circles return detailed token/model/feature breakdowns.
    """
    from src.services.seat_service import SeatServiceError

    # Auth check: must be OWNER or ADMIN
    actor_member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": current_user.id}}
    )
    if actor_member is None or str(actor_member.role) not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "USAGE_VISIBILITY_FORBIDDEN", "message": "Only the Circle owner or an admin can view usage analytics."},
        )

    # Determine detail level based on plan state
    circle = await db.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found.")

    detail_level = "detailed" if circle.circlePlanActive else "basic"

    from src.services.usage_tracking_service import get_per_member_usage as _get_usage

    results = await _get_usage(
        circle_id, window_days=window, detail_level=detail_level, db_client=db
    )
    return {"circleId": circle_id, "window": window, "members": results}
