"""
Circle billing routes — Circle Plan and Plus Seat add-on management.

POST /circles/{id}/billing/circle-plan       — Purchase Circle Plan
DELETE /circles/{id}/billing/circle-plan      — Cancel Circle Plan
POST /circles/{id}/billing/seat-addons       — Purchase Plus Seat add-on
DELETE /circles/{id}/billing/seat-addons/{addon_id} — Cancel add-on
GET /circles/{id}/billing/history            — Billing history

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.dependencies import CurrentUser, db
from src.services.circle_billing_service import (
    CircleBillingError,
    cancel_circle_plan as _cancel_circle_plan,
    cancel_seat_addon as _cancel_seat_addon,
    purchase_circle_plan as _purchase_circle_plan,
    purchase_seat_addon as _purchase_seat_addon,
)

router = APIRouter(prefix="/api/v1/circles", tags=["circle-billing"])


class SeatAddonPurchaseBody(BaseModel):
    quantity: int = Field(default=1, ge=1, le=50)


@router.post("/{circle_id}/billing/circle-plan")
async def purchase_circle_plan(
    circle_id: str,
    current_user: CurrentUser,
):
    """Purchase a Circle Plan for this Circle (OWNER or ADMIN)."""
    try:
        return await _purchase_circle_plan(current_user.id, circle_id, db_client=db)
    except CircleBillingError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.delete("/{circle_id}/billing/circle-plan")
async def cancel_circle_plan(
    circle_id: str,
    current_user: CurrentUser,
):
    """Cancel the Circle Plan at period end (OWNER or ADMIN)."""
    try:
        return await _cancel_circle_plan(current_user.id, circle_id, db_client=db)
    except CircleBillingError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.post("/{circle_id}/billing/seat-addons")
async def purchase_seat_addon(
    circle_id: str,
    current_user: CurrentUser,
    body: SeatAddonPurchaseBody | None = None,
):
    """Purchase Plus Seat add-on(s) for this Circle (OWNER or ADMIN)."""
    quantity = body.quantity if body else 1
    try:
        return await _purchase_seat_addon(
            current_user.id, circle_id, quantity=quantity, db_client=db
        )
    except CircleBillingError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.delete("/{circle_id}/billing/seat-addons/{addon_id}")
async def cancel_seat_addon(
    circle_id: str,
    addon_id: str,
    current_user: CurrentUser,
):
    """Cancel a Plus Seat add-on at period end (OWNER or ADMIN)."""
    try:
        return await _cancel_seat_addon(current_user.id, circle_id, addon_id, db_client=db)
    except CircleBillingError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.get("/{circle_id}/billing/history")
async def billing_history(
    circle_id: str,
    current_user: CurrentUser,
):
    """Get billing history for this Circle (OWNER or ADMIN)."""
    # Verify access
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": current_user.id}}
    )
    if member is None or str(member.role) not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SEAT_MANAGEMENT_FORBIDDEN",
                "message": "Only owner or admin can view billing history.",
            },
        )

    # Fetch subscription and add-on records
    subscription = await db.circlesubscription.find_first(
        where={"circleId": circle_id},
        order={"createdAt": "desc"},
    )
    addons = await db.circleseataddon.find_many(
        where={"circleId": circle_id},
        order={"createdAt": "desc"},
    )

    return {
        "circleId": circle_id,
        "subscription": (
            {
                "id": subscription.id,
                "status": subscription.status,
                "currentPeriodEnd": (
                    subscription.currentPeriodEnd.isoformat()
                    if subscription.currentPeriodEnd
                    else None
                ),
                "createdAt": subscription.createdAt.isoformat(),
            }
            if subscription
            else None
        ),
        "addons": [
            {
                "id": a.id,
                "quantity": getattr(a, "quantity", 1),
                "status": a.status,
                "currentPeriodEnd": a.currentPeriodEnd.isoformat() if a.currentPeriodEnd else None,
                "createdAt": a.createdAt.isoformat(),
            }
            for a in addons
        ],
    }
