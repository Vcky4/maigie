"""
Learning Spaces domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Circle, CircleMember, CircleInvite,
CircleChatGroup, CircleSession, CircleSeatAddon.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, update, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from .db_models import (
    Circle,
    CircleChatGroup,
    CircleChatGroupMember,
    CircleInvite,
    CircleMember,
    CircleMemberStat,
    CircleSeatAddon,
    CircleSession,
    CircleJoinRequest,
)

logger = logging.getLogger(__name__)


class LearningSpaceRepository:
    """Data access for Learning Spaces (Circle tables)."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Spaces (Circle)
    # -----------------------------------------------------------------------

    async def find_space(self, space_id: str) -> Circle | None:
        async with await self._session() as session:
            stmt = (
                select(Circle)
                .options(
                    selectinload(Circle.members).selectinload(CircleMember.user),
                    selectinload(Circle.chat_groups),
                )
                .where(Circle.id == space_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_space_basic(self, space_id: str) -> Circle | None:
        async with await self._session() as session:
            stmt = select(Circle).where(Circle.id == space_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_user_spaces(self, user_id: str) -> list[CircleMember]:
        async with await self._session() as session:
            stmt = (
                select(CircleMember)
                .options(selectinload(CircleMember.circle).selectinload(Circle.members).selectinload(CircleMember.user))
                .where(CircleMember.user_id == user_id)
                .order_by(CircleMember.joined_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_space(self, data: dict[str, Any]) -> Circle:
        async with await self._session() as session:
            circle = Circle(**self._map_circle(data))
            session.add(circle)
            await session.commit()
            await session.refresh(circle)
            return circle

    async def update_space(self, space_id: str, data: dict[str, Any]) -> Circle:
        async with await self._session() as session:
            mapped = self._map_circle(data)
            stmt = update(Circle).where(Circle.id == space_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        return await self.find_space(space_id)

    async def delete_space(self, space_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Circle).where(Circle.id == space_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    async def find_member(self, space_id: str, user_id: str) -> CircleMember | None:
        async with await self._session() as session:
            stmt = (
                select(CircleMember)
                .options(selectinload(CircleMember.user))
                .where(CircleMember.circle_id == space_id, CircleMember.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def add_member(self, data: dict[str, Any]) -> CircleMember:
        async with await self._session() as session:
            member = CircleMember(**self._map_member(data))
            session.add(member)
            await session.commit()
            await session.refresh(member)
            return member

    async def update_member(self, space_id: str, user_id: str, data: dict[str, Any]) -> CircleMember:
        async with await self._session() as session:
            stmt = (
                update(CircleMember)
                .where(CircleMember.circle_id == space_id, CircleMember.user_id == user_id)
                .values(**self._map_member(data))
            )
            await session.execute(stmt)
            await session.commit()
        return await self.find_member(space_id, user_id)

    async def remove_member(self, space_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(CircleMember).where(
                CircleMember.circle_id == space_id, CircleMember.user_id == user_id
            )
            await session.execute(stmt)
            await session.commit()

    async def count_members(self, space_id: str) -> int:
        async with await self._session() as session:
            stmt = select(func.count()).select_from(CircleMember).where(CircleMember.circle_id == space_id)
            return (await session.execute(stmt)).scalar() or 0

    async def count_plus_seats(self, space_id: str) -> int:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(CircleMember)
                .where(CircleMember.circle_id == space_id, CircleMember.seat_tier == "PLUS_SEAT")
            )
            return (await session.execute(stmt)).scalar() or 0

    async def list_plus_members(self, space_id: str) -> list[CircleMember]:
        async with await self._session() as session:
            stmt = (
                select(CircleMember)
                .options(selectinload(CircleMember.user))
                .where(CircleMember.circle_id == space_id, CircleMember.seat_tier == "PLUS_SEAT")
                .order_by(CircleMember.joined_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Invitations
    # -----------------------------------------------------------------------

    async def create_invite(self, data: dict[str, Any]) -> CircleInvite:
        async with await self._session() as session:
            invite = CircleInvite(**self._map_invite(data))
            session.add(invite)
            await session.commit()
            await session.refresh(invite)
            return invite

    async def find_invite(self, invite_id: str) -> CircleInvite | None:
        async with await self._session() as session:
            stmt = select(CircleInvite).where(CircleInvite.id == invite_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_pending_invites(self, space_id: str) -> list[CircleInvite]:
        async with await self._session() as session:
            stmt = (
                select(CircleInvite)
                .where(CircleInvite.circle_id == space_id, CircleInvite.status == "PENDING")
                .order_by(CircleInvite.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_user_invites(self, email: str) -> list[CircleInvite]:
        async with await self._session() as session:
            stmt = (
                select(CircleInvite)
                .where(CircleInvite.invitee_email == email, CircleInvite.status == "PENDING")
                .order_by(CircleInvite.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_invite(self, invite_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_invite(data)
            stmt = update(CircleInvite).where(CircleInvite.id == invite_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Chat Groups
    # -----------------------------------------------------------------------

    async def create_chat_group(self, data: dict[str, Any]) -> CircleChatGroup:
        async with await self._session() as session:
            group = CircleChatGroup(**self._map_chat_group(data))
            session.add(group)
            await session.commit()
            await session.refresh(group)
            return group

    async def find_chat_group(self, group_id: str) -> CircleChatGroup | None:
        async with await self._session() as session:
            stmt = select(CircleChatGroup).where(CircleChatGroup.id == group_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_chat_groups(self, space_id: str) -> list[CircleChatGroup]:
        async with await self._session() as session:
            stmt = select(CircleChatGroup).where(CircleChatGroup.circle_id == space_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_chat_group(self, group_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_chat_group(data)
            stmt = update(CircleChatGroup).where(CircleChatGroup.id == group_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_chat_group(self, group_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(CircleChatGroup).where(CircleChatGroup.id == group_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Sessions (CircleSession)
    # -----------------------------------------------------------------------

    async def create_session(self, data: dict[str, Any]) -> CircleSession:
        async with await self._session() as session:
            cs = CircleSession(**self._map_circle_session(data))
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            return cs

    async def find_session(self, session_id: str) -> CircleSession | None:
        async with await self._session() as session:
            stmt = select(CircleSession).where(CircleSession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_sessions(self, space_id: str) -> list[CircleSession]:
        async with await self._session() as session:
            stmt = (
                select(CircleSession)
                .where(CircleSession.circle_id == space_id)
                .order_by(CircleSession.scheduled_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_upcoming_sessions(self, space_id: str, *, limit: int = 10) -> list[CircleSession]:
        from datetime import UTC
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

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_circle_session(data)
            stmt = update(CircleSession).where(CircleSession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_session(self, session_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(CircleSession).where(CircleSession.id == session_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Seat Addons
    # -----------------------------------------------------------------------

    async def list_seat_addons(self, space_id: str) -> list[CircleSeatAddon]:
        async with await self._session() as session:
            stmt = (
                select(CircleSeatAddon)
                .where(CircleSeatAddon.circle_id == space_id)
                .order_by(CircleSeatAddon.purchased_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    _CIRCLE_MAP = {
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
        "circlePlanActive": "circle_plan_active",
        "circlePlanCurrentPeriodEnd": "circle_plan_current_period_end",
        "seatPoolSize": "seat_pool_size",
        "hiddenByModeration": "hidden_by_moderation",
        "allowMemberExport": "allow_member_export",
        "featured": "featured",
        "joinPolicy": "join_policy",
    }

    _MEMBER_MAP = {
        "circleId": "circle_id",
        "userId": "user_id",
        "role": "role",
        "seatTier": "seat_tier",
    }

    _INVITE_MAP = {
        "circleId": "circle_id",
        "inviterId": "inviter_id",
        "inviteeEmail": "invitee_email",
        "inviteeId": "invitee_id",
        "status": "status",
        "role": "role",
        "seatTier": "seat_tier",
        "expiresAt": "expires_at",
    }

    _CHAT_GROUP_MAP = {
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

    def _map_circle(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._CIRCLE_MAP.get(k, k): v for k, v in data.items() if k in self._CIRCLE_MAP}

    def _map_member(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._MEMBER_MAP.get(k, k): v for k, v in data.items() if k in self._MEMBER_MAP}

    def _map_invite(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._INVITE_MAP.get(k, k): v for k, v in data.items() if k in self._INVITE_MAP}

    def _map_chat_group(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._CHAT_GROUP_MAP.get(k, k): v for k, v in data.items() if k in self._CHAT_GROUP_MAP}

    def _map_circle_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return {self._SESSION_MAP.get(k, k): v for k, v in data.items() if k in self._SESSION_MAP}


# Singleton
space_repo = LearningSpaceRepository()
