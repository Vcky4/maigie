"""
Classrooms domain — Data access layer (SQLAlchemy).

Maps to SpaceChatGroup (classrooms) and SpaceSession (sessions).
Reuses Learning Spaces and Knowledge models.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.knowledge.db_models import Course
from src.domains.learning_spaces.db_models import (
    SpaceChatGroup,
    SpaceChatGroupMember,
    SpaceSession,
)
from src.shared.database import get_session_factory
from src.shared.field_mapping import map_fields

logger = logging.getLogger(__name__)


class ClassroomRepository:
    """Data access for Classrooms and Learning Sessions."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Classrooms (SpaceChatGroup)
    # -----------------------------------------------------------------------

    async def find_classroom(self, classroom_id: str) -> SpaceChatGroup | None:
        async with await self._session() as session:
            stmt = select(SpaceChatGroup).where(SpaceChatGroup.id == classroom_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_classrooms(self, space_id: str) -> list[SpaceChatGroup]:
        async with await self._session() as session:
            stmt = (
                select(SpaceChatGroup)
                .where(SpaceChatGroup.space_id == space_id)
                .order_by(SpaceChatGroup.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_classroom(self, data: dict[str, Any]) -> SpaceChatGroup:
        async with await self._session() as session:
            classroom = SpaceChatGroup(**self._map_classroom(data))
            session.add(classroom)
            await session.commit()
            await session.refresh(classroom)
            return classroom

    async def add_members(self, classroom_id: str, user_ids: list[str]) -> int:
        """Add learners to a classroom. Returns how many rows were written.

        Exists because `ClassroomCreate.memberIds` was accepted and discarded: the field was declared,
        documented as "For PRIVATE: initial member IDs", and named nowhere else in the domain — so
        creating a private classroom with a member list returned `201` and added nobody. The table it
        needed, `SpaceChatGroupMember`, already existed.

        `ON CONFLICT DO NOTHING` against the `(chatGroupId, userId)` unique index, so adding somebody
        who is already in the classroom is a no-op rather than an aborted transaction — otherwise
        re-sending an overlapping list would fail the whole request.
        """
        if not user_ids:
            return 0

        async with await self._session() as session:
            stmt = (
                pg_insert(SpaceChatGroupMember)
                .values(
                    [
                        {"chat_group_id": classroom_id, "user_id": user_id}
                        # De-duplicated here as well as in the database: one statement carrying the same
                        # pair twice raises rather than conflicting, since `ON CONFLICT` cannot resolve a
                        # row against another row in its own insert.
                        for user_id in dict.fromkeys(user_ids)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        SpaceChatGroupMember.chat_group_id,
                        SpaceChatGroupMember.user_id,
                    ]
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0

    async def list_member_ids(self, classroom_id: str) -> list[str]:
        async with await self._session() as session:
            rows = await session.execute(
                select(SpaceChatGroupMember.user_id).where(
                    SpaceChatGroupMember.chat_group_id == classroom_id
                )
            )
            return [row[0] for row in rows]

    async def update_classroom(self, classroom_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_classroom(data)
            stmt = update(SpaceChatGroup).where(SpaceChatGroup.id == classroom_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_classroom(self, classroom_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(SpaceChatGroup).where(SpaceChatGroup.id == classroom_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Learning Sessions (SpaceSession)
    # -----------------------------------------------------------------------

    async def find_session(self, session_id: str) -> SpaceSession | None:
        async with await self._session() as session:
            stmt = select(SpaceSession).where(SpaceSession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(
        self, space_id: str, *, classroom_id: str | None = None
    ) -> list[SpaceSession]:
        async with await self._session() as session:
            conditions = [SpaceSession.space_id == space_id]
            if classroom_id:
                conditions.append(SpaceSession.chat_group_id == classroom_id)
            stmt = (
                select(SpaceSession).where(*conditions).order_by(SpaceSession.scheduled_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_upcoming_sessions(self, space_id: str, *, limit: int = 10) -> list[SpaceSession]:
        now = datetime.now(UTC)
        async with await self._session() as session:
            stmt = (
                select(SpaceSession)
                .where(
                    SpaceSession.space_id == space_id,
                    SpaceSession.status.in_(["SCHEDULED", "ACTIVE"]),
                    SpaceSession.scheduled_at >= now,
                )
                .order_by(SpaceSession.scheduled_at.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_session(self, data: dict[str, Any]) -> SpaceSession:
        async with await self._session() as session:
            cs = SpaceSession(**self._map_session(data))
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            return cs

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_session(data)
            stmt = update(SpaceSession).where(SpaceSession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_session(self, session_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(SpaceSession).where(SpaceSession.id == session_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Assigned Courses
    # -----------------------------------------------------------------------

    async def list_assigned_courses(self, space_id: str) -> list[Course]:
        async with await self._session() as session:
            stmt = (
                select(Course)
                .where(Course.space_id == space_id, Course.archived == False)  # noqa: E712
                .order_by(Course.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def assign_course(self, course_id: str, space_id: str) -> None:
        async with await self._session() as session:
            stmt = update(Course).where(Course.id == course_id).values(space_id=space_id)
            await session.execute(stmt)
            await session.commit()

    async def unassign_course(self, course_id: str) -> None:
        async with await self._session() as session:
            stmt = update(Course).where(Course.id == course_id).values(space_id=None)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Field mapping
    # -----------------------------------------------------------------------

    _CLASSROOM_MAP = {
        "spaceId": "space_id",
        "name": "name",
        "chatSessionId": "chat_session_id",
        "visibility": "visibility",
        "description": "description",
    }

    _SESSION_MAP = {
        "spaceId": "space_id",
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
        return map_fields(data, self._CLASSROOM_MAP, entity="_map_classroom")

    def _map_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SESSION_MAP, entity="_map_session")


# Singleton
classroom_repo = ClassroomRepository()
