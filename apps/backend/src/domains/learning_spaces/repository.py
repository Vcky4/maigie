"""
Learning Spaces domain — Data access layer.

Encapsulates all Prisma queries for Circle (to be renamed LearningSpace),
CircleMember (SpaceMember), CircleInvite, CircleChatGroup, CircleSession.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


class LearningSpaceRepository:
    """Data access for Learning Spaces (currently Circle tables in DB)."""

    # -----------------------------------------------------------------------
    # Spaces (Circle table)
    # -----------------------------------------------------------------------

    async def find_space(self, space_id: str, *, include_members: bool = True):
        includes: dict[str, Any] = {}
        if include_members:
            includes["members"] = {"include": {"user": True}}
            includes["chatGroups"] = True
            includes["courses"] = True
        return await db.circle.find_unique(
            where={"id": space_id},
            include=includes or None,
        )

    async def list_user_spaces(self, user_id: str):
        """List all spaces a user belongs to."""
        memberships = await db.circlemember.find_many(
            where={"userId": user_id},
            include={
                "circle": {
                    "include": {
                        "members": {"include": {"user": True}},
                    }
                }
            },
            order={"joinedAt": "desc"},
        )
        return memberships

    async def create_space(self, data: dict[str, Any]):
        return await db.circle.create(
            data=data,
            include={
                "members": {"include": {"user": True}},
                "chatGroups": True,
            },
        )

    async def update_space(self, space_id: str, data: dict[str, Any]):
        return await db.circle.update(
            where={"id": space_id},
            data=data,
            include={
                "members": {"include": {"user": True}},
                "chatGroups": True,
                "courses": True,
            },
        )

    async def delete_space(self, space_id: str):
        return await db.circle.delete(where={"id": space_id})

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    async def find_member(self, space_id: str, user_id: str):
        return await db.circlemember.find_first(
            where={"circleId": space_id, "userId": user_id},
            include={"user": True},
        )

    async def find_member_by_composite(self, space_id: str, user_id: str):
        return await db.circlemember.find_unique(
            where={"circleId_userId": {"circleId": space_id, "userId": user_id}}
        )

    async def add_member(self, data: dict[str, Any]):
        return await db.circlemember.create(data=data)

    async def update_member(self, member_id: str, data: dict[str, Any]):
        return await db.circlemember.update(where={"id": member_id}, data=data)

    async def remove_member(self, member_id: str):
        return await db.circlemember.delete(where={"id": member_id})

    async def count_members(self, space_id: str) -> int:
        return await db.circlemember.count(where={"circleId": space_id})

    # -----------------------------------------------------------------------
    # Invitations
    # -----------------------------------------------------------------------

    async def create_invite(self, data: dict[str, Any]):
        return await db.circleinvite.create(data=data)

    async def find_invite(self, invite_id: str):
        return await db.circleinvite.find_unique(where={"id": invite_id})

    async def list_pending_invites(self, space_id: str):
        return await db.circleinvite.find_many(
            where={"circleId": space_id, "status": "PENDING"},
            order={"createdAt": "desc"},
        )

    async def list_user_invites(self, email: str):
        return await db.circleinvite.find_many(
            where={"inviteeEmail": email, "status": "PENDING"},
            include={"circle": True},
            order={"createdAt": "desc"},
        )

    async def update_invite(self, invite_id: str, data: dict[str, Any]):
        return await db.circleinvite.update(where={"id": invite_id}, data=data)

    # -----------------------------------------------------------------------
    # Chat Groups
    # -----------------------------------------------------------------------

    async def create_chat_group(self, data: dict[str, Any]):
        return await db.circlechatgroup.create(data=data)

    async def find_chat_group(self, group_id: str):
        return await db.circlechatgroup.find_unique(where={"id": group_id})

    async def update_chat_group(self, group_id: str, data: dict[str, Any]):
        return await db.circlechatgroup.update(where={"id": group_id}, data=data)

    async def delete_chat_group(self, group_id: str):
        return await db.circlechatgroup.delete(where={"id": group_id})

    # -----------------------------------------------------------------------
    # Sessions
    # -----------------------------------------------------------------------

    async def create_session(self, data: dict[str, Any]):
        return await db.circlesession.create(data=data)

    async def find_session(self, session_id: str):
        return await db.circlesession.find_unique(where={"id": session_id})

    async def list_sessions(self, space_id: str):
        return await db.circlesession.find_many(
            where={"circleId": space_id},
            order={"scheduledAt": "desc"},
        )

    async def update_session(self, session_id: str, data: dict[str, Any]):
        return await db.circlesession.update(where={"id": session_id}, data=data)

    async def delete_session(self, session_id: str):
        return await db.circlesession.delete(where={"id": session_id})

    # -----------------------------------------------------------------------
    # Seats
    # -----------------------------------------------------------------------

    async def list_seat_addons(self, space_id: str):
        return await db.circleseataddon.find_many(
            where={"circleId": space_id},
            include={"assignee": True},
            order={"createdAt": "desc"},
        )


# Singleton
space_repo = LearningSpaceRepository()
