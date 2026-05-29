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

from src.dependencies import CurrentUser, db

router = APIRouter(prefix="/api/v1/circles", tags=["circle-billing"])


@router.post("/{circle_id}/billing/circle-plan")
async def purchase_circle_plan(
    circle_id: str,
    current_user: CurrentUser,
):
    """Purchase a Circle Plan for this Circle (OWNER or ADMIN)."""
    # TODO: Task 7.1 — wire to circle_billing_service.purchase_circle_plan
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Circle Plan purchase is not yet implemented.",
    )


@router.delete("/{circle_id}/billing/circle-plan")
async def cancel_circle_plan(
    circle_id: str,
    current_user: CurrentUser,
):
    """Cancel the Circle Plan at period end (OWNER or ADMIN)."""
    # TODO: Task 7.1 — wire to circle_billing_service.cancel_circle_plan
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Circle Plan cancellation is not yet implemented.",
    )


@router.post("/{circle_id}/billing/seat-addons")
async def purchase_seat_addon(
    circle_id: str,
    current_user: CurrentUser,
    body: dict | None = None,
):
    """Purchase Plus Seat add-on(s) for this Circle (OWNER or ADMIN)."""
    # TODO: Task 7.1 — wire to circle_billing_service.purchase_seat_addon
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Plus Seat add-on purchase is not yet implemented.",
    )


@router.delete("/{circle_id}/billing/seat-addons/{addon_id}")
async def cancel_seat_addon(
    circle_id: str,
    addon_id: str,
    current_user: CurrentUser,
):
    """Cancel a Plus Seat add-on at period end (OWNER or ADMIN)."""
    # TODO: Task 7.1 — wire to circle_billing_service.cancel_seat_addon
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Plus Seat add-on cancellation is not yet implemented.",
    )


@router.get("/{circle_id}/billing/history")
async def billing_history(
    circle_id: str,
    current_user: CurrentUser,
):
    """Get billing history for this Circle (OWNER or ADMIN)."""
    # TODO: Task 7.1 — wire to circle_billing_service
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Billing history is not yet implemented.",
    )
