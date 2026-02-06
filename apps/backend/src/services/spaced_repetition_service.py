"""
Spaced repetition service: review scheduling and behaviour logging.

- Creates ReviewItems when a topic is completed (one review schedule per topic).
- Advances intervals (1, 3, 7, 14, 30 days) on each completed review.
- Logs schedule behaviour (on-time, late, skipped, rescheduled) for AI learning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from prisma import Prisma


# Default spaced repetition intervals (days): 1, 3, 7, 14, 30 then repeat 30
DEFAULT_INTERVALS_DAYS = [1, 3, 7, 14, 30]
REVIEW_BLOCK_DURATION_MINUTES = 30


def get_next_interval_days(repetition_count: int) -> int:
    """Return the next interval in days for a given repetition count (0-indexed)."""
    if repetition_count < 0:
        return DEFAULT_INTERVALS_DAYS[0]
    idx = min(repetition_count, len(DEFAULT_INTERVALS_DAYS) - 1)
    return DEFAULT_INTERVALS_DAYS[idx]


async def create_review_item(db: Prisma, user_id: str, topic_id: str) -> Any | None:
    """
    Create a ReviewItem when a topic is first completed.
    nextReviewAt = now + 1 day. Returns the created ReviewItem or None if already exists.
    """
    existing = await db.reviewitem.find_first(where={"userId": user_id, "topicId": topic_id})
    if existing:
        return None
    now = datetime.now(UTC)
    next_review = now + timedelta(days=1)
    return await db.reviewitem.create(
        data={
            "userId": user_id,
            "topicId": topic_id,
            "nextReviewAt": next_review,
            "intervalDays": 1,
            "repetitionCount": 0,
        }
    )


async def advance_review(
    db: Prisma,
    review_item_id: str,
    user_id: str,
    completed_on_time: bool = True,
    actual_at: datetime | None = None,
) -> Any:
    """
    Mark a review as done: log behaviour, update nextReviewAt and repetitionCount.
    If completed_on_time is False, we still advance but log COMPLETED_LATE.
    """
    review = await db.reviewitem.find_first(
        where={"id": review_item_id, "userId": user_id},
        include={"topic": True},
    )
    if not review:
        raise ValueError("ReviewItem not found")
    now = actual_at or datetime.now(UTC)
    behaviour = "COMPLETED_ON_TIME" if completed_on_time else "COMPLETED_LATE"
    await log_behaviour(
        db,
        user_id=user_id,
        behaviour_type=behaviour,
        entity_type="review",
        entity_id=review_item_id,
        scheduled_at=review.nextReviewAt,
        actual_at=now,
        metadata={"topicId": review.topicId, "topicTitle": review.topic.title},
    )
    new_count = review.repetitionCount + 1
    interval_days = get_next_interval_days(new_count)
    next_review_at = now + timedelta(days=interval_days)
    # Clear scheduleBlockId so daily task can create a new block for next time
    updated = await db.reviewitem.update(
        where={"id": review_item_id},
        data={
            "lastReviewedAt": now,
            "repetitionCount": new_count,
            "intervalDays": interval_days,
            "nextReviewAt": next_review_at,
            "scheduleBlockId": None,
        },
    )
    # Unlink the old schedule block from this review (block stays in DB for history)
    if review.scheduleBlockId:
        await db.scheduleblock.update(
            where={"id": review.scheduleBlockId},
            data={"reviewItemId": None},
        )
    return updated


async def log_behaviour(
    db: Prisma,
    user_id: str,
    behaviour_type: str,
    entity_type: str,
    entity_id: str | None = None,
    scheduled_at: datetime | None = None,
    actual_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Log a schedule behaviour event for AI learning."""
    return await db.schedulebehaviourlog.create(
        data={
            "userId": user_id,
            "behaviourType": behaviour_type,
            "entityType": entity_type,
            "entityId": entity_id,
            "scheduledAt": scheduled_at,
            "actualAt": actual_at,
            "metadata": metadata,
        }
    )


async def ensure_review_item_for_completed_topic(
    db: Prisma, user_id: str, topic_id: str
) -> Any | None:
    """
    If the topic is completed and no ReviewItem exists for it, create one.
    Call this after marking a topic complete. Returns created ReviewItem or None.
    """
    topic = await db.topic.find_first(
        where={"id": topic_id, "module": {"course": {"userId": user_id}}},
    )
    if not topic or not topic.completed:
        return None
    return await create_review_item(db, user_id, topic_id)
