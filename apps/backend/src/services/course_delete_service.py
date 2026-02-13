"""
Service for deleting a course with proper cascade handling.
Used by both the API route and the AI tool handler.
"""

from typing import Any

from prisma import PrismaClient

from src.utils.exceptions import ForbiddenError, ResourceNotFoundError


async def delete_course_cascade(db: PrismaClient, course_id: str, user_id: str) -> None:
    """
    Delete a course and its dependent data.
    - Deletes: goals, schedule blocks linked to course/topics/goals
    - Keeps: notes (courseId/topicId set to null)
    - Cascades: modules and topics (via Prisma)
    """
    course = await db.course.find_unique(where={"id": course_id})
    if not course:
        raise ResourceNotFoundError("Course", course_id)
    if course.userId != user_id:
        raise ForbiddenError("You don't have permission to delete this course")

    modules = await db.module.find_many(
        where={"courseId": course_id},
        include={"topics": True},
    )
    topic_ids = [t.id for m in modules for t in m.topics]

    if topic_ids:
        goals = await db.goal.find_many(
            where={"OR": [{"courseId": course_id}, {"topicId": {"in": topic_ids}}]}
        )
    else:
        goals = await db.goal.find_many(where={"courseId": course_id})
    goal_ids = [g.id for g in goals]

    # 1. Detach notes
    await db.note.update_many(
        where={"courseId": course_id},
        data={"courseId": None},
    )
    if topic_ids:
        await db.note.update_many(
            where={"topicId": {"in": topic_ids}},
            data={"topicId": None},
        )

    # 2. Delete schedule blocks
    sb_or: list[dict[str, Any]] = [{"courseId": course_id}]
    if topic_ids:
        sb_or.append({"topicId": {"in": topic_ids}})
    if goal_ids:
        sb_or.append({"goalId": {"in": goal_ids}})
    await db.scheduleblock.delete_many(where={"OR": sb_or} if len(sb_or) > 1 else sb_or[0])

    # 3. Delete goals
    if goal_ids:
        await db.goal.delete_many(where={"id": {"in": goal_ids}})
    else:
        await db.goal.delete_many(where={"courseId": course_id})

    # 4. Delete course (cascades modules and topics)
    await db.course.delete(where={"id": course_id})
