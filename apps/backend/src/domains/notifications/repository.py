"""Persistence operations owned by the notification domain."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from src.config import get_settings
from src.shared.database import get_session_factory

from .db_models import (
    Notification,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationInteraction,
    NotificationPolicy,
    NotificationPreference,
    PushInstallation,
)


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
        plan_mobile_push: bool = False,
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
                            await session.execute(
                                update(NotificationDelivery)
                                .where(
                                    NotificationDelivery.notification_id == grouped.id,
                                    NotificationDelivery.status.in_(
                                        ["PLANNED", "QUEUED", "SENDING", "ACCEPTED"]
                                    ),
                                )
                                .values(
                                    status="CANCELLED",
                                    suppression_reason="NOTIFICATION_REPLACED",
                                )
                            )

                    row = Notification(**values)
                    session.add(row)
                    await session.flush()
                    if plan_mobile_push:
                        installations = list(
                            (
                                await session.execute(
                                    select(PushInstallation).where(
                                        PushInstallation.user_id == values["user_id"],
                                        PushInstallation.transport == "EXPO",
                                        PushInstallation.disabled_at.is_(None),
                                        PushInstallation.token.is_not(None),
                                    )
                                )
                            ).scalars()
                        )
                        for installation in installations:
                            session.add(
                                NotificationDelivery(
                                    notification_id=row.id,
                                    user_id=row.user_id,
                                    destination_id=installation.id,
                                    channel="MOBILE_PUSH",
                                    provider="EXPO",
                                    status="PLANNED",
                                    eligible_at=values["eligible_at"],
                                    next_attempt_at=values["eligible_at"],
                                    expires_at=values.get("expires_at"),
                                    max_attempts=get_settings().MOBILE_PUSH_MAX_ATTEMPTS,
                                )
                            )
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
                    delivery_id = values.get("delivery_id")
                    if delivery_id is not None:
                        authorized_delivery = await session.scalar(
                            select(NotificationDelivery.id).where(
                                NotificationDelivery.id == delivery_id,
                                NotificationDelivery.notification_id == notification_id,
                                NotificationDelivery.user_id == user_id,
                            )
                        )
                        if authorized_delivery is None:
                            # Do not reveal whether the supplied delivery exists or who owns it.
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

    async def list_installations(self, user_id: str) -> list[PushInstallation]:
        factory = get_session_factory()
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(PushInstallation)
                        .where(PushInstallation.user_id == user_id)
                        .order_by(PushInstallation.created_at.desc(), PushInstallation.id.desc())
                    )
                ).scalars()
            )

    async def upsert_mobile_installation(
        self, user_id: str, values: dict[str, Any]
    ) -> tuple[PushInstallation, str | None]:
        """Rotate/reassign one Expo address and its scoped revocation authority."""

        now = datetime.now(UTC)
        token = values["token"]
        installation_id = values["installation_id"]
        revocation_secret: str | None = None
        factory = get_session_factory()
        async with factory() as session, session.begin():
            # Serialize both address and stable installation identity, including
            # the no-row-yet case where row locks alone cannot protect us.
            for key in sorted((f"push-token:{token}", f"push-install:{installation_id}")):
                await session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
                )
            rows = list(
                (
                    await session.execute(
                        select(PushInstallation)
                        .where(
                            or_(
                                PushInstallation.token == token,
                                and_(
                                    PushInstallation.installation_id == installation_id,
                                    PushInstallation.transport == "EXPO",
                                ),
                            )
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            target = next(
                (
                    row
                    for row in rows
                    if row.user_id == user_id
                    and row.installation_id == installation_id
                    and row.transport == "EXPO"
                ),
                None,
            )
            previous_token = target.token if target is not None else None
            was_disabled = target is not None and target.disabled_at is not None
            authority_reassigned = any(
                row is not target
                and row.installation_id == installation_id
                and row.transport == "EXPO"
                for row in rows
            )
            for row in rows:
                if row is target:
                    continue
                row.token = None
                row.disabled_at = now
                row.revocation_secret_hash = None
            # Release the globally unique address before assigning it to the
            # current owner; explicit flush avoids UPDATE ordering ambiguity.
            await session.flush()
            if target is None:
                target = PushInstallation(
                    user_id=user_id,
                    installation_id=installation_id,
                    transport="EXPO",
                )
                session.add(target)
            if (
                target.revocation_secret_hash is None
                or previous_token != token
                or was_disabled
                or authority_reassigned
            ):
                revocation_secret = secrets.token_urlsafe(32)
                target.revocation_secret_hash = hashlib.sha256(
                    revocation_secret.encode("utf-8")
                ).hexdigest()
            target.platform = values["platform"]
            target.token = token
            target.app_version = values.get("app_version")
            target.device_locale = values.get("device_locale")
            target.timezone = values.get("timezone")
            target.permission_state = values.get("permission_state", "DEFAULT")
            target.last_seen_at = now
            target.last_registered_at = now
            target.disabled_at = (
                None
                if target.permission_state == "GRANTED"
                else (now if target.permission_state == "DENIED" else None)
            )
            target.failure_count = 0
            await session.flush()
            await session.refresh(target)
            return target, revocation_secret

    async def disable_installation(self, user_id: str, installation_id: str) -> bool:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(PushInstallation)
                .where(
                    PushInstallation.id == installation_id,
                    PushInstallation.user_id == user_id,
                )
                .values(disabled_at=datetime.now(UTC))
            )
            return bool(getattr(result, "rowcount", 0))

    async def revoke_installation(self, installation_id: str, revocation_secret: str) -> None:
        """Disable a matching installation without revealing whether it exists."""

        secret_hash = hashlib.sha256(revocation_secret.encode("utf-8")).hexdigest()
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(PushInstallation)
                .where(
                    PushInstallation.installation_id == installation_id,
                    PushInstallation.transport == "EXPO",
                    PushInstallation.revocation_secret_hash == secret_hash,
                )
                .values(disabled_at=datetime.now(UTC))
            )

    async def claim_due_deliveries(
        self, *, limit: int, now: datetime
    ) -> list[tuple[NotificationDelivery, Notification, PushInstallation]]:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            rows = list(
                (
                    await session.execute(
                        select(NotificationDelivery, Notification, PushInstallation)
                        .join(Notification, Notification.id == NotificationDelivery.notification_id)
                        .join(
                            PushInstallation,
                            PushInstallation.id == NotificationDelivery.destination_id,
                        )
                        .where(
                            NotificationDelivery.channel == "MOBILE_PUSH",
                            NotificationDelivery.provider == "EXPO",
                            NotificationDelivery.status.in_(["PLANNED", "QUEUED"]),
                            NotificationDelivery.eligible_at <= now,
                            or_(
                                NotificationDelivery.next_attempt_at.is_(None),
                                NotificationDelivery.next_attempt_at <= now,
                            ),
                            or_(
                                NotificationDelivery.expires_at.is_(None),
                                NotificationDelivery.expires_at > now,
                            ),
                            NotificationDelivery.attempt_count < NotificationDelivery.max_attempts,
                            Notification.read_at.is_(None),
                            Notification.dismissed_at.is_(None),
                            Notification.archived_at.is_(None),
                            Notification.status.notin_(["READ", "DISMISSED", "EXPIRED"]),
                            PushInstallation.disabled_at.is_(None),
                            PushInstallation.token.is_not(None),
                        )
                        .order_by(
                            NotificationDelivery.next_attempt_at.asc().nullsfirst(),
                            NotificationDelivery.created_at.asc(),
                        )
                        .with_for_update(of=NotificationDelivery, skip_locked=True)
                        .limit(limit)
                    )
                ).all()
            )
            for delivery, _notification, _installation in rows:
                delivery.status = "SENDING"
                delivery.attempt_count += 1
            await session.flush()
            return rows

    async def delivery_still_sendable(self, delivery_id: str) -> bool:
        """Recheck the authoritative item after claim and immediately before I/O."""

        factory = get_session_factory()
        async with factory() as session:
            value = await session.scalar(
                select(NotificationDelivery.id)
                .join(Notification, Notification.id == NotificationDelivery.notification_id)
                .where(
                    NotificationDelivery.id == delivery_id,
                    NotificationDelivery.status == "SENDING",
                    Notification.read_at.is_(None),
                    Notification.dismissed_at.is_(None),
                    Notification.archived_at.is_(None),
                    Notification.status.notin_(["READ", "DISMISSED", "EXPIRED"]),
                )
            )
            return value is not None

    @asynccontextmanager
    async def current_delivery_tokens(
        self, delivery_ids: list[str], *, now: datetime
    ) -> AsyncIterator[dict[str, str]]:
        """Lock and expose only currently authorized addresses through provider submission."""

        factory = get_session_factory()
        async with factory() as session, session.begin():
            rows = (
                await session.execute(
                    select(NotificationDelivery.id, PushInstallation.token)
                    .select_from(NotificationDelivery)
                    .join(Notification, Notification.id == NotificationDelivery.notification_id)
                    .join(
                        PushInstallation,
                        PushInstallation.id == NotificationDelivery.destination_id,
                    )
                    .where(
                        NotificationDelivery.id.in_(delivery_ids),
                        NotificationDelivery.status == "SENDING",
                        NotificationDelivery.channel == "MOBILE_PUSH",
                        NotificationDelivery.provider == "EXPO",
                        or_(
                            NotificationDelivery.expires_at.is_(None),
                            NotificationDelivery.expires_at > now,
                        ),
                        NotificationDelivery.user_id == Notification.user_id,
                        NotificationDelivery.user_id == PushInstallation.user_id,
                        Notification.read_at.is_(None),
                        Notification.dismissed_at.is_(None),
                        Notification.archived_at.is_(None),
                        Notification.status.notin_(["READ", "DISMISSED", "EXPIRED"]),
                        PushInstallation.transport == "EXPO",
                        PushInstallation.disabled_at.is_(None),
                        PushInstallation.token.is_not(None),
                        PushInstallation.token != "",
                        PushInstallation.permission_state.in_(["DEFAULT", "GRANTED"]),
                    )
                    .with_for_update(of=(NotificationDelivery, Notification, PushInstallation))
                )
            ).all()
            yield {delivery_id: token for delivery_id, token in rows if token}

    async def defer_delivery(self, delivery_id: str, *, next_attempt_at: datetime) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.id == delivery_id,
                    NotificationDelivery.status == "SENDING",
                )
                .values(
                    status="QUEUED",
                    next_attempt_at=next_attempt_at,
                    attempt_count=func.greatest(NotificationDelivery.attempt_count - 1, 0),
                )
            )

    async def suppress_delivery(self, delivery_id: str, reason: str) -> None:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.id == delivery_id,
                    NotificationDelivery.status == "SENDING",
                )
                .values(
                    status="SUPPRESSED",
                    suppression_reason=reason,
                    attempt_count=func.greatest(NotificationDelivery.attempt_count - 1, 0),
                )
            )

    async def record_ticket_result(
        self,
        delivery_id: str,
        *,
        requested_at: datetime,
        duration_ms: int,
        ticket_id: str | None,
        retryable: bool,
        error_code: str | None,
        error_detail: str | None,
        next_attempt_at: datetime | None,
        disable_destination: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            delivery = await session.scalar(
                select(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .with_for_update()
            )
            if delivery is None or delivery.status != "SENDING":
                return
            session.add(
                NotificationDeliveryAttempt(
                    delivery_id=delivery.id,
                    attempt_number=delivery.attempt_count,
                    requested_at=requested_at,
                    duration_ms=duration_ms,
                    retryable=retryable,
                    provider_message_id=ticket_id,
                    response_metadata={"outcome": "ACCEPTED" if ticket_id else "ERROR"},
                    error_code=error_code,
                    error_detail=(error_detail or "")[:500] or None,
                )
            )
            if ticket_id:
                delivery.status = "ACCEPTED"
                delivery.provider_message_id = ticket_id
                delivery.accepted_at = now
                delivery.next_attempt_at = next_attempt_at
                delivery.failure_code = None
                delivery.failure_detail = None
            elif (
                retryable
                and next_attempt_at is not None
                and (delivery.expires_at is None or next_attempt_at < delivery.expires_at)
                and delivery.attempt_count < delivery.max_attempts
            ):
                delivery.status = "QUEUED"
                delivery.next_attempt_at = next_attempt_at
                delivery.failure_code = error_code
                delivery.failure_detail = (error_detail or "")[:500] or None
            else:
                delivery.status = "FAILED"
                delivery.failed_at = now
                delivery.failure_code = error_code or "PROVIDER_ERROR"
                delivery.failure_detail = (error_detail or "")[:500] or None
            if disable_destination and delivery.destination_id:
                await session.execute(
                    update(PushInstallation)
                    .where(PushInstallation.id == delivery.destination_id)
                    .values(
                        disabled_at=now,
                        failure_count=PushInstallation.failure_count + 1,
                    )
                )

    async def accepted_for_receipts(
        self, *, limit: int, now: datetime
    ) -> list[NotificationDelivery]:
        factory = get_session_factory()
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        select(NotificationDelivery)
                        .where(
                            NotificationDelivery.provider == "EXPO",
                            NotificationDelivery.status == "ACCEPTED",
                            NotificationDelivery.provider_message_id.is_not(None),
                            or_(
                                NotificationDelivery.next_attempt_at.is_(None),
                                NotificationDelivery.next_attempt_at <= now,
                            ),
                        )
                        .order_by(NotificationDelivery.accepted_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )

    async def record_receipt(
        self,
        delivery_id: str,
        *,
        delivered: bool,
        retryable: bool = False,
        error_code: str | None = None,
        error_detail: str | None = None,
        next_attempt_at: datetime | None = None,
        disable_destination: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            delivery = await session.scalar(
                select(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .with_for_update()
            )
            if delivery is None or delivery.status != "ACCEPTED":
                return
            attempt = await session.scalar(
                select(NotificationDeliveryAttempt)
                .where(
                    NotificationDeliveryAttempt.delivery_id == delivery.id,
                    NotificationDeliveryAttempt.attempt_number == delivery.attempt_count,
                )
                .with_for_update()
            )
            if attempt is not None:
                attempt.provider_receipt_id = delivery.provider_message_id
                attempt.response_metadata = {"outcome": "DELIVERED" if delivered else "ERROR"}
                attempt.retryable = retryable
                attempt.error_code = error_code
                attempt.error_detail = (error_detail or "")[:500] or None
            if delivered:
                delivery.status = "DELIVERED"
                delivery.delivered_at = now
                delivery.next_attempt_at = None
            elif (
                retryable
                and next_attempt_at is not None
                and (delivery.expires_at is None or next_attempt_at < delivery.expires_at)
                and delivery.attempt_count < delivery.max_attempts
            ):
                delivery.status = "QUEUED"
                delivery.next_attempt_at = next_attempt_at
                delivery.failure_code = error_code
            else:
                delivery.status = "FAILED"
                delivery.failed_at = now
                delivery.failure_code = error_code or "RECEIPT_ERROR"
                delivery.failure_detail = (error_detail or "")[:500] or None
            if disable_destination and delivery.destination_id:
                await session.execute(
                    update(PushInstallation)
                    .where(PushInstallation.id == delivery.destination_id)
                    .values(
                        disabled_at=now,
                        failure_count=PushInstallation.failure_count + 1,
                    )
                )

    async def defer_missing_receipts(
        self, delivery_ids: list[str], *, next_attempt_at: datetime
    ) -> None:
        if not delivery_ids:
            return
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.id.in_(delivery_ids),
                    NotificationDelivery.status == "ACCEPTED",
                )
                .values(next_attempt_at=next_attempt_at)
            )

    async def expire_due_deliveries(self, *, now: datetime) -> int:
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.status.in_(["PLANNED", "QUEUED", "ACCEPTED"]),
                    NotificationDelivery.expires_at.is_not(None),
                    NotificationDelivery.expires_at <= now,
                )
                .values(status="EXPIRED", failed_at=now, failure_code="EXPIRED")
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def recover_stale_sending(self, *, stale_before: datetime) -> int:
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            rows = list(
                (
                    await session.execute(
                        select(NotificationDelivery)
                        .where(
                            NotificationDelivery.status == "SENDING",
                            NotificationDelivery.updated_at < stale_before,
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            for delivery in rows:
                if delivery.expires_at is not None and delivery.expires_at <= now:
                    delivery.status = "EXPIRED"
                    delivery.failed_at = now
                    delivery.failure_code = "EXPIRED"
                elif delivery.attempt_count >= delivery.max_attempts:
                    delivery.status = "FAILED"
                    delivery.failed_at = now
                    delivery.failure_code = "STALE_MAX_ATTEMPTS"
                else:
                    delivery.status = "QUEUED"
                    delivery.next_attempt_at = now
                    # A crashed provider request has unknown outcome. Keep the
                    # consumed attempt number to bound possible duplicate sends.
                    session.add(
                        NotificationDeliveryAttempt(
                            delivery_id=delivery.id,
                            attempt_number=delivery.attempt_count,
                            requested_at=delivery.updated_at,
                            retryable=True,
                            error_code="STALE_SENDING",
                            error_detail="Worker stopped before recording provider outcome",
                        )
                    )
            return len(rows)

    async def dispatch_policy(
        self, user_id: str, notification_type: str, category: str
    ) -> dict[str, Any]:
        from src.domains.identity.db_models import UserPreferences

        factory = get_session_factory()
        async with factory() as session:
            policy = await session.scalar(
                select(NotificationPolicy).where(NotificationPolicy.user_id == user_id)
            )
            legacy = await session.scalar(
                select(UserPreferences).where(UserPreferences.user_id == user_id)
            )
            override = await session.scalar(
                select(NotificationPreference)
                .where(
                    NotificationPreference.user_id == user_id,
                    NotificationPreference.channel == "MOBILE_PUSH",
                    or_(
                        NotificationPreference.notification_type == notification_type,
                        and_(
                            NotificationPreference.notification_type.is_(None),
                            NotificationPreference.category == category,
                        ),
                    ),
                )
                .order_by(NotificationPreference.notification_type.desc().nullslast())
                .limit(1)
            )
            return {"policy": policy, "legacy": legacy, "override": override}


notification_repo = NotificationRepository()
