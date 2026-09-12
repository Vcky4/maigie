"""Admin courses — a staff view over the knowledge domain.

Read-only list/detail plus a hard delete, all over real `Course`/`Module`/`Topic` rows. Cross-domain
by nature; per Decision 2 it should migrate behind a knowledge-domain read as one appears.
"""

from __future__ import annotations

from sqlalchemy import func, select

from src.shared.database import get_session_factory, ilike_any
from src.shared.exceptions import NotFoundError

from .. import models
from .analytics_service import _counts_for_courses

_PAGE_MAX = 200


async def list_courses(
    *,
    page: int,
    page_size: int,
    user_id: str | None = None,
    difficulty: str | None = None,
    is_ai_generated: bool | None = None,
    archived: bool | None = None,
    search: str | None = None,
) -> models.AdminCourseListResponse:
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course

    conditions = []
    if user_id:
        conditions.append(Course.user_id == user_id)
    if difficulty:
        conditions.append(Course.difficulty == difficulty)
    if is_ai_generated is not None:
        conditions.append(Course.is_ai_generated.is_(is_ai_generated))
    if archived is not None:
        conditions.append(Course.archived.is_(archived))
    if search:
        conditions.append(ilike_any(search, Course.title, Course.description))

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(Course).where(*conditions))
        ).scalar() or 0
        rows = (
            await session.execute(
                select(Course, User.email, User.name)
                .join(User, User.id == Course.user_id)
                .where(*conditions)
                .order_by(Course.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        modules, topics = await _counts_for_courses(session, [c.id for c, _e, _n in rows])

    courses = []
    for course, email, name in rows:
        m_total, _m_done = modules.get(course.id, (0, 0))
        t_total, t_done = topics.get(course.id, (0, 0))
        courses.append(
            models.AdminCourseItem(
                id=course.id,
                userId=course.user_id,
                userEmail=email,
                userName=name,
                title=course.title,
                description=course.description,
                difficulty=course.difficulty,
                isAIGenerated=course.is_ai_generated,
                archived=course.archived,
                progress=course.progress or 0.0,
                totalTopics=t_total,
                completedTopics=t_done,
                moduleCount=m_total,
                createdAt=course.created_at,
                updatedAt=course.updated_at,
            )
        )

    import math

    return models.AdminCourseListResponse(
        courses=courses,
        total=total,
        page=page,
        pageSize=page_size,
        totalPages=math.ceil(total / page_size) if total else 0,
    )


async def course_detail(course_id: str) -> models.AdminCourseDetail:
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(Course, User.email, User.name)
                .join(User, User.id == Course.user_id)
                .where(Course.id == course_id)
            )
        ).first()
        if row is None:
            raise NotFoundError("Course", course_id)
        course, email, name = row
        # `modules`/`topics` are selectin relationships, so they load with the course. Access them
        # inside the session to be safe.
        modules_out: list[models.AdminModuleItem] = []
        course_total_topics = 0
        course_completed_topics = 0
        for module in course.modules:
            topics_out: list[models.AdminTopicItem] = []
            m_total = 0
            m_done = 0
            for topic in module.topics:
                m_total += 1
                if topic.completed:
                    m_done += 1
                topics_out.append(
                    models.AdminTopicItem(
                        id=topic.id,
                        title=topic.title,
                        content=topic.content,
                        order=topic.order,
                        completed=topic.completed,
                        estimatedHours=topic.estimated_hours,
                        createdAt=topic.created_at,
                    )
                )
            course_total_topics += m_total
            course_completed_topics += m_done
            modules_out.append(
                models.AdminModuleItem(
                    id=module.id,
                    title=module.title,
                    description=module.description,
                    order=module.order,
                    completed=module.completed,
                    progress=round(m_done / m_total * 100, 1) if m_total else 0.0,
                    totalTopics=m_total,
                    completedTopics=m_done,
                    topics=topics_out,
                )
            )

    return models.AdminCourseDetail(
        id=course.id,
        userId=course.user_id,
        userEmail=email,
        userName=name,
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        targetDate=course.target_date,
        isAIGenerated=course.is_ai_generated,
        archived=course.archived,
        progress=course.progress or 0.0,
        totalTopics=course_total_topics,
        completedTopics=course_completed_topics,
        modules=modules_out,
        createdAt=course.created_at,
        updatedAt=course.updated_at,
    )


async def delete_course(course_id: str) -> None:
    from src.domains.knowledge.db_models import Course

    factory = get_session_factory()
    async with factory() as session:
        course = (
            await session.execute(select(Course).where(Course.id == course_id))
        ).scalar_one_or_none()
        if course is None:
            raise NotFoundError("Course", course_id)
        await session.delete(course)
        await session.commit()
