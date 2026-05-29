"""
Report and moderation routes.

POST /reports                    — Submit a moderation report
GET /admin/reports?status=PENDING — List pending reports (admin)
POST /admin/reports/{id}/decide  — Decide on a report (admin)

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.dependencies import CurrentUser, db
from src.schemas.circle import ReportSubmitRequest
from src.services.moderation_service import (
    ModerationError,
    decide_report,
    list_pending_reports,
    submit_report,
)

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.post("/reports")
async def create_report(
    body: ReportSubmitRequest,
    current_user: CurrentUser,
):
    """Submit a moderation report."""
    try:
        return await submit_report(
            reporter_user_id=current_user.id,
            target_type=body.target_type.value,
            target_id=body.target_id,
            reason_code=body.reason_code,
            description=body.description,
            db_client=db,
        )
    except ModerationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e


@router.get("/admin/reports")
async def get_pending_reports(
    current_user: CurrentUser,
    report_status: str = Query("PENDING", alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """List pending reports (admin only)."""
    # Admin check
    if str(getattr(current_user, "role", "")) != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return await list_pending_reports(page=page, size=size, db_client=db)


@router.post("/admin/reports/{report_id}/decide")
async def decide_on_report(
    report_id: str,
    body: dict,
    current_user: CurrentUser,
):
    """Decide on a report (admin only). Body: { decision: "UPHELD" | "DISMISSED" }"""
    if str(getattr(current_user, "role", "")) != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    decision = body.get("decision")
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'decision' is required (UPHELD or DISMISSED).",
        )

    try:
        return await decide_report(
            report_id=report_id,
            decision=decision,
            admin_user_id=current_user.id,
            db_client=db,
        )
    except ModerationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        ) from e
