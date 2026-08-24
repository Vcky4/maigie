"""
Spaced repetition service: SM-2 adaptive review scheduling and behaviour logging.

- Creates ReviewItems when a topic is completed (one review schedule per topic).
- Implements the SM-2 algorithm with adaptive ease factor and quality-based intervals.
- Handles lapses (quality < 3) by resetting intervals for re-learning.
- Applies an overdue penalty when reviews are completed significantly late.
- Logs schedule behaviour (on-time, late, skipped, rescheduled, lapsed) for AI learning.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from ..db_models import ReviewItem
from ..repository import progress_repo

logger = logging.getLogger(__name__)

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


async def create_review_item_for_topic(user_id: str, topic_id: str) -> Any | None:
    """
    Create a ReviewItem when a topic is first completed.
    nextReviewAt = now + 1 day. Returns the created ReviewItem or None if already exists.
    """
    existing = await progress_repo.find_review_by_topic(user_id, topic_id)
    if existing:
        return None
    now = datetime.now(UTC)
    next_review = now + timedelta(days=GRADUATING_INTERVAL_DAYS)
    return await progress_repo.create_review_item(
        {
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


async def create_schedule_block_for_review(review) -> Any | None:
    """
    Create a ScheduleBlock for a ReviewItem so it appears on the calendar.
    Call this when a new review is created (topic completed) or when the daily task runs.
    Returns the created ScheduleBlock or None on error.
    """
    topic = review.topic
    if not topic:
        return None

    topic_title = topic.title if topic else "Topic"
    start_at = review.next_review_at
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    end_at = start_at + timedelta(minutes=REVIEW_BLOCK_DURATION_MINUTES)

    data: dict[str, Any] = {
        "userId": review.user_id,
        "title": f"Review: {topic_title}",
        "description": "Spaced repetition review (quiz and refresher)",
        "startAt": start_at,
        "endAt": end_at,
        "reviewItemId": review.id,
        "topicId": review.topic_id,
    }

    return await progress_repo.create_block(data)


async def advance_review_sqlalchemy(
    user_id: str,
    review_item_id: str,
    quality: int,
    actual_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Mark a review as done using SM-2 adaptive scheduling (SQLAlchemy version).

    Args:
        quality: 0–5 rating of recall quality.
        actual_at: When the review was actually completed (default: now).

    Updates nextReviewAt, intervalDays, easeFactor, repetitionCount, lapseCount.
    Returns a dict with the updated review info.
    """
    review = await progress_repo.find_review(review_item_id, user_id)
    if not review:
        raise ValueError("ReviewItem not found")

    now = actual_at or datetime.now(UTC)
    quality = max(0, min(5, quality))

    # ── Determine behaviour type for logging ────────────────────────────
    is_lapse = quality < 3
    if is_lapse:
        behaviour = "LAPSED"
    elif review.next_review_at and now > review.next_review_at + timedelta(days=1):
        behaviour = "COMPLETED_LATE"
    else:
        behaviour = "COMPLETED_ON_TIME"

    await progress_repo.create_behaviour_log(
        {
            "userId": user_id,
            "behaviourType": behaviour,
            "entityType": "review",
            "entityId": review_item_id,
            "scheduledAt": review.next_review_at,
            "actualAt": now,
            "metadata": {
                "topicId": review.topic_id,
                "topicTitle": review.topic.title if review.topic else "",
                "quality": quality,
                "previousEaseFactor": review.ease_factor,
                "previousInterval": review.interval_days,
                "previousRepetitionCount": review.repetition_count,
            },
        }
    )

    # ── SM-2 computation ────────────────────────────────────────────────
    new_interval, new_ef, new_rep_count = compute_sm2(
        quality=quality,
        repetition_count=review.repetition_count,
        ease_factor=review.ease_factor,
        interval_days=review.interval_days,
    )

    # ── Overdue penalty (only for successful reviews) ───────────────────
    if not is_lapse and review.next_review_at:
        new_interval, new_ef = apply_overdue_penalty(
            interval_days=new_interval,
            ease_factor=new_ef,
            scheduled_at=review.next_review_at,
            completed_at=now,
        )

    # ── Update lapse count ──────────────────────────────────────────────
    new_lapse_count = review.lapse_count + (1 if is_lapse else 0)

    next_review_at = now + timedelta(days=new_interval)
    updated = await progress_repo.update_review(
        review_item_id,
        {
            "lastReviewedAt": now,
            "repetitionCount": new_rep_count,
            "intervalDays": new_interval,
            "easeFactor": new_ef,
            "lastQuality": quality,
            "lapseCount": new_lapse_count,
            "nextReviewAt": next_review_at,
        },
    )

    # Unlink the old schedule block (set reviewItemId to None)
    if review.schedule_block:
        await progress_repo.update_block(review.schedule_block.id, {"reviewItemId": None})

    return {
        "id": updated.id,
        "nextReviewAt": updated.next_review_at.isoformat(),
        "intervalDays": updated.interval_days,
        "repetitionCount": updated.repetition_count,
        "easeFactor": updated.ease_factor,
        "lastQuality": updated.last_quality,
        "lapseCount": updated.lapse_count,
        "behaviour": behaviour,
    }


