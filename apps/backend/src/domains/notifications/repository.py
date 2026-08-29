"""Persistence operations owned by the notification domain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from src.shared.database import get_session_factory

from .db_models import Notification, NotificationInteraction, NotificationPolicy


def active_unread_predicate(now: datetime) -> ColumnElement[bool]:
    """One predicate for visibility, badges, read-all, and grouping."""

    return and_(
        Notification.eligible_at.is_not(None),
        Notification.eligible_at <= now,
        or_(Notification.expires_at.is_(None), Notification.expires_at > now),
        Notification.read_at.is_(None),
        Notification.dismissed_at.is_(None),
        Notification.archived_at.is_(None),
        Notification.status.notin_(["EXPIRED", "DISMISSED", "READ"]),
    )


class NotificationRepository:
    async def create_canonical(
        self,
        values: dict[str, Any],
        *,
        group_window: timedelta | None,
    ) -> tuple[Notification, str | None, str | None]:
        """Insert, replay, or replace one active grouped row atomically."""

        factory = get_session_factory()
        async with factory() as session:
            try:
                async with session.begin():
                    group_key = values.get("group_key")
                    if group_key and group_window is not None:
                        # Serialize even the empty-group case. A row lock cannot protect
                        # a group for which no row exists yet.
                        lock_key = f"{values['user_id']}:{group_key}"
                        await session.execute(
                            select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
                        )

                    existing = (
                        await session.execute(
                            select(Notification).where(
                                Notification.user_id == values["user_id"],
                                Notification.idempotency_key == values["idempotency_key"],
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        return existing, None, None

                    replaced_id: str | None = None
                    if group_key and group_window is not None:
                        now = datetime.now(UTC)
                        grouped = (
                            await session.execute(
                                select(Notification)
                                .where(
                                    Notification.user_id == values["user_id"],
                                    Notification.group_key == group_key,
                                    Notification.created_at >= now - group_window,
                                    active_unread_predicate(now),
                                )
                                .order_by(Notification.created_at.desc(), Notification.id.desc())
                                .with_for_update()
                                .limit(1)
                            )
                        ).scalar_one_or_none()
                        if grouped is not None:
                            # Preserve immutable evidence and every historical replay key;
                            # replacement means archive-and-append, never rewrite.
                            grouped.archived_at = now
                            replaced_id = grouped.id

                    row = Notification(**values)
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return row, "created", replaced_id
            except IntegrityError:
                await session.rollback()
                replay = (
                    await session.execute(
                        select(Notification).where(
                            Notification.user_id == values["user_id"],
                            Notification.idempotency_key == values["idempotency_key"],
                        )
                    )
                ).scalar_one()
                return replay, None, None

    async def list_history(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: tuple[datetime, str] | None,
        status: str,
        category: str | None,
    ) -> tuple[list[Notification], bool]:
        now = datetime.now(UTC)
        conditions = [Notification.user_id == user_id]
        if category:
            conditions.append(Notification.category == category)
        if status == "unread":
            conditions.append(active_unread_predicate(now))
        elif status == "read":
            conditions.append(Notification.read_at.is_not(None))
        elif status == "dismissed":
            conditions.append(Notification.dismissed_at.is_not(None))
        elif status == "archived":
            conditions.append(Notification.archived_at.is_not(None))
        if cursor:
            created_at, row_id = cursor
            conditions.append(
                or_(
                    Notification.created_at < created_at,
                    and_(Notification.created_at == created_at, Notification.id < row_id),
                )
            )

        factory = get_session_factory()
        async with factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(Notification)
                        .where(*conditions)
                        .order_by(Notification.created_at.desc(), Notification.id.desc())
                        .limit(limit + 1)
                    )
                ).scalars()
            )
        return rows[:limit], len(rows) > limit

    async def list_legacy_unread(self, user_id: str) -> list[Notification]:
        factory = get_session_factory()
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(Notification)
                        .where(
                            Notification.user_id == user_id,
                            Notification.status.notin_(["READ", "DISMISSED"]),
                        )
                        .order_by(Notification.priority.asc(), Notification.scheduled_at.asc())
                    )
                ).scalars()
            )

    async def unread_count(self, user_id: str) -> int:
        factory = get_session_factory()
        async with factory() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, active_unread_predicate(datetime.now(UTC)))
            )
            return int(value or 0)

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                )
                .values(read_at=now, status="READ")
            )
            return bool(getattr(result, "rowcount", 0))

    async def dismiss(self, user_id: str, notification_id: str) -> bool:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                    Notification.dismissed_at.is_(None),
                )
                .values(dismissed_at=now, status="DISMISSED")
            )
            return bool(getattr(result, "rowcount", 0))

    async def mark_all_read(self, user_id: str) -> int:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(Notification)
                .where(Notification.user_id == user_id, active_unread_predicate(now))
                .values(read_at=now, status="READ")
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def append_interaction(
        self, user_id: str, notification_id: str, values: dict[str, Any]
    ) -> tuple[NotificationInteraction | None, bool]:
        factory = get_session_factory()
        async with factory() as session:
            try:
                async with session.begin():
                    owned = await session.scalar(
                        select(Notification.id).where(
                            Notification.id == notification_id,
                            Notification.user_id == user_id,
                        )
                    )
                    if owned is None:
                        return None, False
                    existing = (
                        await session.execute(
                            select(NotificationInteraction).where(
                                NotificationInteraction.user_id == user_id,
                                NotificationInteraction.idempotency_id == values["idempotency_id"],
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        if existing.notification_id != notification_id:
                            raise ValueError(
                                "idempotencyId is already used for another notification"
                            )
                        return existing, False
                    row = NotificationInteraction(
                        notification_id=notification_id, user_id=user_id, **values
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return row, True
            except IntegrityError:
                await session.rollback()
                replay = (
                    await session.execute(
                        select(NotificationInteraction).where(
                            NotificationInteraction.user_id == user_id,
                            NotificationInteraction.idempotency_id == values["idempotency_id"],
                        )
                    )
                ).scalar_one_or_none()
                if replay is not None and replay.notification_id != notification_id:
                    raise ValueError("idempotencyId is already used for another notification")
                return replay, False

    async def count_delivered_between(
        self, user_id: str, *, since: datetime, until: datetime
    ) -> int:
        factory = get_session_factory()
        async with factory() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.delivered_at >= since,
                    Notification.delivered_at < until,
                )
            )
            return int(value or 0)

    async def ensure_policy(self, user_id: str) -> NotificationPolicy:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(NotificationPolicy).where(NotificationPolicy.user_id == user_id)
                )
                if row is None:
                    row = NotificationPolicy(user_id=user_id, engagement_enabled=False)
                    session.add(row)
                    try:
                        await session.flush()
                    except IntegrityError:
                        await session.rollback()
                        return await self.ensure_policy(user_id)
                return row


notification_repo = NotificationRepository()
