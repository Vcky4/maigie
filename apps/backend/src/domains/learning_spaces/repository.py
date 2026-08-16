"""
Learning Spaces domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Space, SpaceMember, SpaceInvite,
SpaceChatGroup, SpaceSession, SpaceSeatAddon.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory
from src.shared.field_mapping import map_fields

from .db_models import (
    Space,
    SpaceChatGroup,
    SpaceChatGroupMember,
    SpaceInvite,
    SpaceJoinRequest,
    SpaceMember,
    SpaceMemberStat,
    SpaceSeatAddon,
    SpaceSession,
)

logger = logging.getLogger(__name__)


class LearningSpaceRepository:
    """Data access for Learning Spaces."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Spaces
    # -----------------------------------------------------------------------

    async def find_space(self, space_id: str) -> Space | None:
        async with await self._session() as session:
            stmt = (
                select(Space)
                .options(
                    selectinload(Space.members).selectinload(SpaceMember.user),
                    selectinload(Space.chat_groups),
                )
                .where(Space.id == space_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_space_basic(self, space_id: str) -> Space | None:
        async with await self._session() as session:
            stmt = select(Space).where(Space.id == space_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_user_spaces(self, user_id: str) -> list[SpaceMember]:
        async with await self._session() as session:
            stmt = (
                select(SpaceMember)
                .options(
                    selectinload(SpaceMember.space)
                    .selectinload(Space.members)
                    .selectinload(SpaceMember.user)
                )
                .where(SpaceMember.user_id == user_id)
                .order_by(SpaceMember.joined_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_space(self, data: dict[str, Any]) -> Space:
        async with await self._session() as session:
            space = Space(**self._map_space(data))
            session.add(space)
            await session.commit()
            await session.refresh(space)
            return space

    async def update_space(self, space_id: str, data: dict[str, Any]) -> Space:
        async with await self._session() as session:
            mapped = self._map_space(data)
            stmt = update(Space).where(Space.id == space_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        return await self.find_space(space_id)

    async def delete_space(self, space_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Space).where(Space.id == space_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    async def find_member(self, space_id: str, user_id: str) -> SpaceMember | None:
        async with await self._session() as session:
            stmt = (
                select(SpaceMember)
                .options(selectinload(SpaceMember.user))
                .where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def add_member(self, data: dict[str, Any]) -> SpaceMember:
        async with await self._session() as session:
            member = SpaceMember(**self._map_member(data))
            session.add(member)
            await session.commit()
            await session.refresh(member)
            return member

    async def update_member(self, space_id: str, user_id: str, data: dict[str, Any]) -> SpaceMember:
        async with await self._session() as session:
            stmt = (
                update(SpaceMember)
                .where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
                .values(**self._map_member(data))
            )
            await session.execute(stmt)
            await session.commit()
        return await self.find_member(space_id, user_id)

    async def remove_member(self, space_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(SpaceMember).where(
                SpaceMember.space_id == space_id, SpaceMember.user_id == user_id
            )
            await session.execute(stmt)
            await session.commit()

    async def count_members(self, space_id: str) -> int:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(SpaceMember)
                .where(SpaceMember.space_id == space_id)
            )
            return (await session.execute(stmt)).scalar() or 0

    async def count_plus_seats(self, space_id: str) -> int:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(SpaceMember)
                .where(SpaceMember.space_id == space_id, SpaceMember.seat_tier == "PLUS_SEAT")
            )
            return (await session.execute(stmt)).scalar() or 0

    async def list_plus_members(self, space_id: str) -> list[SpaceMember]:
        async with await self._session() as session:
            stmt = (
                select(SpaceMember)
                .options(selectinload(SpaceMember.user))
                .where(SpaceMember.space_id == space_id, SpaceMember.seat_tier == "PLUS_SEAT")
                .order_by(SpaceMember.joined_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Invitations
    # -----------------------------------------------------------------------

    async def create_invite(self, data: dict[str, Any]) -> SpaceInvite:
        async with await self._session() as session:
            invite = SpaceInvite(**self._map_invite(data))
            session.add(invite)
            await session.commit()
            await session.refresh(invite)
            return invite

    async def find_invite(self, invite_id: str) -> SpaceInvite | None:
        async with await self._session() as session:
            stmt = select(SpaceInvite).where(SpaceInvite.id == invite_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_pending_invites(self, space_id: str) -> list[SpaceInvite]:
        async with await self._session() as session:
            stmt = (
                select(SpaceInvite)
                .where(SpaceInvite.space_id == space_id, SpaceInvite.status == "PENDING")
                .order_by(SpaceInvite.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_user_invites(self, email: str) -> list[SpaceInvite]:
        async with await self._session() as session:
            stmt = (
                select(SpaceInvite)
                .where(
                    func.lower(SpaceInvite.invitee_email) == email.lower(),
                    SpaceInvite.status == "PENDING",
                    SpaceInvite.expires_at > datetime.now(UTC),
                )
                .order_by(SpaceInvite.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_invite(self, invite_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_invite(data)
            stmt = update(SpaceInvite).where(SpaceInvite.id == invite_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Chat Groups
    # -----------------------------------------------------------------------

    async def create_chat_group(self, data: dict[str, Any]) -> SpaceChatGroup:
        async with await self._session() as session:
            group = SpaceChatGroup(**self._map_chat_group(data))
            session.add(group)
            await session.commit()
            await session.refresh(group)
            return group

    async def add_chat_group_members(self, group_id: str, user_ids: list[str]) -> int:
        """Add members to a chat group. Returns how many rows were written.

        Exists because `ChatGroupCreate.memberIds` was accepted and discarded: the field was declared on
        the request model and named nowhere else in the domain, so creating a group with members created
        an empty one and reported success. `SpaceChatGroupMember` already existed.

        `ON CONFLICT DO NOTHING` on the `(chatGroupId, userId)` unique index, so re-adding an existing
        member is a no-op rather than an aborted transaction.
        """
        if not user_ids:
            return 0

        async with await self._session() as session:
            stmt = (
                pg_insert(SpaceChatGroupMember)
                .values(
                    [
                        {"chat_group_id": group_id, "user_id": user_id}
                        # De-duplicated in Python too: `ON CONFLICT` cannot resolve a row against
                        # another row inside its own insert, so a list containing the same id twice
                        # would raise rather than conflict.
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

    async def list_member_ids(self, space_id: str) -> set[str]:
        """The user ids in a space, for checking a submitted member list against.

        A set of ids rather than the `SpaceMember` rows, because the one caller is asking "are these
        people in this space" and loading full rows to answer it would fetch roles and stats nobody
        reads.
        """
        async with await self._session() as session:
            rows = await session.execute(
                select(SpaceMember.user_id).where(SpaceMember.space_id == space_id)
            )
            return {row[0] for row in rows}

    async def find_chat_group(self, group_id: str) -> SpaceChatGroup | None:
        async with await self._session() as session:
            stmt = select(SpaceChatGroup).where(SpaceChatGroup.id == group_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_chat_groups(self, space_id: str) -> list[SpaceChatGroup]:
        async with await self._session() as session:
            stmt = select(SpaceChatGroup).where(SpaceChatGroup.space_id == space_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_chat_group(self, group_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_chat_group(data)
            stmt = update(SpaceChatGroup).where(SpaceChatGroup.id == group_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_chat_group(self, group_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(SpaceChatGroup).where(SpaceChatGroup.id == group_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Sessions (SpaceSession)
    # -----------------------------------------------------------------------

    async def create_session(self, data: dict[str, Any]) -> SpaceSession:
        async with await self._session() as session:
            cs = SpaceSession(**self._map_space_session(data))
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            return cs

    async def find_session(self, session_id: str) -> SpaceSession | None:
        async with await self._session() as session:
            stmt = select(SpaceSession).where(SpaceSession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(self, space_id: str) -> list[SpaceSession]:
        async with await self._session() as session:
            stmt = (
                select(SpaceSession)
                .where(SpaceSession.space_id == space_id)
                .order_by(SpaceSession.scheduled_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_upcoming_sessions(self, space_id: str, *, limit: int = 10) -> list[SpaceSession]:
        from datetime import UTC

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

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_space_session(data)
            stmt = update(SpaceSession).where(SpaceSession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_session(self, session_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(SpaceSession).where(SpaceSession.id == session_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Seat Addons
    # -----------------------------------------------------------------------

    async def list_seat_addons(self, space_id: str) -> list[SpaceSeatAddon]:
        async with await self._session() as session:
            stmt = (
                select(SpaceSeatAddon)
                .where(SpaceSeatAddon.space_id == space_id)
                .order_by(SpaceSeatAddon.purchased_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    _SPACE_MAP = {
        "name": "name",
        "description": "description",
        "avatarUrl": "avatar_url",
        "createdById": "created_by_id",
        "maxMembers": "max_members",
        "maxGroups": "max_groups",
        "credits": "credits",
        "creditsLimit": "credits_limit",
        "visibility": "visibility",
        "category": "category",
        "bannerUrl": "banner_url",
        "themeJson": "theme_json",
        "circlePlanActive": "space_plan_active",
        "circlePlanCurrentPeriodEnd": "space_plan_current_period_end",
        "seatPoolSize": "seat_pool_size",
        "hiddenByModeration": "hidden_by_moderation",
        "allowMemberExport": "allow_member_export",
        "featured": "featured",
        "joinPolicy": "join_policy",
    }

    _MEMBER_MAP = {
        "spaceId": "space_id",
        "userId": "user_id",
        "role": "role",
        "seatTier": "seat_tier",
    }

    _INVITE_MAP = {
        "spaceId": "space_id",
        "inviterId": "inviter_id",
        "inviteeEmail": "invitee_email",
        "inviteeId": "invitee_id",
        "status": "status",
        "role": "role",
        "seatTier": "seat_tier",
        "expiresAt": "expires_at",
    }

    _CHAT_GROUP_MAP = {
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

    def _map_space(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SPACE_MAP, entity="_map_space")

    def _map_member(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._MEMBER_MAP, entity="_map_member")

    def _map_invite(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._INVITE_MAP, entity="_map_invite")

    def _map_chat_group(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._CHAT_GROUP_MAP, entity="_map_chat_group")

    def _map_space_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SESSION_MAP, entity="_map_space_session")


# Singleton
space_repo = LearningSpaceRepository()