async def get_review_stats(user_id: str) -> dict[str, Any]:
    """
    Compute review statistics for the user dashboard.
    Returns counts, averages, and a strength breakdown.
    """
    now = datetime.now(UTC)
    items = await progress_repo.list_all_reviews(user_id)

    total = len(items)
    due_today = sum(1 for r in items if r.next_review_at <= now)
    due_this_week = sum(1 for r in items if r.next_review_at <= now + timedelta(days=7))

    # Strength distribution
    strong = moderate = weak = 0
    total_ease = 0.0
    total_reviewed = 0
    for r in items:
        label = get_strength_label(r.ease_factor, r.interval_days, r.lapse_count)
        if label == "strong":
            strong += 1
        elif label == "moderate":
            moderate += 1
        else:
            weak += 1
        total_ease += r.ease_factor
        if r.repetition_count > 0:
            total_reviewed += 1

    avg_ease = round(total_ease / total, 2) if total > 0 else INITIAL_EASE_FACTOR

    # Estimated retention (rough heuristic based on ease factor distribution)
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
        count = sum(1 for r in items if day_start <= r.next_review_at < day_end)
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


async def ensure_review_item_for_completed_topic(user_id: str, topic_id: str) -> Any | None:
    """
    If no ReviewItem exists for the topic, create one and create a schedule
    block so the review appears on the calendar.
    Returns created ReviewItem or None.

    **Still has no callers, and that is deliberate.** "Call this after marking a topic complete" is what
    this docstring used to say, and nothing ever did — the intended caller was an event listener, and no
    listener was ever registered. `progress.listeners.schedule_first_review` is that listener now, and it
    calls `create_review_item_for_topic` instead, because the schedule block this function also writes is
    superseded: `agenda_service` composes due reviews on read, so a block is a second record of one
    commitment that has to be found and rewritten every time SM-2 moves the due date. Same reasoning as
    `process_due_reviews`.

    Kept rather than deleted because a block is still the only way a review reaches a connected Google
    Calendar, so a caller may want the pair. If you wire this, know that the agenda will show the review
    through the block and `_read_topic_reviews` will skip the item, which is the intended handover — not
    a duplicate.
    """
    review = await create_review_item_for_topic(user_id, topic_id)
    if review:
        await create_schedule_block_for_review(review)
        await progress_repo.create_behaviour_log(
            {
                "userId": user_id,
                "behaviourType": "AI_CREATED",
                "entityType": "schedule_block",
                "entityId": None,
                "scheduledAt": review.next_review_at,
                "actualAt": None,
                "metadata": {"topicId": topic_id, "source": "topic_completed"},
            }
        )
    return review


# ---------------------------------------------------------------------------
# Legacy Prisma-compatible wrappers (for callers outside this domain that
# still pass a Prisma `db` argument). These ignore the db arg and use the repo.
# ---------------------------------------------------------------------------


