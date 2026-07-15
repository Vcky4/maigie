"""
Classrooms domain — Data access layer.

Currently maps to CircleChatGroup (classrooms) and CircleSession (sessions).
Will transition to dedicated Classroom table in Phase 10.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


class ClassroomRepository:
    """Data access for Classrooms and Learning Sessions."""

    # -----------------------------------------------------------------------
    # Classrooms (currently CircleChatGroup)
    # -----------------------------------------------------------------------

    async def find_classroom(self, classroom_id: str):
        """Find a classroom (chat group) by ID."""
        return await db.circlechatgroup.find_unique(
            where={"id": classroom_id},
            include={"circle": True},
        )

    async def list_classrooms(self, space_id: str) -> list:
        """List all classrooms in a Learning Space."""
        return await db.circlechatgroup.find_many(
            where={"circleId": space_id},
            order={"createdAt": "desc"},
        )

    async def create_classroom(self, data: dict[str, Any]):
        return await db.circlechatgroup.create(data=data)

    async def update_classroom(self, classroom_id: str, data: dict[str, Any]):
        return await db.circlechatgroup.update(where={"id": classroom_id}, data=data)

    async def delete_classroom(self, classroom_id: str):
        return await db.circlechatgroup.delete(where={"id": classroom_id})

    # -----------------------------------------------------------------------
    # Learning Sessions (currently CircleSession)
    # -----------------------------------------------------------------------

    async def find_session(self, session_id: str):
        return await db.circlesession.find_unique(where={"id": session_id})

    async def list_sessions(self, space_id: str, *, classroom_id: str | None = None) -> list:
        """List sessions in a space, optionally filtered by classroom."""
        where: dict[str, Any] = {"circleId": space_id}
        if classroom_id:
            where["chatGroupId"] = classroom_id
        return await db.circlesession.find_many(
            where=where,
            order={"scheduledAt": "desc"},
        )

    async def list_upcoming_sessions(self, space_id: str, *, limit: int = 10) -> list:
        """List upcoming sessions (scheduled or active)."""
        from datetime import UTC, datetime

        return await db.circlesession.find_many(
            where={
                "circleId": space_id,
                "status": {"in": ["SCHEDULED", "ACTIVE"]},
                "scheduledAt": {"gte": datetime.now(UTC)},
            },
            order={"scheduledAt": "asc"},
            take=limit,
        )

    async def create_session(self, data: dict[str, Any]):
        return await db.circlesession.create(data=data)

    async def update_session(self, session_id: str, data: dict[str, Any]):
        return await db.circlesession.update(where={"id": session_id}, data=data)

    async def delete_session(self, session_id: str):
        return await db.circlesession.delete(where={"id": session_id})

    # -----------------------------------------------------------------------
    # Assigned Courses (courses linked to a circle/space)
    # -----------------------------------------------------------------------

    async def list_assigned_courses(self, space_id: str) -> list:
        """List courses assigned to a Learning Space."""
        return await db.course.find_many(
            where={"circleId": space_id, "archived": False},
            order={"createdAt": "desc"},
        )

    async def assign_course(self, course_id: str, space_id: str):
        """Assign a course to a space (set circleId on course)."""
        return await db.course.update(
            where={"id": course_id},
            data={"circleId": space_id},
        )

    async def unassign_course(self, course_id: str):
        """Remove course from space (set circleId to null)."""
        return await db.course.update(
            where={"id": course_id},
            data={"circleId": None},
        )


# Singleton
classroom_repo = ClassroomRepository()
