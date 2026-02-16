"""
Review (spaced repetition) routes.

List due/upcoming reviews (priority-sorted), complete a review with SM-2 quality,
snooze, and retrieve dashboard stats.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from prisma import Client as PrismaClient

from ..dependencies import CurrentUser
from ..services.spaced_repetition_service import (
    advance_review,
    get_review_stats,
    get_strength_label,
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
    easeFactor: float
    lastQuality: int
    lapseCount: int
    strength: str  # "strong" | "moderate" | "weak"
    lastReviewedAt: datetime | None
    scheduleBlockId: str | None

    class Config:
        from_attributes = True


class ReviewListResponse(BaseModel):
    due: list[ReviewItemResponse]
    upcoming: list[ReviewItemResponse]


class CompleteReviewRequest(BaseModel):
    quality: int = Field(
        4,
        ge=0,
        le=5,
        description="SM-2 quality rating: 0=blackout, 1-2=failed, 3=hard, 4=good, 5=easy",
    )
    completedOnTime: bool = Field(True, description="Legacy: True if done on or before due date")
    actualAt: datetime | None = Field(
        None, description="When the review was completed (default: now)"
    )


class SnoozeReviewRequest(BaseModel):
    nextReviewAt: datetime = Field(..., description="New date/time for the next review")


class ForecastDay(BaseModel):
    date: str
    count: int


class StrengthBreakdown(BaseModel):
    strong: int
    moderate: int
    weak: int


class ReviewStatsResponse(BaseModel):
    total: int
    dueToday: int
    dueThisWeek: int
    totalReviewed: int
    averageEaseFactor: float
    estimatedRetention: float
    strength: StrengthBreakdown
    forecast: list[ForecastDay]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _review_to_response(
    r: object, topic: object, course: object, schedule_block: object
) -> ReviewItemResponse:
    """Convert a ReviewItem DB record to a response model."""
    return ReviewItemResponse(
        id=r.id,
        topicId=r.topicId,
        topicTitle=topic.title if topic else "",
        courseId=course.id if course else None,
        courseTitle=course.title if course else None,
        nextReviewAt=r.nextReviewAt,
        intervalDays=r.intervalDays,
        repetitionCount=r.repetitionCount,
        easeFactor=r.easeFactor,
        lastQuality=r.lastQuality,
        lapseCount=r.lapseCount,
        strength=get_strength_label(r.easeFactor, r.intervalDays, r.lapseCount),
        lastReviewedAt=r.lastReviewedAt,
        scheduleBlockId=schedule_block.id if schedule_block else None,
    )


def _priority_sort_key(r: object) -> tuple:
    """
    Sort reviews by priority (most urgent first):
    1. Most overdue first (nextReviewAt ascending — already past items come first)
    2. Weakest items first (lowest ease factor)
    3. Highest lapse count first
    """
    return (r.nextReviewAt, r.easeFactor, -r.lapseCount)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=ReviewStatsResponse)
async def review_stats(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
):
    """Get review statistics and dashboard data for the current user."""
    stats = await get_review_stats(db, current_user.id)
    return ReviewStatsResponse(**stats)


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
    """List the current user's review items: due (within N days) and upcoming, priority-sorted."""
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

    # Sort by priority
    items.sort(key=_priority_sort_key)

    due = []
    upcoming = []
    for r in items:
        topic = r.topic
        course = topic.module.course if topic and topic.module else None
        schedule_block = r.scheduleBlock
        payload = _review_to_response(r, topic, course, schedule_block)
        if r.nextReviewAt <= cutoff:
            due.append(payload)
        else:
            upcoming.append(payload)
    return ReviewListResponse(due=due, upcoming=upcoming)


@router.post("/{review_item_id}/complete", response_model=ReviewItemResponse)
async def complete_review(
    review_item_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    body: CompleteReviewRequest | None = None,
):
    """Mark a review as completed with SM-2 quality rating. Advances spaced repetition and logs behaviour."""
    user_id = current_user.id
    req = body or CompleteReviewRequest()
    try:
        updated = await advance_review(
            db,
            review_item_id=review_item_id,
            user_id=user_id,
            quality=req.quality,
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
    schedule_block = review_with_topic.scheduleBlock if review_with_topic else None
    return _review_to_response(review_with_topic, topic, course, schedule_block)


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
    # Re-fetch to get scheduleBlock for response
    updated_with_block = await db.reviewitem.find_unique(
        where={"id": updated.id},
        include={
            "topic": {"include": {"module": {"include": {"course": True}}}},
            "scheduleBlock": True,
        },
    )
    return _review_to_response(
        updated_with_block,
        updated_with_block.topic if updated_with_block else topic,
        (
            updated_with_block.topic.module.course
            if updated_with_block and updated_with_block.topic and updated_with_block.topic.module
            else course
        ),
        updated_with_block.scheduleBlock if updated_with_block else None,
    )