async def create_review_item(db, user_id: str, topic_id: str) -> Any | None:
    """Legacy wrapper — ignores db arg, delegates to create_review_item_for_topic."""
    return await create_review_item_for_topic(user_id, topic_id)


async def advance_review(db, review_item_id: str, user_id: str, quality: int = 4, **kwargs) -> Any:
    """Legacy wrapper — ignores db arg, delegates to advance_review_sqlalchemy."""
    return await advance_review_sqlalchemy(
        user_id, review_item_id, quality, actual_at=kwargs.get("actual_at")
    )


async def log_behaviour(
    db,
    user_id: str,
    behaviour_type: str,
    entity_type: str,
    entity_id: str | None = None,
    scheduled_at=None,
    actual_at=None,
    metadata=None,
) -> Any:
    """Legacy wrapper — ignores db arg, uses repository."""
    return await progress_repo.create_behaviour_log(
        {
            "userId": user_id,
            "behaviourType": behaviour_type,
            "entityType": entity_type,
            "entityId": entity_id,
            "scheduledAt": scheduled_at,
            "actualAt": actual_at,
            "metadata": metadata,
        }
    )


async def process_due_reviews() -> dict[str, int]:
    """Give every soon-due review item a calendar block.

    This is the background sweep that ``progress.process_spaced_repetition`` runs. It was
    missing: the worker imported it from ``src.tasks.spaced_repetition``, a module that no
    longer exists, and because the import sits inside the task body nothing detected it.
    The interactive side of spaced repetition was migrated and works, so reviews came due
    and were answerable, but nothing put them on the calendar on a schedule.

    Looks ahead to the end of tomorrow rather than only at what is due now, so a learner
    opening their schedule sees tomorrow's reviews already placed.

    ``ScheduleBlock.reviewItemId`` is unique, so an item can hold only one block. An
    existing block that has not yet ended is left alone; one that has already ended is
    unlinked first, which frees the unique slot for the new block.

    Returns counts so an empty run is distinguishable from a broken one.

    **Superseded, and deliberately not on a beat schedule.** `agenda_service` now surfaces due reviews by
    reading them, so a learner sees them on their day without any block being written — and it skips items
    that already hold a block, so running this sweep does not double them up. Materialising was the older
    design and it carries the cost this docstring already describes: a second record of one commitment,
    which has to be unlinked and rewritten whenever a due date moves. Scheduling this would put that
    maintenance back for no gain a reader would notice.

    It stays because the Celery task is registered and a caller may want a one-off sweep — for instance to
    push reviews into a connected Google Calendar, which only blocks reach.
    """
    now = datetime.now(UTC)
    end_of_tomorrow = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ReviewItem)
            .options(
                selectinload(ReviewItem.topic),
                selectinload(ReviewItem.schedule_block),
            )
            .where(ReviewItem.next_review_at <= end_of_tomorrow)
            .order_by(ReviewItem.next_review_at.asc())
        )
        items = list(result.scalars().all())

    created = 0
    skipped = 0
    failed = 0

    for review in items:
        try:
            existing = getattr(review, "schedule_block", None)
            if existing is not None:
                end_at = existing.end_at
                if end_at is not None and end_at.tzinfo is None:
                    end_at = end_at.replace(tzinfo=UTC)
                if end_at is not None and end_at >= now:
                    # Still upcoming or in progress; leave it.
                    skipped += 1
                    continue
                # Stale block: release the unique reviewItemId before creating a new one.
                await progress_repo.update_block(existing.id, {"reviewItemId": None})

            if await create_schedule_block_for_review(review) is None:
                # A review item whose topic has gone cannot be scheduled.
                skipped += 1
                continue
            created += 1
        except Exception:
            # One bad row must not stop the sweep.
            failed += 1
            logger.exception("Failed to schedule review item %s", review.id)

    summary = {"considered": len(items), "created": created, "skipped": skipped, "failed": failed}
    if items:
        logger.info("Spaced repetition sweep: %s", summary)
    return summary
