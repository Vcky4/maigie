"""
Course lifecycle — create, update, delete, list, progress calculation.

Delegates AI generation to the Intelligence domain (via background tasks).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.domains.identity.db_models import User
from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError, ValidationError

from ..events import emit_course_created, emit_topic_completed, emit_topic_uncompleted
from ..repository import knowledge_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ownership checks
# ---------------------------------------------------------------------------


async def check_course_ownership(course_id: str, user_id: str):
    """Verify course belongs to user. Returns course or raises."""
    course = await knowledge_repo.find_course(course_id, user_id)
    if not course:
        raise NotFoundError("Course", course_id)
    return course


async def check_module_ownership(module_id: str, user_id: str):
    """Verify module belongs to a course owned by user. Returns (module, course)."""
    module = await knowledge_repo.find_module(module_id)
    if not module or not module.course:
        raise NotFoundError("Module", module_id)
    if module.course.userId != user_id:
        raise ForbiddenError("You do not own this module")
    return module, module.course


async def check_topic_ownership(topic_id: str, user_id: str):
    """Verify topic belongs to a course owned by user. Returns (topic, module, course)."""
    topic = await knowledge_repo.find_topic(topic_id)
    if not topic or not topic.module or not topic.module.course:
        raise NotFoundError("Topic", topic_id)
    if topic.module.course.userId != user_id:
        raise ForbiddenError("You do not own this topic")
    return topic, topic.module, topic.module.course


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def calculate_course_progress(course_id: str) -> tuple[float, int, int]:
    """Calculate (progress%, total_topics, completed_topics) for a course."""
    modules = await knowledge_repo.list_modules(course_id)
    total = 0
    completed = 0
    for module in modules:
        topics = getattr(module, "topics", []) or []
        total += len(topics)
        completed += sum(1 for t in topics if t.completed)
    progress = (completed / total * 100) if total > 0 else 0.0
    return round(progress, 1), total, completed


def calculate_module_progress(module) -> dict[str, Any]:
    """Calculate progress for a single module (with topics loaded)."""
    topics = getattr(module, "topics", []) or []
    total = len(topics)
    completed = sum(1 for t in topics if t.completed)
    progress = (completed / total * 100) if total > 0 else 0.0
    return {
        "id": module.id,
        "courseId": module.courseId,
        "title": module.title,
        "order": module.order,
        "description": module.description,
        "completed": total > 0 and completed == total,
        "progress": round(progress, 1),
        "topicCount": total,
        "completedTopicCount": completed,
        "topics": topics,
        "createdAt": module.createdAt,
        "updatedAt": module.updatedAt,
    }


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


async def create_course(*, user: User, data: dict[str, Any]) -> Any:
    """Create a course manually."""
    # Free tier limit: 2 courses per month
    if str(user.tier) == "FREE":
        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
        count = await knowledge_repo.count_courses(
            {"userId": user.id, "createdAt": {"gte": thirty_days_ago}}
        )
        if count >= 2:
            raise ForbiddenError(
                "You can only create 2 courses per month on the free plan. "
                "Start a free trial for unlimited courses."
            )

    create_data = {
        "userId": user.id,
        "title": data["title"],
        "description": data.get("description"),
        "difficulty": data.get("difficulty"),
        "targetDate": data.get("targetDate"),
        "isAIGenerated": data.get("isAIGenerated", False),
    }
    if data.get("spaceId"):
        create_data["spaceId"] = data["spaceId"]

    course = await knowledge_repo.create_course(create_data)
    await emit_course_created(user.id, course.id, is_ai_generated=create_data["isAIGenerated"])
    return course


async def update_course(*, course_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update course metadata."""
    await check_course_ownership(course_id, user_id)
    update_data = {k: v for k, v in data.items() if v is not None}
    if not update_data:
        return await knowledge_repo.find_course_with_modules(course_id, user_id)
    await knowledge_repo.update_course(course_id, update_data)
    return await knowledge_repo.find_course_with_modules(course_id, user_id)


async def archive_course(*, course_id: str, user_id: str) -> Any:
    """Archive a course (soft delete)."""
    await check_course_ownership(course_id, user_id)
    await knowledge_repo.update_course(course_id, {"archived": True})
    return await knowledge_repo.find_course_with_modules(course_id, user_id)


async def delete_course(*, course_id: str, user_id: str) -> None:
    """Delete a course with cascade."""
    await check_course_ownership(course_id, user_id)
    await knowledge_repo.delete_course(course_id)


# ---------------------------------------------------------------------------
# Topic completion
# ---------------------------------------------------------------------------


async def toggle_topic_completion(
    *, topic_id: str, module_id: str, course_id: str, user_id: str, completed: bool
) -> Any:
    """Mark/unmark a topic as completed. Emits domain events."""
    topic, module, course = await check_topic_ownership(topic_id, user_id)

    if topic.moduleId != module_id or module.courseId != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    updated = await knowledge_repo.update_topic(topic_id, {"completed": completed})

    if completed:
        await emit_topic_completed(user_id, topic_id, course_id)
        # Spaced repetition: Progress domain listens to topic.completed event
        # and creates ReviewSchedule automatically via the event bus
    else:
        await emit_topic_uncompleted(user_id, topic_id, course_id)

    return updated
