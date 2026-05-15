"""
Course route helpers: progress math, ownership checks, module enrichment.

Extracted from courses.py to keep HTTP handlers easier to navigate.
"""

from __future__ import annotations

from typing import Any

from prisma import Client as PrismaClient

from src.models.courses import TopicResponse
from src.utils.exceptions import ForbiddenError, ResourceNotFoundError
from src.utils.progress import round_progress_percent


async def calculate_topic_list_progress(topics: list[Any]) -> tuple[float, int, int]:
    """Calculate progress from a list of topics."""
    total = len(topics)
    if total == 0:
        return 0.0, 0, 0

    completed = sum(1 for topic in topics if topic.completed)
    progress = round_progress_percent((completed / total) * 100)

    return progress, total, completed


async def calculate_module_progress(db: PrismaClient, module_id: str) -> tuple[float, bool]:
    """Calculate progress for a single module."""
    topics = await db.topic.find_many(where={"moduleId": module_id})

    if not topics:
        return 0.0, True  # No topics = considered complete

    progress, total, completed = await calculate_topic_list_progress(topics)
    is_completed = completed == total

    return progress, is_completed


async def calculate_course_progress(db: PrismaClient, course_id: str) -> tuple[float, int, int]:
    """Calculate overall course progress based on total topics."""
    total_topics = await db.topic.count(where={"module": {"courseId": course_id}})

    if total_topics == 0:
        return 0.0, 0, 0

    completed_topics = await db.topic.count(
        where={"module": {"courseId": course_id}, "completed": True}
    )

    progress = round_progress_percent((completed_topics / total_topics) * 100)

    return progress, total_topics, completed_topics


async def update_goal_progress_for_course(db: PrismaClient, course_id: str, user_id: str) -> None:
    """Update progress for all goals linked to a course."""
    goals = await db.goal.find_many(
        where={"courseId": course_id, "userId": user_id, "status": "ACTIVE"}
    )

    if not goals:
        return

    course_progress, _, _ = await calculate_course_progress(db, course_id)

    for goal in goals:
        await db.goal.update(
            where={"id": goal.id},
            data={"progress": course_progress},
        )

        if course_progress >= 100.0:
            await db.goal.update(
                where={"id": goal.id},
                data={"status": "COMPLETED"},
            )


async def update_goal_progress_for_topic(
    db: PrismaClient, topic_id: str, user_id: str, completed: bool
) -> None:
    """Update progress for all goals linked to a specific topic."""
    goals = await db.goal.find_many(
        where={"topicId": topic_id, "userId": user_id, "status": "ACTIVE"}
    )

    if not goals:
        return

    progress = 100.0 if completed else 0.0

    for goal in goals:
        await db.goal.update(
            where={"id": goal.id},
            data={"progress": progress},
        )

        if completed:
            await db.goal.update(
                where={"id": goal.id},
                data={"status": "COMPLETED"},
            )


async def enrich_module_with_progress(
    db: PrismaClient, module: Any, include_topics: bool = True
) -> dict[str, Any]:
    """Enrich a module with calculated progress and completion status."""
    topics = await db.topic.find_many(
        where={"moduleId": module.id}, include={"notes": True}, order={"order": "asc"}
    )

    progress, total, completed = await calculate_topic_list_progress(topics)
    is_completed = completed == total if total > 0 else True

    topic_payload = (
        [TopicResponse.model_validate(t, from_attributes=True) for t in topics]
        if include_topics
        else []
    )

    return {
        "id": module.id,
        "courseId": module.courseId,
        "title": module.title,
        "order": module.order,
        "description": module.description,
        "completed": is_completed,
        "progress": progress,
        "topicCount": total,
        "completedTopicCount": completed,
        "topics": topic_payload,
        "createdAt": module.createdAt,
        "updatedAt": module.updatedAt,
    }


async def check_course_ownership(db: PrismaClient, course_id: str, user_id: str) -> Any:
    """Check if course exists and belongs to user."""
    course = await db.course.find_unique(where={"id": course_id})

    if not course:
        raise ResourceNotFoundError("Course", course_id)

    if course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this course")

    return course


async def outline_satisfaction_recorded_for_user(
    db: PrismaClient, user_id: str, course_id: str
) -> bool:
    n = await db.courseoutlinesatisfaction.count(where={"userId": user_id, "courseId": course_id})
    return n > 0


async def check_module_ownership(db: PrismaClient, module_id: str, user_id: str) -> tuple[Any, Any]:
    """Check if module exists and belongs to user (via course)."""
    module = await db.module.find_unique(where={"id": module_id}, include={"course": True})

    if not module:
        raise ResourceNotFoundError("Module", module_id)

    if module.course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this module")

    return module, module.course


async def check_topic_ownership(
    db: PrismaClient, topic_id: str, user_id: str
) -> tuple[Any, Any, Any]:
    """Check if topic exists and belongs to user (via module > course)."""
    topic = await db.topic.find_unique(
        where={"id": topic_id}, include={"module": {"include": {"course": True}}, "notes": True}
    )

    if not topic:
        raise ResourceNotFoundError("Topic", topic_id)

    if topic.module.course.userId != user_id:
        raise ForbiddenError("You don't have permission to access this topic")

    return topic, topic.module, topic.module.course
