"""
Topic Progress Service — per-user progress tracking for shared Circle courses.

For personal courses (circleId=null), Topic.completed is still the source of truth.
For Circle courses (circleId set), UserTopicProgress is the source of truth per member.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from prisma import Prisma

logger = logging.getLogger(__name__)


async def get_user_topic_completed(
    db: Prisma, user_id: str, topic_id: str, course_circle_id: str | None
) -> bool:
    """
    Check if a topic is completed for a given user.
    For personal courses: reads Topic.completed directly.
    For circle courses: reads UserTopicProgress.
    """
    if not course_circle_id:
        # Personal course: use the legacy Topic.completed field
        topic = await db.topic.find_unique(where={"id": topic_id})
        return bool(topic and topic.completed)

    # Circle course: check UserTopicProgress
    progress = await db.usertopicprogress.find_unique(
        where={"userId_topicId": {"userId": user_id, "topicId": topic_id}}
    )
    return bool(progress and progress.completed)


async def mark_topic_completed(
    db: Prisma, user_id: str, topic_id: str, course_circle_id: str | None, completed: bool = True
) -> dict:
    """
    Mark a topic as completed/incomplete for a user.
    For personal courses: updates Topic.completed.
    For circle courses: upserts UserTopicProgress.
    """
    if not course_circle_id:
        # Personal course: update Topic.completed directly (legacy behavior)
        updated = await db.topic.update(
            where={"id": topic_id},
            data={"completed": completed},
        )
        return {"topicId": topic_id, "completed": completed, "source": "topic"}

    # Circle course: upsert UserTopicProgress
    progress = await db.usertopicprogress.upsert(
        where={"userId_topicId": {"userId": user_id, "topicId": topic_id}},
        create={
            "userId": user_id,
            "topicId": topic_id,
            "completed": completed,
            "completedAt": datetime.now(UTC) if completed else None,
        },
        update={
            "completed": completed,
            "completedAt": datetime.now(UTC) if completed else None,
        },
    )
    return {"topicId": topic_id, "completed": completed, "source": "user_progress"}


async def get_user_course_progress(
    db: Prisma, user_id: str, course_id: str, course_circle_id: str | None
) -> dict:
    """
    Calculate progress for a user on a course.
    Returns: { progress: float, totalTopics: int, completedTopics: int }
    """
    # Get all topics in the course
    modules = await db.module.find_many(
        where={"courseId": course_id},
        include={"topics": True},
    )
    all_topics = [t for m in modules for t in m.topics]
    total = len(all_topics)

    if total == 0:
        return {"progress": 0.0, "totalTopics": 0, "completedTopics": 0}

    if not course_circle_id:
        # Personal course: count Topic.completed
        completed = sum(1 for t in all_topics if t.completed)
    else:
        # Circle course: count UserTopicProgress records
        topic_ids = [t.id for t in all_topics]
        progress_records = await db.usertopicprogress.find_many(
            where={
                "userId": user_id,
                "topicId": {"in": topic_ids},
                "completed": True,
            }
        )
        completed = len(progress_records)

    progress_pct = round((completed / total) * 100, 1) if total > 0 else 0.0
    return {"progress": progress_pct, "totalTopics": total, "completedTopics": completed}


async def get_user_module_progress(
    db: Prisma, user_id: str, module_id: str, course_circle_id: str | None
) -> dict:
    """
    Calculate progress for a user on a module.
    Returns: { progress: float, totalTopics: int, completedTopics: int, completed: bool }
    """
    topics = await db.topic.find_many(where={"moduleId": module_id})
    total = len(topics)

    if total == 0:
        return {"progress": 0.0, "totalTopics": 0, "completedTopics": 0, "completed": True}

    if not course_circle_id:
        completed = sum(1 for t in topics if t.completed)
    else:
        topic_ids = [t.id for t in topics]
        progress_records = await db.usertopicprogress.find_many(
            where={
                "userId": user_id,
                "topicId": {"in": topic_ids},
                "completed": True,
            }
        )
        completed = len(progress_records)

    progress_pct = round((completed / total) * 100, 1) if total > 0 else 0.0
    return {
        "progress": progress_pct,
        "totalTopics": total,
        "completedTopics": completed,
        "completed": completed == total,
    }


async def get_topics_with_user_progress(
    db: Prisma, user_id: str, module_id: str, course_circle_id: str | None
) -> list[dict]:
    """
    Return all topics in a module with per-user completion status.
    """
    topics = await db.topic.find_many(
        where={"moduleId": module_id},
        order={"order": "asc"},
        include={"notes": True},
    )

    if not course_circle_id:
        # Personal: use Topic.completed
        return [
            {
                "id": t.id,
                "title": t.title,
                "content": t.content,
                "order": t.order,
                "completed": t.completed,
                "estimatedHours": t.estimatedHours,
            }
            for t in topics
        ]

    # Circle: batch-fetch progress
    topic_ids = [t.id for t in topics]
    progress_records = await db.usertopicprogress.find_many(
        where={"userId": user_id, "topicId": {"in": topic_ids}},
    )
    completed_set = {p.topicId for p in progress_records if p.completed}

    return [
        {
            "id": t.id,
            "title": t.title,
            "content": t.content,
            "order": t.order,
            "completed": t.id in completed_set,
            "estimatedHours": t.estimatedHours,
        }
        for t in topics
    ]
