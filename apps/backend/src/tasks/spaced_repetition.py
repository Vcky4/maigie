"""
Spaced repetition and schedule review tasks (Celery).

- Daily: create schedule blocks for due reviews.
- Daily: AI reviews each user's schedule and behaviour and suggests/creates blocks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task
from src.tasks.schedules import DAILY_AT_8AM, register_periodic_task

logger = logging.getLogger(__name__)

TASK_CREATE_REVIEW_BLOCKS = "spaced_repetition.create_review_blocks"
TASK_AI_REVIEW_SCHEDULES = "spaced_repetition.ai_review_schedules"


async def _ensure_db_connected() -> None:
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _create_review_blocks_for_due_items() -> dict[str, Any]:
    """Find ReviewItems due today/tomorrow without a future block; create 30-min blocks."""
    from src.core.database import db
    from src.services.spaced_repetition_service import (
        REVIEW_BLOCK_DURATION_MINUTES,
        log_behaviour,
    )

    await _ensure_db_connected()
    now = datetime.now(UTC)
    end_tomorrow = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    created = 0
    skipped = 0
    errors = []

    items = await db.reviewitem.find_many(
        where={
            "nextReviewAt": {"lte": end_tomorrow},
            "OR": [
                {"scheduleBlockId": None},
                {"scheduleBlock": {"endAt": {"lt": now}}},
            ],
        },
        include={
            "topic": {"include": {"module": {"include": {"course": True}}}},
            "scheduleBlock": True,
        },
    )

    for review in items:
        try:
            # If there's an old block, unlink it
            if review.scheduleBlockId and review.scheduleBlock:
                if review.scheduleBlock.endAt >= now:
                    skipped += 1
                    continue
                await db.scheduleblock.update(
                    where={"id": review.scheduleBlockId},
                    data={"reviewItemId": None},
                )
            topic = review.topic
            course = topic.module.course if topic and topic.module else None
            topic_title = topic.title if topic else "Topic"
            start_at = review.nextReviewAt
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=UTC)
            end_at = start_at + timedelta(minutes=REVIEW_BLOCK_DURATION_MINUTES)
            block = await db.scheduleblock.create(
                data={
                    "userId": review.userId,
                    "title": f"Review: {topic_title}",
                    "description": "Spaced repetition review (quiz recommended)",
                    "startAt": start_at,
                    "endAt": end_at,
                    "topicId": review.topicId,
                    "courseId": course.id if course else None,
                    "reviewItemId": review.id,
                },
            )
            await db.reviewitem.update(
                where={"id": review.id},
                data={"scheduleBlockId": block.id},
            )
            await log_behaviour(
                db,
                user_id=review.userId,
                behaviour_type="AI_CREATED",
                entity_type="schedule_block",
                entity_id=block.id,
                scheduled_at=start_at,
                actual_at=None,
                metadata={"topicId": review.topicId, "source": "spaced_repetition"},
            )
            created += 1
        except Exception as e:
            logger.exception("Failed to create review block for %s: %s", review.id, e)
            errors.append({"reviewId": review.id, "error": str(e)})

    return {"created": created, "skipped": skipped, "errors": errors}


@register_task(
    name=TASK_CREATE_REVIEW_BLOCKS,
    description="Create schedule blocks for due spaced-repetition reviews",
    category="schedule",
    tags=["schedule", "spaced_repetition"],
)
@task(name=TASK_CREATE_REVIEW_BLOCKS, bind=True, max_retries=2)
def create_review_blocks_task(self: Any) -> dict[str, Any]:
    """Run daily: create ScheduleBlocks for ReviewItems due today/tomorrow."""
    return run_async_in_celery(_create_review_blocks_for_due_items())


async def _ai_review_schedules_for_user(user_id: str, db: Any) -> dict[str, Any]:
    """Gather user's recent behaviour and schedule; call LLM to suggest new blocks; create and log."""
    from src.services.spaced_repetition_service import log_behaviour
    from src.services.llm_service import get_schedule_review_suggestions

    suggestions = await get_schedule_review_suggestions(user_id, db)
    created = 0
    from src.services.action_service import action_service

    for block_data in suggestions:
        try:
            result = await action_service.create_schedule(block_data, user_id)
            if result.get("status") == "success" and result.get("schedule"):
                schedule_id = result["schedule"].get("id")
                if schedule_id:
                    await log_behaviour(
                        db,
                        user_id=user_id,
                        behaviour_type="AI_CREATED",
                        entity_type="schedule_block",
                        entity_id=schedule_id,
                        scheduled_at=None,
                        actual_at=None,
                        metadata={"source": "ai_daily_review"},
                    )
                    created += 1
        except Exception as e:
            logger.warning("AI suggested block failed for user %s: %s", user_id, e)
    return {"created": created, "suggestions_count": len(suggestions)}


async def _ai_review_schedules_daily() -> dict[str, Any]:
    """For each user with recent activity, run AI schedule review and create suggested blocks."""
    from src.core.database import db

    await _ensure_db_connected()
    # Users with ReviewItem or ScheduleBehaviourLog in last 14 days
    since = datetime.now(UTC) - timedelta(days=14)
    behaviour_user_ids = await db.schedulebehaviourlog.find_many(
        where={"createdAt": {"gte": since}},
        distinct=["userId"],
    )
    review_user_ids = await db.reviewitem.find_many(
        where={"updatedAt": {"gte": since}},
        distinct=["userId"],
    )
    user_ids = list({r.userId for r in behaviour_user_ids} | {r.userId for r in review_user_ids})
    results = {}
    for uid in user_ids[:50]:  # Cap at 50 users per run
        try:
            results[uid] = await _ai_review_schedules_for_user(uid, db)
        except Exception as e:
            logger.warning("AI review failed for user %s: %s", uid, e)
            results[uid] = {"created": 0, "suggestions_count": 0, "error": str(e)}
    return {"users_processed": len(results), "results": results}


@register_task(
    name=TASK_AI_REVIEW_SCHEDULES,
    description="Daily AI review of user schedules and behaviour; suggest and create blocks",
    category="schedule",
    tags=["schedule", "ai", "spaced_repetition"],
)
@task(name=TASK_AI_REVIEW_SCHEDULES, bind=True, max_retries=2)
def ai_review_schedules_task(self: Any) -> dict[str, Any]:
    """Run daily after review blocks: AI suggests schedule changes from behaviour."""
    return run_async_in_celery(_ai_review_schedules_daily())


def register_spaced_repetition_beat_tasks() -> None:
    """Register periodic Celery Beat tasks for spaced repetition."""
    register_periodic_task(
        name="spaced_repetition.create_review_blocks.daily",
        schedule=DAILY_AT_8AM,
        task=TASK_CREATE_REVIEW_BLOCKS,
    )
    # Run AI review 1 hour after review blocks (9 AM)
    from celery.schedules import crontab

    register_periodic_task(
        name="spaced_repetition.ai_review_schedules.daily",
        schedule=crontab(hour=9, minute=0),
        task=TASK_AI_REVIEW_SCHEDULES,
    )


# Register with Celery Beat when module is loaded
register_spaced_repetition_beat_tasks()
