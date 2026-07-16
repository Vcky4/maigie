"""
Service for deleting a course with proper cascade handling.
Used by both the API route and the AI tool handler.
"""

from typing import Any

from sqlalchemy import select, update, delete

from src.shared.database import get_session_factory
from src.domains.knowledge.db_models import Course, Module, Topic
from src.domains.progress.db_models import Goal, ScheduleBlock
from src.domains.personal_learning.db_models import Note
from src.utils.exceptions import ForbiddenError, ResourceNotFoundError


async def delete_course_cascade(db: Any, course_id: str, user_id: str) -> None:
    """
    Delete a course and its dependent data.
    - Deletes: goals, schedule blocks linked to course/topics/goals
    - Keeps: notes (courseId/topicId set to null)
    - Cascades: modules and topics (via ON DELETE CASCADE)
    """
    factory = get_session_factory()

    # Fetch course
    async with factory() as session:
        result = await session.execute(select(Course).where(Course.id == course_id))
        course = result.scalar_one_or_none()

    if not course:
        raise ResourceNotFoundError("Course", course_id)
    if course.user_id != user_id:
        raise ForbiddenError("You don't have permission to delete this course")

    # Get topic IDs for cascade cleanup
    async with factory() as session:
        stmt = (
            select(Topic.id)
            .join(Module, Topic.module_id == Module.id)
            .where(Module.course_id == course_id)
        )
        result = await session.execute(stmt)
        topic_ids = [row[0] for row in result.all()]

    # Get goal IDs linked to this course or its topics
    async with factory() as session:
        conditions = [Goal.course_id == course_id]
        if topic_ids:
            conditions = [Goal.course_id == course_id]
            goal_stmt = select(Goal.id).where(
                (Goal.course_id == course_id) | (Goal.topic_id.in_(topic_ids))
            )
        else:
            goal_stmt = select(Goal.id).where(Goal.course_id == course_id)
        result = await session.execute(goal_stmt)
        goal_ids = [row[0] for row in result.all()]

    # Execute cleanup in a single session
    async with factory() as session:
        # 1. Detach notes (set courseId/topicId to null)
        await session.execute(
            update(Note).where(Note.course_id == course_id).values(course_id=None)
        )
        if topic_ids:
            await session.execute(
                update(Note).where(Note.topic_id.in_(topic_ids)).values(topic_id=None)
            )

        # 2. Delete schedule blocks linked to course/topics/goals
        sb_conditions = [ScheduleBlock.course_id == course_id]
        if topic_ids:
            sb_conditions.append(ScheduleBlock.topic_id.in_(topic_ids))
        if goal_ids:
            sb_conditions.append(ScheduleBlock.goal_id.in_(goal_ids))

        # Use OR for multiple conditions
        from sqlalchemy import or_

        await session.execute(delete(ScheduleBlock).where(or_(*sb_conditions)))

        # 3. Delete goals
        if goal_ids:
            await session.execute(delete(Goal).where(Goal.id.in_(goal_ids)))

        # 4. Delete course (modules and topics cascade via FK ON DELETE CASCADE)
        await session.execute(delete(Course).where(Course.id == course_id))

        await session.commit()
