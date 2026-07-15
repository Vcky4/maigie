"""
Classrooms domain — Data access layer (SQLAlchemy).

Maps to CircleChatGroup (classrooms) and CircleSession (sessions).
Reuses Learning Spaces and Knowledge models.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory
from src.domains.learning_spaces.db_models import CircleChatGroup, CircleSession
from src.domains.knowledge.db_models import Course

logger = logging.getLogger(__name__)


class ClassroomRepository:
    """Data access for Classrooms and Learning Sessions."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Classrooms (CircleChatGroup)
    # -----------------------------------------------------------------------

    async def find_classroom(self, classroom_id: str) -> CircleChatGroup | None:
        async with await self._session() as session:
            stmt = select(CircleChatGroup).where(CircleChatGroup.id == classroom_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_classrooms(self, space_id: str) -> list[CircleChatGroup]:
        async with await self._session() as session:
            stmt = (
                select(CircleChatGroup)
                .where(CircleChatGroup.circle_id == space_id)
                .order_by(CircleChatGroup.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_classroom(self, data: dict[str, Any]) -> CircleChatGroup:
        async with await self._session() as session:
            classroom = CircleChatGroup(**self._map_classroom(data))
            session.add(classroom)
            await session.commit()
            await session.refresh(classroom)
            return classroom

    async def update_classroom(self, classroom_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_classroom(data)
            stmt = update(CircleChatGroup).where(CircleChatGroup.id == classroom_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_classroom(self, classroom_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(CircleChatGroup).where(CircleChatGroup.id == classroom_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Learning Sessions (CircleSession)
    # -----------------------------------------------------------------------

    async def find_session(self, session_id: str) -> CircleSession | None:
        async with await self._session() as session:
            stmt = select(CircleSession).where(CircleSession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(self, space_id: str, *, classroom_id: str | None = None) -> list[CircleSession]:
        async with await self._session() as session:
            conditions = [CircleSession.circle_id == space_id]
            if classroom_id:
                conditions.append(CircleSession.chat_group_id == classroom_id)
            stmt = (
                select(CircleSession)
                .where(*conditions)
                .order_by(CircleSession.scheduled_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_upcoming_sessions(self, space_id: str, *, limit: int = 10) -> list[CircleSession]:
        now = datetime.now(UTC)
        async with await self._session() as session:
            stmt = (
                select(CircleSession)
                .where(
                    CircleSession.circle_id == space_id,
                    CircleSession.status.in_(["SCHEDULED", "ACTIVE"]),
                    CircleSession.scheduled_at >= now,
                )
                .order_by(CircleSession.scheduled_at.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_session(self, data: dict[str, Any]) -> CircleSession:
        async with await self._session() as session:
            cs = CircleSession(**self._map_session(data))
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            return cs

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_session(data)
            stmt = update(CircleSession).where(CircleSession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_session(self, session_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(CircleSession).where(CircleSession.id == session_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Assigned Courses
    # -----------------------------------------------------------------------

    async def list_assigned_courses(self, space_id: str) -> list[Course]:
        async with await self._session() as session:
            stmt = (
                select(Course)
                .where(Course.circle_id == space_id, Course.archived == False)  # noqa: E712
                .order_by(Course.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def assign_course(self, course_id: str, space_id: str) -> None:
        async with await self._session() as session:
            stmt = update(Course).where(Course.id == course_id).values(circle_id=space_id)
            await session.execute(stmt)
            await session.commit()

    async def unassign_course(self, course_id: str) -> None:
        async with await self._session() as session:
            stmt = update(Course).where(Course.id == course_id).values(circle_id=None)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    _CLASSROOM_MAP = {
        "circleId": "circle_id",
        "name": "name",
        "chatSessionId": "chat_session_id",
        "visibility": "visibility",
        "description": "description",
    }

    _SESSION_MAP = {
        "circleId": "circle_id",
        "title": "title",
        "description": "description",
        "scheduledAt": "scheduled_at",
        "duration": "duration",
        "status": "status",
        "chatGroupId": "chat_group_id",
        "topicId": "topic_id",
        "goalId": "goal_id",
        "createdById": "created_by_id",
    }

    def _map_classroom(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._CLASSROOM_MAP.get(k, k): v for k, v in data.items() if k in self._CLASSROOM_MAP}

    def _map_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._SESSION_MAP.get(k, k): v for k, v in data.items() if k in self._SESSION_MAP}


# Singleton
classroom_repo = ClassroomRepository()
