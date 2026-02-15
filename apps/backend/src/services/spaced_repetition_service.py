"""
Spaced repetition service: SM-2 adaptive review scheduling and behaviour logging.

- Creates ReviewItems when a topic is completed (one review schedule per topic).
- Implements the SM-2 algorithm with adaptive ease factor and quality-based intervals.
- Handles lapses (quality < 3) by resetting intervals for re-learning.
- Applies an overdue penalty when reviews are completed significantly late.
- Logs schedule behaviour (on-time, late, skipped, rescheduled, lapsed) for AI learning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from prisma import Prisma


# ── SM-2 Algorithm Constants ────────────────────────────────────────────────
INITIAL_EASE_FACTOR = 2.5  # Starting ease for new items
MIN_EASE_FACTOR = 1.3  # Floor – prevents intervals from shrinking too aggressively
MAX_INTERVAL_DAYS = 365  # Safety cap – no single interval exceeds ~1 year
LAPSE_INTERVAL_DAYS = 1  # Interval after a lapse (forgot the material)
GRADUATING_INTERVAL_DAYS = 1  # First successful review interval
EASY_BONUS = 1.3  # Multiplier boost for "easy" (quality 5) reviews

REVIEW_BLOCK_DURATION_MINUTES = 30

# Quality scale (0–5):
#  0 – Complete blackout, no recall at all
#  1 – Incorrect; upon seeing the answer, remembered "oh right"
#  2 – Incorrect; the correct answer seemed easy to recall once shown
#  3 – Correct answer recalled with serious difficulty
#  4 – Correct answer after some hesitation
#  5 – Perfect recall, instant answer


def compute_sm2(
    quality: int,
    repetition_count: int,
    ease_factor: float,
    interval_days: int,
) -> tuple[int, float, int]:
    """
    Core SM-2 computation.

    Args:
        quality:          User quality rating 0–5
        repetition_count: Number of consecutive successful reviews
        ease_factor:      Current ease factor (≥ 1.3)
        interval_days:    Current interval in days

    Returns:
        (new_interval_days, new_ease_factor, new_repetition_count)
    """
    quality = max(0, min(5, quality))  # clamp

    if quality < 3:
        # ── Lapse: user didn't recall well enough ───────────────────────
        new_repetition_count = 0
        new_interval = LAPSE_INTERVAL_DAYS
        # Reduce ease factor on lapse but respect floor
        new_ef = max(MIN_EASE_FACTOR, ease_factor - 0.2)
    else:
        # ── Successful recall ───────────────────────────────────────────
        new_repetition_count = repetition_count + 1

        if new_repetition_count == 1:
            new_interval = GRADUATING_INTERVAL_DAYS
        elif new_repetition_count == 2:
            new_interval = 6
        else:
            new_interval = round(interval_days * ease_factor)

        # Apply easy bonus for quality 5
        if quality == 5:
            new_interval = round(new_interval * EASY_BONUS)

        # SM-2 ease factor adjustment:
        # EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
        new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        new_ef = max(MIN_EASE_FACTOR, new_ef)

    # Enforce caps
    new_interval = max(1, min(new_interval, MAX_INTERVAL_DAYS))

    return new_interval, new_ef, new_repetition_count


def apply_overdue_penalty(
    interval_days: int,
    ease_factor: float,
    scheduled_at: datetime,
    completed_at: datetime,
) -> tuple[int, float]:
    """
    If the review was completed significantly after its due date, reduce the
    next interval proportionally. "Significantly" = more than 25% past the
    scheduled interval.

    Returns:
        (adjusted_interval, adjusted_ease_factor)
    """
    overdue_seconds = (completed_at - scheduled_at).total_seconds()
    if overdue_seconds <= 0:
        return interval_days, ease_factor  # on time or early

    scheduled_interval_seconds = interval_days * 86400
    overdue_ratio = overdue_seconds / max(scheduled_interval_seconds, 86400)

    if overdue_ratio <= 0.25:
        return interval_days, ease_factor  # within grace period

    # Scale down: at 2× overdue the interval is halved
    penalty = max(0.5, 1.0 - (overdue_ratio - 0.25) * 0.5)
    adjusted_interval = max(1, round(interval_days * penalty))
    adjusted_ef = max(MIN_EASE_FACTOR, ease_factor - 0.05 * min(overdue_ratio, 2.0))

    return adjusted_interval, adjusted_ef


def get_strength_label(ease_factor: float, interval_days: int, lapse_count: int) -> str:
    """
    Human-friendly strength label for a review item.
    Used on the frontend to show per-topic retention strength.
    """
    if lapse_count >= 3 and interval_days <= 3:
        return "weak"
    if ease_factor < 1.8 or interval_days <= 3:
        return "weak"
    if ease_factor < 2.2 or interval_days <= 14:
        return "moderate"
    return "strong"


async def create_review_item(db: Prisma, user_id: str, topic_id: str) -> Any | None:
    """
    Create a ReviewItem when a topic is first completed.
    nextReviewAt = now + 1 day. Returns the created ReviewItem or None if already exists.
    """
    existing = await db.reviewitem.find_first(where={"userId": user_id, "topicId": topic_id})
    if existing:
        return None
    now = datetime.now(UTC)
    next_review = now + timedelta(days=GRADUATING_INTERVAL_DAYS)
    return await db.reviewitem.create(
        data={
            "userId": user_id,
            "topicId": topic_id,
            "nextReviewAt": next_review,
            "intervalDays": GRADUATING_INTERVAL_DAYS,
            "repetitionCount": 0,
            "easeFactor": INITIAL_EASE_FACTOR,
            "lastQuality": -1,
            "lapseCount": 0,
        }
    )


async def create_schedule_block_for_review(db: Prisma, review: Any) -> Any | None:
    """
    Create a ScheduleBlock for a ReviewItem so it appears on the calendar.
    Call this when a new review is created (topic completed) or when the daily task runs.
    Returns the created ScheduleBlock or None on error.
    """
    review_with_topic = await db.reviewitem.find_unique(
        where={"id": review.id},
        include={"topic": {"include": {"module": {"include": {"course": True}}}}},
    )
    if not review_with_topic or not review_with_topic.topic:
        return None
    topic = review_with_topic.topic
    course = topic.module.course if topic.module else None
    topic_title = topic.title if topic else "Topic"
    start_at = review.nextReviewAt
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    end_at = start_at + timedelta(minutes=REVIEW_BLOCK_DURATION_MINUTES)
    return await db.scheduleblock.create(
        data={
            "userId": review.userId,
            "title": f"Review: {topic_title}",
            "description": "Spaced repetition review (quiz and refresher)",
            "startAt": start_at,
            "endAt": end_at,
            "topicId": review.topicId,
            "courseId": course.id if course else None,
            "reviewItemId": review.id,
        },
    )


async def advance_review(
    db: Prisma,
    review_item_id: str,
    user_id: str,
    quality: int = 4,
    completed_on_time: bool = True,
    actual_at: datetime | None = None,
) -> Any:
    """
    Mark a review as done using SM-2 adaptive scheduling.

    Args:
        quality: 0–5 rating of recall quality. AI provides this based on quiz performance.
                 0-2 = lapse (failed), 3 = hard, 4 = good, 5 = easy.
        completed_on_time: Legacy flag, now derived from quality + timing automatically.
        actual_at: When the review was actually completed (default: now).

    Updates nextReviewAt, intervalDays, easeFactor, repetitionCount, lapseCount.
    """
    review = await db.reviewitem.find_first(
        where={"id": review_item_id, "userId": user_id},
        include={"topic": True, "scheduleBlock": True},
    )
    if not review:
        raise ValueError("ReviewItem not found")

    now = actual_at or datetime.now(UTC)
    quality = max(0, min(5, quality))

    # ── Determine behaviour type for logging ────────────────────────────
    is_lapse = quality < 3
    if is_lapse:
        behaviour = "LAPSED"
    elif review.nextReviewAt and now > review.nextReviewAt + timedelta(days=1):
        behaviour = "COMPLETED_LATE"
    else:
        behaviour = "COMPLETED_ON_TIME"

    await log_behaviour(
        db,
        user_id=user_id,
        behaviour_type=behaviour,
        entity_type="review",
        entity_id=review_item_id,
        scheduled_at=review.nextReviewAt,
        actual_at=now,
        metadata={
            "topicId": review.topicId,
            "topicTitle": review.topic.title if review.topic else "",
            "quality": quality,
            "previousEaseFactor": review.easeFactor,
            "previousInterval": review.intervalDays,
            "previousRepetitionCount": review.repetitionCount,
        },
    )

    # ── SM-2 computation ────────────────────────────────────────────────
    new_interval, new_ef, new_rep_count = compute_sm2(
        quality=quality,
        repetition_count=review.repetitionCount,
        ease_factor=review.easeFactor,
        interval_days=review.intervalDays,
    )

    # ── Overdue penalty (only for successful reviews) ───────────────────
    if not is_lapse and review.nextReviewAt:
        new_interval, new_ef = apply_overdue_penalty(
            interval_days=new_interval,
            ease_factor=new_ef,
            scheduled_at=review.nextReviewAt,
            completed_at=now,
        )

    # ── Update lapse count ──────────────────────────────────────────────
    new_lapse_count = review.lapseCount + (1 if is_lapse else 0)

    next_review_at = now + timedelta(days=new_interval)
    updated = await db.reviewitem.update(
        where={"id": review_item_id},
        data={
            "lastReviewedAt": now,
            "repetitionCount": new_rep_count,
            "intervalDays": new_interval,
            "easeFactor": new_ef,
            "lastQuality": quality,
            "lapseCount": new_lapse_count,
            "nextReviewAt": next_review_at,
        },
    )

    # Unlink the old schedule block from this review (block holds FK; stays in DB for history)
    if review.scheduleBlock:
        await db.scheduleblock.update(
            where={"id": review.scheduleBlock.id},
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


async def get_review_stats(db: Prisma, user_id: str) -> dict[str, Any]:
    """
    Compute review statistics for the user dashboard.
    Returns counts, averages, and a strength breakdown.
    """
    now = datetime.now(UTC)
    items = await db.reviewitem.find_many(
        where={"userId": user_id},
        include={"topic": True},
    )

    total = len(items)
    due_today = sum(1 for r in items if r.nextReviewAt <= now)
    due_this_week = sum(1 for r in items if r.nextReviewAt <= now + timedelta(days=7))

    # Strength distribution
    strong = moderate = weak = 0
    total_ease = 0.0
    total_reviewed = 0
    for r in items:
        label = get_strength_label(r.easeFactor, r.intervalDays, r.lapseCount)
        if label == "strong":
            strong += 1
        elif label == "moderate":
            moderate += 1
        else:
            weak += 1
        total_ease += r.easeFactor
        if r.repetitionCount > 0:
            total_reviewed += 1

    avg_ease = round(total_ease / total, 2) if total > 0 else INITIAL_EASE_FACTOR

    # Estimated retention (rough heuristic based on ease factor distribution)
    # Higher ease = better retention. EF 2.5 ≈ 90%, EF 1.3 ≈ 70%
    if total > 0:
        retention_estimate = round(min(95, max(50, 60 + (avg_ease - 1.3) * 25)), 1)
    else:
        retention_estimate = 0

    # Upcoming load forecast: reviews due in next 7 days by day
    forecast = []
    for day_offset in range(7):
        day_start = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        count = sum(1 for r in items if day_start <= r.nextReviewAt < day_end)
        forecast.append({"date": day_start.isoformat(), "count": count})

    return {
        "total": total,
        "dueToday": due_today,
        "dueThisWeek": due_this_week,
        "totalReviewed": total_reviewed,
        "averageEaseFactor": avg_ease,
        "estimatedRetention": retention_estimate,
        "strength": {
            "strong": strong,
            "moderate": moderate,
            "weak": weak,
        },
        "forecast": forecast,
    }


async def ensure_review_item_for_completed_topic(
    db: Prisma, user_id: str, topic_id: str
) -> Any | None:
    """
    If the topic is completed and no ReviewItem exists for it, create one
    and create a schedule block so the review appears on the calendar.
    Call this after marking a topic complete. Returns created ReviewItem or None.
    """
    topic = await db.topic.find_first(
        where={"id": topic_id, "module": {"course": {"userId": user_id}}},
    )
    if not topic or not topic.completed:
        return None
    review = await create_review_item(db, user_id, topic_id)
    if review:
        await create_schedule_block_for_review(db, review)
        await log_behaviour(
            db,
            user_id=user_id,
            behaviour_type="AI_CREATED",
            entity_type="schedule_block",
            entity_id=None,
            scheduled_at=review.nextReviewAt,
            actual_at=None,
            metadata={"topicId": topic_id, "source": "topic_completed"},
        )
    return review
