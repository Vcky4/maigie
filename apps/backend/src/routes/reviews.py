"""
Review (spaced repetition) routes.

List due/upcoming reviews, complete a review, or snooze.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from prisma import Client as PrismaClient

from ..dependencies import CurrentUser
from ..services.spaced_repetition_service import (
    advance_review,
    log_behaviour,
)
from ..utils.dependencies import get_db_client
from ..utils.exceptions import ResourceNotFoundError

router = APIRouter(tags=["reviews"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReviewItemResponse(BaseModel):
    id: str
    topicId: str
    topicTitle: str
    courseId: str | None
    courseTitle: str | None
    nextReviewAt: datetime
    intervalDays: int
    repetitionCount: int
    lastReviewedAt: datetime | None
    scheduleBlockId: str | None

    class Config:
        from_attributes = True


class ReviewListResponse(BaseModel):
    due: list[ReviewItemResponse]
    upcoming: list[ReviewItemResponse]


class CompleteReviewRequest(BaseModel):
    completedOnTime: bool = Field(True, description="True if done on or before due date")
    actualAt: datetime | None = Field(
        None, description="When the review was completed (default: now)"
    )


class SnoozeReviewRequest(BaseModel):
    nextReviewAt: datetime = Field(..., description="New date/time for the next review")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ReviewListResponse)
async def list_review_items(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    due_within_days: int = Query(
        7,
        ge=1,
        le=90,
        alias="dueWithinDays",
        description="Treat as 'due' if nextReviewAt within this many days",
    ),
):
    """List the current user's review items: due (within N days) and upcoming."""
    user_id = current_user.id
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=due_within_days)

    items = await db.reviewitem.find_many(
        where={"userId": user_id},
        include={
            "topic": {"include": {"module": {"include": {"course": True}}}},
            "scheduleBlock": True,
        },
        order={"nextReviewAt": "asc"},
    )
    due = []
    upcoming = []
    for r in items:
        course = r.topic.module.course if r.topic and r.topic.module else None
        payload = {
            "id": r.id,
            "topicId": r.topicId,
            "topicTitle": r.topic.title if r.topic else "",
            "courseId": course.id if course else None,
            "courseTitle": course.title if course else None,
            "nextReviewAt": r.nextReviewAt,
            "intervalDays": r.intervalDays,
            "repetitionCount": r.repetitionCount,
            "lastReviewedAt": r.lastReviewedAt,
            "scheduleBlockId": r.scheduleBlock.id if r.scheduleBlock else None,
        }
        if r.nextReviewAt <= cutoff:
            due.append(ReviewItemResponse(**payload))
        else:
            upcoming.append(ReviewItemResponse(**payload))
    return ReviewListResponse(due=due, upcoming=upcoming)


@router.post("/{review_item_id}/complete", response_model=ReviewItemResponse)
async def complete_review(
    review_item_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    body: CompleteReviewRequest | None = None,
):
    """Mark a review as completed. Advances spaced repetition and logs behaviour."""
    user_id = current_user.id
    req = body or CompleteReviewRequest()
    try:
        updated = await advance_review(
            db,
            review_item_id=review_item_id,
            user_id=user_id,
            completed_on_time=req.completedOnTime,
            actual_at=req.actualAt,
        )
    except ValueError as e:
        raise ResourceNotFoundError("Review item", review_item_id) from e
    review_with_topic = await db.reviewitem.find_unique(
        where={"id": updated.id},
        include={
            "topic": {"include": {"module": {"include": {"course": True}}}},
            "scheduleBlock": True,
        },
    )
    topic = review_with_topic.topic if review_with_topic else None
    course = topic.module.course if topic and topic.module else None
    return ReviewItemResponse(
        id=updated.id,
        topicId=updated.topicId,
        topicTitle=topic.title if topic else "",
        courseId=course.id if course else None,
        courseTitle=course.title if course else None,
        nextReviewAt=updated.nextReviewAt,
        intervalDays=updated.intervalDays,
        repetitionCount=updated.repetitionCount,
        lastReviewedAt=updated.lastReviewedAt,
        scheduleBlockId=review_with_topic.scheduleBlock.id if review_with_topic and review_with_topic.scheduleBlock else None,
    )


@router.post("/{review_item_id}/snooze", response_model=ReviewItemResponse)
async def snooze_review(
    review_item_id: str,
    body: SnoozeReviewRequest,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """Reschedule the next review to a later date. Logs RESCHEDULED behaviour."""
    user_id = current_user.id
    review = await db.reviewitem.find_first(
        where={"id": review_item_id, "userId": user_id},
        include={
            "topic": {"include": {"module": {"include": {"course": True}}}},
            "scheduleBlock": True,
        },
    )
    if not review:
        raise ResourceNotFoundError("Review item", review_item_id)
    topic = review.topic
    course = topic.module.course if topic and topic.module else None
    await log_behaviour(
        db,
        user_id=user_id,
        behaviour_type="RESCHEDULED",
        entity_type="review",
        entity_id=review_item_id,
        scheduled_at=review.nextReviewAt,
        actual_at=None,
        metadata={
            "topicId": review.topicId,
            "topicTitle": topic.title if topic else "",
            "newNextReviewAt": body.nextReviewAt.isoformat(),
        },
    )
    updated = await db.reviewitem.update(
        where={"id": review_item_id},
        data={"nextReviewAt": body.nextReviewAt},
    )
    # Re-fetch to get scheduleBlock for response (updated has no relation)
    updated_with_block = await db.reviewitem.find_unique(
        where={"id": updated.id},
        include={"scheduleBlock": True},
    )
    return ReviewItemResponse(
        id=updated.id,
        topicId=updated.topicId,
        topicTitle=topic.title if topic else "",
        courseId=course.id if course else None,
        courseTitle=course.title if course else None,
        nextReviewAt=updated.nextReviewAt,
        intervalDays=updated.intervalDays,
        repetitionCount=updated.repetitionCount,
        lastReviewedAt=updated.lastReviewedAt,
        scheduleBlockId=updated_with_block.scheduleBlock.id if updated_with_block and updated_with_block.scheduleBlock else None,
    )
