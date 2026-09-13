"""Feedback domain — API routes.

Submission is a learner action; listing, reading and triage are staff-only. Mounted at
``/api/v1/feedback`` — the path the admin client already calls.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from src.shared.auth import CurrentUser, StaffUser
from src.shared.database import get_session_factory, ilike_any

from . import models
from .db_models import Feedback

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


def _naive_utc_now() -> datetime:
    """UTC now without tzinfo — the Feedback timestamp columns are ``timestamp without time zone``."""
    return datetime.now(UTC).replace(tzinfo=None)


def _response(row: Feedback) -> models.FeedbackResponse:
    return models.FeedbackResponse(
        id=row.id,
        userId=row.user_id,
        type=row.type,
        title=row.title,
        description=row.description,
        status=row.status,
        pageUrl=row.page_url,
        metadata=row.metadata_json,
        adminNotes=row.admin_notes,
        resolvedAt=row.resolved_at,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


@router.post("", response_model=models.FeedbackResponse, status_code=201)
async def submit_feedback(
    body: models.FeedbackCreateRequest, current_user: CurrentUser, request: Request
):
    """Submit feedback (any authenticated learner)."""
    if body.type not in models.FEEDBACK_TYPES:
        raise HTTPException(status_code=400, detail="Invalid feedback type")
    if not body.title.strip() or not body.description.strip():
        raise HTTPException(status_code=400, detail="Title and description are required")

    now = _naive_utc_now()
    row = Feedback(
        user_id=current_user.id,
        type=body.type,
        title=body.title.strip(),
        description=body.description.strip(),
        page_url=body.pageUrl,
        user_agent=request.headers.get("user-agent"),
        metadata_json=body.metadata,
        status="PENDING",
        created_at=now,
        updated_at=now,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _response(row)


@router.get("", response_model=models.FeedbackListResponse)
async def list_feedback(
    admin_user: StaffUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    type: str | None = Query(None),
    search: str | None = Query(None),
):
    """List feedback, paginated and filterable (staff only)."""
    conditions = []
    if status:
        conditions.append(Feedback.status == status)
    if type:
        conditions.append(Feedback.type == type)
    if search:
        conditions.append(ilike_any(search, Feedback.title, Feedback.description))

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(Feedback).where(*conditions))
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(Feedback)
                    .where(*conditions)
                    .order_by(Feedback.created_at.desc())
                    .offset((page - 1) * pageSize)
                    .limit(pageSize)
                )
            )
            .scalars()
            .all()
        )

    return models.FeedbackListResponse(
        feedback=[_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=pageSize,
        hasMore=(page * pageSize) < total,
    )


@router.get("/{feedback_id}", response_model=models.FeedbackResponse)
async def get_feedback(feedback_id: str, admin_user: StaffUser):
    """Read one feedback item (staff only)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(Feedback).where(Feedback.id == feedback_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return _response(row)


@router.patch("/{feedback_id}", response_model=models.FeedbackResponse)
async def update_feedback(
    feedback_id: str, body: models.FeedbackUpdateRequest, admin_user: StaffUser
):
    """Triage a feedback item — status and/or admin notes (staff only), audited."""
    from src.domains.admin.services.audit_service import log_admin_action

    if body.status is not None and body.status not in models.FEEDBACK_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid feedback status")

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(Feedback).where(Feedback.id == feedback_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Feedback not found")

        before = {"status": row.status, "adminNotes": row.admin_notes}
        if body.status is not None:
            row.status = body.status
            # Stamp resolution when it lands on RESOLVED, and clear it if reopened, so the timestamp
            # never claims a resolution that was undone. Naive UTC, matching the column.
            row.resolved_at = _naive_utc_now() if body.status == "RESOLVED" else None
        if body.adminNotes is not None:
            row.admin_notes = body.adminNotes
        row.updated_at = _naive_utc_now()
        await session.commit()
        await session.refresh(row)

    await log_admin_action(
        admin_user_id=admin_user.id,
        action="update_feedback",
        resource_type="feedback",
        resource_id=feedback_id,
        details={
            "before": before,
            "after": {"status": row.status, "adminNotes": row.admin_notes},
        },
    )
    return _response(row)
