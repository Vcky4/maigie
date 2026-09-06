"""Persistence operations owned by the notification domain."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from src.config import get_settings
from src.shared.database import get_session_factory

from .db_models import (
    EmailProviderEvent,
    EmailSuppression,
    Notification,
    NotificationDecision,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationDigest,
    NotificationDigestItem,
    NotificationInteraction,
    NotificationPolicy,
    NotificationPreference,
    PushInstallation,
)
from .decision import NotificationDecisionRecord

#: Which `PushInstallation` column carries the provider address for each transport. Expo
#: addresses a device by opaque token; Web Push addresses a browser by endpoint URL. Both are
#: globally unique and both are enforced by a partial unique index, so one rotation routine
#: serves either — this map is the only place the difference lives.
_ADDRESS_FIELD: dict[str, str] = {"EXPO": "token", "WEB_PUSH": "endpoint"}


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


@dataclass(frozen=True)
class EmailPlan:
    """What the service resolved about emailing this notification, before it is planned.

    Passed in rather than resolved here so the address lookup and the consent decision stay
    in the service, where the taxonomy and preference rules already live.
    """

    address_ref: str
    provider: str
    max_attempts: int


class NotificationRepository:
    async def create_canonical(
        self,
        values: dict[str, Any],
        *,
        group_window: timedelta | None,
        plan_mobile_push: bool = False,
        plan_web_push: bool = False,
        plan_email: EmailPlan | None = None,
        decision_record: NotificationDecisionRecord | None = None,
    ) -> tuple[Notification, str | None, str | None]:
        """Insert, replay, or replace one active grouped row atomically.

        When a decision record is supplied it is written in the same transaction and linked from
        the notification, so the audit trail and the notification succeed or fail together. On an
        idempotent replay no decision is written — the original decision stands.
        """

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

                    if decision_record is not None:
                        # Written before the notification so its id can link the FK in the same
                        # insert. Only reached past the idempotency check above, so a replay never
                        # produces a second decision for the same logical event.
                        decision_row = NotificationDecision(
                            user_id=decision_record.user_id,
                            notification_type=decision_record.notification_type,
                            policy_version=decision_record.policy_version,
                            model_version=decision_record.model_version,
                            input_snapshot=decision_record.input_snapshot,
                            decision=decision_record.decision,
                            reason_codes=decision_record.reason_codes,
                            confidence=decision_record.confidence,
                            used_fallback=decision_record.used_fallback,
                            experiment_id=decision_record.experiment_id,
                        )
                        session.add(decision_row)
                        await session.flush()
                        values = {**values, "intelligence_decision_id": decision_row.id}

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
                    if plan_web_push:
                        # One delivery per subscribed browser, the same shape as mobile: a
                        # learner with a laptop and a desktop expects both to buzz, and each
                        # has its own endpoint, its own failures, and its own revocation.
                        subscriptions = list(
                            (
                                await session.execute(
                                    select(PushInstallation).where(
                                        PushInstallation.user_id == values["user_id"],
                                        PushInstallation.transport == "WEB_PUSH",
                                        PushInstallation.disabled_at.is_(None),
                                        PushInstallation.endpoint.is_not(None),
                                        PushInstallation.p256dh_encrypted.is_not(None),
                                        PushInstallation.auth_encrypted.is_not(None),
                                    )
                                )
                            ).scalars()
                        )
                        for subscription in subscriptions:
                            session.add(
                                NotificationDelivery(
                                    notification_id=row.id,
                                    user_id=row.user_id,
                                    destination_id=subscription.id,
                                    channel="WEB_PUSH",
                                    provider="WEB_PUSH",
                                    status="PLANNED",
                                    eligible_at=values["eligible_at"],
                                    next_attempt_at=values["eligible_at"],
                                    expires_at=values.get("expires_at"),
                                    max_attempts=get_settings().WEB_PUSH_MAX_ATTEMPTS,
                                )
                            )
                        await session.flush()
                    if plan_email is not None:
                        # One row per notification, guarded by the partial unique index on
                        # (notificationId, channel) for destination-less channels — a replayed
                        # planner cannot produce a second email.
                        session.add(
                            NotificationDelivery(
                                notification_id=row.id,
                                user_id=row.user_id,
                                destination_id=None,
                                destination_ref=plan_email.address_ref,
                                channel="EMAIL",
                                provider=plan_email.provider,
                                status="PLANNED",
                                eligible_at=values["eligible_at"],
                                next_attempt_at=values["eligible_at"],
                                expires_at=values.get("expires_at"),
                                max_attempts=plan_email.max_attempts,
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

    async def find_actionable_notification(
        self,
        user_id: str,
        *,
        source_entity_id: str,
        source_entity_type: str | None = None,
        within: timedelta,
    ) -> str | None:
        """The most recent notification this learner got about one entity, for attribution.

        When a learner does the thing a notification pointed at — completes the study block it
        reminded them of, answers the goal nudge — this maps that entity back to the notification
        so an `ACTIONED` outcome can be recorded against it. Bounded by `within` so an action taken
        long after is not credited to a stale notification; the per-type attribution window is
        applied again at read time, so this bound only needs to be the generous outer limit.

        Scoped to `user_id`, and returns only an id, so a caller cannot use it to read another
        learner's notification.
        """

        factory = get_session_factory()
        async with factory() as session:
            conditions = [
                Notification.user_id == user_id,
                Notification.source_entity_id == source_entity_id,
                Notification.created_at >= datetime.now(UTC) - within,
            ]
            if source_entity_type is not None:
                conditions.append(Notification.source_entity_type == source_entity_type)
            return await session.scalar(
                select(Notification.id)
                .where(*conditions)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .limit(1)
            )

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

    async def upsert_push_installation(
        self, user_id: str, values: dict[str, Any], *, transport: str
    ) -> tuple[PushInstallation, str | None]:
        """Rotate/reassign one push address and its scoped revocation authority.

        One routine serves Expo and Web Push because the problem is identical: a globally
        unique provider address may migrate between installations, accounts, and reinstalls,
        and whoever holds it now must be the only row that can be pushed to. The transports
        differ only in which column carries the address, so that is the parameter.
        """

        address_field = _ADDRESS_FIELD[transport]
        address_column = getattr(PushInstallation, address_field)
        now = datetime.now(UTC)
        address = values["address"]
        installation_id = values["installation_id"]
        revocation_secret: str | None = None
        factory = get_session_factory()
        async with factory() as session, session.begin():
            # Serialize both address and stable installation identity, including
            # the no-row-yet case where row locks alone cannot protect us.
            #
            # The `push-token:` label is historical and deliberately unchanged: an Expo token
            # and a Web Push endpoint URL can never be the same string, so one namespace is
            # safe, and keeping the label means a rolling deploy cannot leave old and new
            # workers locking different keys for the same installation.
            for key in sorted((f"push-token:{address}", f"push-install:{installation_id}")):
                await session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
                )
            rows = list(
                (
                    await session.execute(
                        select(PushInstallation)
                        .where(
                            or_(
                                address_column == address,
                                and_(
                                    PushInstallation.installation_id == installation_id,
                                    PushInstallation.transport == transport,
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
                    and row.transport == transport
                ),
                None,
            )
            previous_address = getattr(target, address_field) if target is not None else None
            was_disabled = target is not None and target.disabled_at is not None
            authority_reassigned = any(
                row is not target
                and row.installation_id == installation_id
                and row.transport == transport
                for row in rows
            )
            for row in rows:
                if row is target:
                    continue
                setattr(row, address_field, None)
                row.disabled_at = now
                row.revocation_secret_hash = None
            # Release the globally unique address before assigning it to the
            # current owner; explicit flush avoids UPDATE ordering ambiguity.
            await session.flush()
            if target is None:
                target = PushInstallation(
                    user_id=user_id,
                    installation_id=installation_id,
                    transport=transport,
                )
                session.add(target)
            if (
                target.revocation_secret_hash is None
                or previous_address != address
                or was_disabled
                or authority_reassigned
            ):
                revocation_secret = secrets.token_urlsafe(32)
                target.revocation_secret_hash = hashlib.sha256(
                    revocation_secret.encode("utf-8")
                ).hexdigest()
            target.platform = values["platform"]
            setattr(target, address_field, address)
            target.app_version = values.get("app_version")
            target.device_locale = values.get("device_locale")
            target.timezone = values.get("timezone")
            target.permission_state = values.get("permission_state", "DEFAULT")
            # Present only for Web Push, where the payload is encrypted to the browser's own
            # key material. Assigned unconditionally for that transport so a resubscribe that
            # produces fresh keys cannot leave the previous pair behind.
            if "p256dh_encrypted" in values:
                target.p256dh_encrypted = values["p256dh_encrypted"]
            if "auth_encrypted" in values:
                target.auth_encrypted = values["auth_encrypted"]
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

    async def disable_push_installation_by_address(
        self, user_id: str, *, transport: str, address: str
    ) -> bool:
        """Disable the caller's own installation named by its provider address.

        Web Push has no stable row id on the client: a browser knows its endpoint and nothing
        else, so unsubscribing has to be expressed that way. Scoped to `user_id` so an
        endpoint learned elsewhere cannot be used to silence another learner.
        """

        address_column = getattr(PushInstallation, _ADDRESS_FIELD[transport])
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(PushInstallation)
                .where(
                    PushInstallation.user_id == user_id,
                    PushInstallation.transport == transport,
                    address_column == address,
                    PushInstallation.disabled_at.is_(None),
                )
                .values(disabled_at=datetime.now(UTC))
            )
            return bool(getattr(result, "rowcount", 0))

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

    async def revoke_installation(
        self, installation_id: str, revocation_secret: str, *, transport: str = "EXPO"
    ) -> None:
        """Disable a matching installation without revealing whether it exists.

        The secret exists because a mobile logout may happen with no usable session — offline,
        or after the access token has already been discarded — so revocation authority has to
        travel with the installation rather than with the account. Web Push does not need it:
        a browser can only unsubscribe from a page that is already authenticated, which is why
        the web route uses `disable_push_installation_by_address` instead.
        """

        secret_hash = hashlib.sha256(revocation_secret.encode("utf-8")).hexdigest()
        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(PushInstallation)
                .where(
                    PushInstallation.installation_id == installation_id,
                    PushInstallation.transport == transport,
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

    async def claim_due_email_deliveries(
        self, *, limit: int, now: datetime
    ) -> list[tuple[NotificationDelivery, Notification]]:
        """Claim due EMAIL rows, marking them SENDING before any provider call.

        `skip_locked` lets two workers run without either waiting or double-sending: a row
        claimed by one is invisible to the other. The attempt counter is incremented here
        rather than after the send, so a process that dies mid-send has still consumed an
        attempt and cannot retry forever.
        """

        factory = get_session_factory()
        async with factory() as session, session.begin():
            rows = list(
                (
                    await session.execute(
                        select(NotificationDelivery, Notification)
                        .join(Notification, Notification.id == NotificationDelivery.notification_id)
                        .where(
                            NotificationDelivery.channel == "EMAIL",
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
                            # An item the learner has already dealt with in the app is not
                            # worth an email about.
                            Notification.read_at.is_(None),
                            Notification.dismissed_at.is_(None),
                            Notification.archived_at.is_(None),
                            Notification.status.notin_(["READ", "DISMISSED", "EXPIRED"]),
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
            for delivery, _notification in rows:
                delivery.status = "SENDING"
                delivery.attempt_count += 1
            await session.flush()
            return rows

    async def record_email_result(
        self,
        delivery_id: str,
        *,
        requested_at: datetime,
        duration_ms: int,
        accepted: bool,
        provider: str | None,
        provider_message_id: str | None,
        retryable: bool,
        error_code: str | None,
        error_detail: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        """Append the attempt and move the delivery to its next honest state.

        Acceptance is recorded as `ACCEPTED`, never `DELIVERED`: a provider taking the
        message means it will try, not that it arrived. Only a provider webhook could
        justify `DELIVERED`, and that is not built yet — so `acceptedAt` is set and
        `nextAttemptAt` is cleared, which is what tells the backlog metric this row is
        finished rather than waiting on a reconciler.
        """

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
                    provider_message_id=provider_message_id,
                    response_metadata={
                        "outcome": "ACCEPTED" if accepted else "ERROR",
                        "provider": provider,
                    },
                    error_code=error_code,
                    error_detail=(error_detail or "")[:500] or None,
                )
            )
            if accepted:
                delivery.status = "ACCEPTED"
                delivery.provider = provider or delivery.provider
                delivery.provider_message_id = provider_message_id
                delivery.accepted_at = now
                delivery.next_attempt_at = None
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

    async def claim_due_web_push_deliveries(
        self, *, limit: int, now: datetime
    ) -> list[tuple[NotificationDelivery, Notification, PushInstallation]]:
        """Claim due WEB_PUSH rows, marking them SENDING before any push service call.

        The installation is joined and returned rather than looked up afterwards, so a
        subscription revoked between claiming and sending is excluded by the same query that
        locked the delivery. Both encrypted key fields are required to be present: a row
        missing either cannot be encrypted to, and returning it would only produce a failed
        attempt on every run.
        """

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
                            NotificationDelivery.channel == "WEB_PUSH",
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
                            # Guards against a delivery pointing at another learner's
                            # installation, which would be the worst possible bug here.
                            NotificationDelivery.user_id == Notification.user_id,
                            NotificationDelivery.user_id == PushInstallation.user_id,
                            # An item already dealt with in the app is not worth interrupting for.
                            Notification.read_at.is_(None),
                            Notification.dismissed_at.is_(None),
                            Notification.archived_at.is_(None),
                            Notification.status.notin_(["READ", "DISMISSED", "EXPIRED"]),
                            PushInstallation.transport == "WEB_PUSH",
                            PushInstallation.disabled_at.is_(None),
                            PushInstallation.endpoint.is_not(None),
                            PushInstallation.p256dh_encrypted.is_not(None),
                            PushInstallation.auth_encrypted.is_not(None),
                            # A browser subscription only exists while permission is granted,
                            # so unlike mobile there is no DEFAULT state worth sending to.
                            PushInstallation.permission_state == "GRANTED",
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

    async def record_web_push_result(
        self,
        delivery_id: str,
        *,
        requested_at: datetime,
        duration_ms: int,
        accepted: bool,
        provider_message_id: str | None,
        retryable: bool,
        expired: bool,
        error_code: str | None,
        error_detail: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        """Append the attempt, settle the delivery, and prune a dead subscription.

        Deliberately separate from `record_ticket_result` and `record_email_result` despite the
        similar shape, because the three channels settle differently and collapsing them would
        mean encoding all three exceptions in one branchy function. Expo keeps `nextAttemptAt`
        after acceptance so the receipt reconciler revisits the row; email and web push clear it
        because acceptance is as far as either can see. Only web push prunes its destination:
        a push service answering 404 or 410 is stating that the subscription is gone, which is
        authoritative in a way no email bounce is.

        `ACCEPTED`, not `DELIVERED`. The push service took the message; whether the browser
        was awake to receive it is not observable from here.
        """

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
                    provider_message_id=provider_message_id,
                    response_metadata={
                        "outcome": "ACCEPTED" if accepted else "ERROR",
                        "provider": "WEB_PUSH",
                        "expired": expired,
                    },
                    error_code=error_code,
                    error_detail=(error_detail or "")[:500] or None,
                )
            )
            if accepted:
                delivery.status = "ACCEPTED"
                delivery.provider_message_id = provider_message_id
                delivery.accepted_at = now
                delivery.next_attempt_at = None
                delivery.failure_code = None
                delivery.failure_detail = None
            elif (
                retryable
                and not expired
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
                delivery.next_attempt_at = None
                delivery.failure_code = error_code or "WEB_PUSH_ERROR"
                delivery.failure_detail = (error_detail or "")[:500] or None
            if expired and delivery.destination_id:
                # Disabled, not deleted: the row is evidence that this browser was subscribed,
                # and the learner resubscribing reuses it through `upsert_push_installation`.
                # The endpoint is released so the globally unique index cannot block a new
                # subscription that the push service hands out with the same URL.
                await session.execute(
                    update(PushInstallation)
                    .where(PushInstallation.id == delivery.destination_id)
                    .values(
                        disabled_at=now,
                        endpoint=None,
                        p256dh_encrypted=None,
                        auth_encrypted=None,
                        failure_count=PushInstallation.failure_count + 1,
                    )
                )

    async def digest_subscriptions(self, *, limit: int) -> list[dict[str, Any]]:
        """Every learner/category pair whose email preference asks for a digest."""

        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        NotificationPreference.user_id,
                        NotificationPreference.category,
                        NotificationPreference.digest_period,
                        NotificationPolicy,
                    )
                    .join(
                        NotificationPolicy,
                        NotificationPolicy.user_id == NotificationPreference.user_id,
                    )
                    .where(
                        NotificationPreference.channel == "EMAIL",
                        NotificationPreference.enabled.is_(True),
                        NotificationPreference.frequency == "DIGEST",
                        NotificationPreference.digest_period.is_not(None),
                        # A category row, not an exact-type override: a digest is a
                        # category-level promise.
                        NotificationPreference.notification_type.is_(None),
                        # The master gate the dispatcher applies, so a learner with engagement off
                        # never has a digest built for them. The legacy `UserPreferences.notifications`
                        # column this used to also check was retired once proven identical to it.
                        NotificationPolicy.engagement_enabled.is_(True),
                    )
                    .limit(limit)
                )
            ).all()

        # Database categories collapse into the settings category the learner actually chose,
        # so SOCIAL and CLASSROOM produce one digest rather than two.
        settings_category_of = {
            "LEARNING": "LEARNING",
            "PROGRESS": "PROGRESS",
            "SOCIAL": "SOCIAL_CLASSROOM",
            "CLASSROOM": "SOCIAL_CLASSROOM",
        }
        seen: set[tuple[str, str, str]] = set()
        subscriptions: list[dict[str, Any]] = []
        for user_id, category, digest_period, policy in rows:
            settings_category = settings_category_of.get(category)
            if settings_category is None:
                continue
            key = (user_id, settings_category, digest_period)
            if key in seen:
                continue
            seen.add(key)
            subscriptions.append(
                {
                    "user_id": user_id,
                    "settings_category": settings_category,
                    "digest_period": digest_period,
                    "policy": policy,
                }
            )
        return subscriptions

    async def digestible_notifications(
        self,
        user_id: str,
        *,
        categories: tuple[str, ...],
        since: datetime,
        until: datetime,
        email_allowed_types: list[str],
    ) -> list[dict[str, Any]]:
        """Notifications from the period that belong in a digest and are not already in one.

        Excludes anything the learner has already read, dismissed, or archived: a digest exists
        to catch what they missed, and repeating what they already dealt with in the app is how
        a summary becomes noise.
        """

        if not email_allowed_types:
            return []
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        Notification.id,
                        Notification.title,
                        Notification.body,
                        Notification.created_at,
                    )
                    .outerjoin(
                        NotificationDigestItem,
                        NotificationDigestItem.notification_id == Notification.id,
                    )
                    .where(
                        Notification.user_id == user_id,
                        Notification.category.in_(categories),
                        Notification.type.in_(email_allowed_types),
                        Notification.created_at >= since,
                        Notification.created_at < until,
                        Notification.read_at.is_(None),
                        Notification.dismissed_at.is_(None),
                        Notification.archived_at.is_(None),
                        NotificationDigestItem.id.is_(None),
                    )
                    .order_by(Notification.created_at.asc())
                )
            ).all()
        return [{"id": row.id, "title": row.title, "body": row.body or ""} for row in rows]

    async def claim_digest(
        self,
        *,
        user_id: str,
        category: str,
        period: str,
        period_start: datetime,
        period_end: datetime,
        notification_ids: list[str],
    ) -> dict[str, Any] | None:
        """Create the digest run and claim its items, or return ``None`` if already claimed.

        Both unique constraints do real work here and neither is redundant. The one on the run
        stops a second hourly pass summarising a period twice. The global one on the item stops a
        notification created near a boundary from appearing in two periods — and because both are
        claimed in one transaction, a concurrent run either wins the whole digest or none of it.
        """

        factory = get_session_factory()
        async with factory() as session:
            try:
                async with session.begin():
                    digest = NotificationDigest(
                        user_id=user_id,
                        category=category,
                        period=period,
                        period_start=period_start,
                        period_end=period_end,
                        item_count=len(notification_ids),
                    )
                    session.add(digest)
                    await session.flush()
                    for notification_id in notification_ids:
                        session.add(
                            NotificationDigestItem(
                                digest_id=digest.id, notification_id=notification_id
                            )
                        )
                    await session.flush()
                    return {"id": digest.id, "itemCount": digest.item_count}
            except IntegrityError:
                await session.rollback()
                return None

    async def attach_digest_notification(self, digest_id: str, notification_id: str) -> None:
        """Link the digest run to the notification that carries it."""

        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(NotificationDigest)
                .where(NotificationDigest.id == digest_id)
                .values(notification_id=notification_id)
            )

    async def record_email_provider_event(
        self,
        *,
        provider: str,
        provider_event_id: str,
        event_type: str,
        provider_message_id: str | None,
        address_hash: str | None,
        occurred_at: datetime,
        outcome: str,
    ) -> bool:
        """Record one provider event. Returns ``False`` if it was already ingested.

        The unique constraint on `(provider, providerEventId)` is what makes replay safe, so a
        retried webhook returns ``False`` here and the caller skips the side effects rather
        than applying them a second time.
        """

        factory = get_session_factory()
        async with factory() as session:
            try:
                async with session.begin():
                    delivery_id = None
                    if provider_message_id:
                        delivery_id = await session.scalar(
                            select(NotificationDelivery.id).where(
                                NotificationDelivery.provider_message_id == provider_message_id,
                                NotificationDelivery.channel == "EMAIL",
                            )
                        )
                    session.add(
                        EmailProviderEvent(
                            provider=provider,
                            provider_event_id=provider_event_id,
                            event_type=event_type[:48],
                            provider_message_id=provider_message_id,
                            address_hash=address_hash,
                            delivery_id=delivery_id,
                            occurred_at=occurred_at,
                            outcome=outcome[:32],
                        )
                    )
                    await session.flush()
                    return True
            except IntegrityError:
                await session.rollback()
                return False

    async def mark_email_delivered(
        self, *, provider_message_id: str, delivered_at: datetime
    ) -> bool:
        """Promote an accepted email to `DELIVERED` on the provider's word.

        Only `ACCEPTED` may become `DELIVERED`. A late `delivered` for a row already marked
        `FAILED` by a bounce must not overwrite that: the bounce is the outcome the learner
        experienced, and providers do not promise event ordering.
        """

        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.provider_message_id == provider_message_id,
                    NotificationDelivery.channel == "EMAIL",
                    NotificationDelivery.status == "ACCEPTED",
                )
                .values(status="DELIVERED", delivered_at=delivered_at, next_attempt_at=None)
            )
            return bool(getattr(result, "rowcount", 0))

    async def mark_email_failed(
        self, *, provider_message_id: str, failure_code: str, failed_at: datetime
    ) -> bool:
        """Record a bounce or complaint against the delivery it belongs to.

        A bounce can arrive after the provider accepted *and* reported delivery, so `DELIVERED`
        is also a valid state to leave — the message reached the provider's next hop and was
        then rejected, and the ledger should say so rather than keep the rosier status.
        """

        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.provider_message_id == provider_message_id,
                    NotificationDelivery.channel == "EMAIL",
                    NotificationDelivery.status.in_(["ACCEPTED", "DELIVERED"]),
                )
                .values(
                    status="FAILED",
                    failed_at=failed_at,
                    failure_code=failure_code,
                    next_attempt_at=None,
                )
            )
            return bool(getattr(result, "rowcount", 0))

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
                    or_(
                        NotificationDelivery.status.in_(["PLANNED", "QUEUED"]),
                        # `ACCEPTED` is expirable only where a receipt reconciler can still
                        # change it — that is Expo. An accepted email is terminal, and
                        # relabelling it `EXPIRED` would turn a message we did send into a
                        # failure in the ledger.
                        and_(
                            NotificationDelivery.status == "ACCEPTED",
                            NotificationDelivery.provider == "EXPO",
                        ),
                    ),
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

    async def notification_settings_snapshot(self, user_id: str) -> dict[str, Any]:
        """Load normalized policy/preferences and legacy compatibility sources."""

        from src.domains.identity.db_models import UserPreferences
        from src.domains.personal_learning.db_models import LearningProfile

        factory = get_session_factory()
        async with factory() as session:
            policy = await session.scalar(
                select(NotificationPolicy).where(NotificationPolicy.user_id == user_id)
            )
            preferences = list(
                (
                    await session.execute(
                        select(NotificationPreference).where(
                            NotificationPreference.user_id == user_id
                        )
                    )
                ).scalars()
            )
            legacy = await session.scalar(
                select(UserPreferences).where(UserPreferences.user_id == user_id)
            )
            profile = await session.scalar(
                select(LearningProfile).where(LearningProfile.user_id == user_id)
            )
            return {
                "policy": policy,
                "preferences": preferences,
                "legacy": legacy,
                "profile": profile,
            }

    async def update_notification_settings(
        self,
        user_id: str,
        *,
        policy_values: dict[str, Any],
        preferences: list[dict[str, Any]],
    ) -> None:
        """Atomically update the normalized policy, preferences, and mirrored profile fields."""

        from src.domains.personal_learning.db_models import LearningProfile

        factory = get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"notification-settings:{user_id}", 0)
                    )
                )
            )
            policy = await session.scalar(
                select(NotificationPolicy)
                .where(NotificationPolicy.user_id == user_id)
                .with_for_update()
            )
            if policy is None:
                policy = NotificationPolicy(user_id=user_id)
                session.add(policy)
            for key, value in policy_values.items():
                setattr(policy, key, value)

            profile = await session.scalar(
                select(LearningProfile).where(LearningProfile.user_id == user_id).with_for_update()
            )
            if profile is None:
                profile = LearningProfile(user_id=user_id)
                session.add(profile)
            profile.quiet_hours_start = policy_values["quiet_hours_start"]
            profile.quiet_hours_end = policy_values["quiet_hours_end"]
            profile.max_daily_notifications = policy_values["max_daily_notifications"]

            existing = list(
                (
                    await session.execute(
                        select(NotificationPreference)
                        .where(NotificationPreference.user_id == user_id)
                        .with_for_update()
                    )
                ).scalars()
            )
            category_rows = {
                (row.category, row.channel): row
                for row in existing
                if row.notification_type is None
            }
            for values in preferences:
                key = (values["category"], values["channel"])
                row = category_rows.get(key)
                if row is None:
                    row = NotificationPreference(
                        user_id=user_id,
                        category=values["category"],
                        notification_type=None,
                        channel=values["channel"],
                        enabled=values["enabled"],
                        frequency=values["frequency"],
                        digest_period=values["digest_period"],
                    )
                    session.add(row)
                    category_rows[key] = row
                else:
                    row.enabled = values["enabled"]
                    row.frequency = values["frequency"]
                    row.digest_period = values["digest_period"]

                # The UI is category-level. Keep previously migrated exact overrides
                # in the selected category aligned so they cannot outrank the new choice.
                for exact in existing:
                    if (
                        exact.notification_type is not None
                        and exact.category == values["category"]
                        and exact.channel == values["channel"]
                    ):
                        exact.enabled = values["enabled"]
                        exact.frequency = values["frequency"]
                        exact.digest_period = values["digest_period"]

            await session.flush()

    async def lifecycle_metrics(self, *, now: datetime) -> dict[str, Any]:
        """Return database-backed, low-cardinality operational lifecycle metrics."""

        stale_before = now - timedelta(seconds=get_settings().MOBILE_PUSH_STALE_SENDING_SECONDS)
        actionable_at = case(
            (
                NotificationDelivery.status == "SENDING",
                NotificationDelivery.updated_at,
            ),
            (
                NotificationDelivery.status == "ACCEPTED",
                func.coalesce(
                    NotificationDelivery.next_attempt_at,
                    NotificationDelivery.accepted_at,
                    NotificationDelivery.eligible_at,
                ),
            ),
            else_=func.coalesce(
                NotificationDelivery.next_attempt_at,
                NotificationDelivery.eligible_at,
            ),
        )
        actionable = or_(
            and_(
                NotificationDelivery.status.in_(["PLANNED", "QUEUED"]),
                NotificationDelivery.eligible_at <= now,
                or_(
                    NotificationDelivery.next_attempt_at.is_(None),
                    NotificationDelivery.next_attempt_at <= now,
                ),
            ),
            and_(
                NotificationDelivery.status == "ACCEPTED",
                # Only awaiting-receipt rows are outstanding work. Email has no receipt
                # reconciler, so an accepted email is finished; counting it as backlog
                # would leave a number that only ever grows and means nothing.
                NotificationDelivery.provider == "EXPO",
                or_(
                    NotificationDelivery.next_attempt_at.is_(None),
                    NotificationDelivery.next_attempt_at <= now,
                ),
            ),
            and_(
                NotificationDelivery.status == "SENDING",
                NotificationDelivery.updated_at <= stale_before,
            ),
        )

        factory = get_session_factory()
        async with factory() as session:
            actionable_rows = (
                await session.execute(
                    select(
                        NotificationDelivery.channel,
                        NotificationDelivery.status,
                        func.count(NotificationDelivery.id),
                        func.min(actionable_at),
                    )
                    .where(
                        actionable,
                        or_(
                            NotificationDelivery.expires_at.is_(None),
                            NotificationDelivery.expires_at > now,
                        ),
                    )
                    .group_by(NotificationDelivery.channel, NotificationDelivery.status)
                    .order_by(NotificationDelivery.channel, NotificationDelivery.status)
                )
            ).all()
            failure_rows = (
                await session.execute(
                    select(
                        NotificationDelivery.channel,
                        NotificationDelivery.failure_code,
                        func.count(NotificationDelivery.id),
                    )
                    .where(
                        NotificationDelivery.status == "FAILED",
                        NotificationDelivery.updated_at >= now - timedelta(hours=24),
                    )
                    .group_by(
                        NotificationDelivery.channel,
                        NotificationDelivery.failure_code,
                    )
                    .order_by(NotificationDelivery.channel, func.count().desc())
                )
            ).all()
            interaction_rows = (
                await session.execute(
                    select(
                        NotificationInteraction.surface,
                        NotificationInteraction.event,
                        func.count(NotificationInteraction.id),
                    )
                    .where(NotificationInteraction.occurred_at >= now - timedelta(hours=24))
                    .group_by(NotificationInteraction.surface, NotificationInteraction.event)
                    .order_by(NotificationInteraction.surface, NotificationInteraction.event)
                )
            ).all()

        return {
            "generatedAt": now,
            "actionableDeliveries": [
                {
                    "channel": channel,
                    "status": status,
                    "count": count,
                    "oldestActionableAt": oldest_actionable_at,
                }
                for channel, status, count, oldest_actionable_at in actionable_rows
            ],
            "failuresLast24Hours": [
                {"channel": channel, "failureCode": failure_code, "count": count}
                for channel, failure_code, count in failure_rows
            ],
            "interactionsLast24Hours": [
                {"surface": surface, "event": event, "count": count}
                for surface, event, count in interaction_rows
            ],
        }

    async def decision_metrics(self, *, since: datetime, until: datetime) -> dict[str, Any]:
        """How the decision engine behaved over a window, for the control dashboard.

        The point of the baseline is to be a measurable control, so this reports what it decided
        and how often it fell back, plus — once a proposer exists — how often a proposal diverged
        from it. All low-cardinality: counts by policy version, by mode, by reason code, never per
        learner or per notification.
        """

        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        NotificationDecision.policy_version,
                        NotificationDecision.used_fallback,
                        NotificationDecision.reason_codes,
                    ).where(
                        NotificationDecision.created_at >= since,
                        NotificationDecision.created_at < until,
                    )
                )
            ).all()

        total = len(rows)
        fallbacks = 0
        by_policy: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        shadow = 0
        shadow_with_divergence = 0
        for policy_version, used_fallback, reason_codes in rows:
            by_policy[policy_version] = by_policy.get(policy_version, 0) + 1
            if used_fallback:
                fallbacks += 1
            codes = reason_codes or []
            is_shadow = "MODE_SHADOW" in codes
            if is_shadow:
                shadow += 1
                if any(c.startswith("DIVERGE_") for c in codes):
                    shadow_with_divergence += 1
            for code in codes:
                if code.startswith("MODE_"):
                    by_mode[code] = by_mode.get(code, 0) + 1
                else:
                    by_reason[code] = by_reason.get(code, 0) + 1

        return {
            "windowHours": round((until - since).total_seconds() / 3600, 1),
            "decisions": total,
            "usedFallback": fallbacks,
            "byPolicyVersion": [
                {"policyVersion": pv, "count": c} for pv, c in sorted(by_policy.items())
            ],
            "byMode": [{"mode": m, "count": c} for m, c in sorted(by_mode.items())],
            "byReasonCode": [
                {"reasonCode": code, "count": c}
                for code, c in sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            # Meaningful only once a proposer runs; reported so shadow evaluation is visible the
            # moment it begins rather than needing a new metric then.
            "shadow": {"decisions": shadow, "withDivergence": shadow_with_divergence},
        }

    async def outcome_attribution(
        self, *, since: datetime, until: datetime
    ) -> list[dict[str, Any]]:
        """Meaningful-action-per-interruption, per notification type, over a window.

        This is the exit-criterion measure for the deterministic baseline: did an interruption
        lead to the outcome the type is for. It is computed here rather than in one SQL statement
        because "success" is per-type — a different event set and a different attribution window
        for a reminder than for a digest — and that policy lives in the taxonomy, not the schema.

        An *interruption* is a notification that left on an interruptive channel (push or email);
        an in-app record is not an interruption and is not counted, per the plan. The funnel is
        reported whole — interruptions, opened/clicked, meaningful action, dismissed, no response —
        so the gap between "opened" and "acted" stays visible rather than being collapsed into one
        rate that hides it.
        """

        factory = get_session_factory()
        async with factory() as session:
            # One row per (notification, interaction) for interruptive notifications created in the
            # window, plus the notification's own facts. Bounded by the window and by requiring an
            # interruptive delivery, so it stays a staff-scale query, not a per-request one.
            rows = (
                await session.execute(
                    select(
                        Notification.id,
                        Notification.type,
                        Notification.eligible_at,
                        NotificationInteraction.event,
                        NotificationInteraction.occurred_at,
                    )
                    .join(
                        NotificationDelivery,
                        NotificationDelivery.notification_id == Notification.id,
                    )
                    .outerjoin(
                        NotificationInteraction,
                        NotificationInteraction.notification_id == Notification.id,
                    )
                    .where(
                        Notification.created_at >= since,
                        Notification.created_at < until,
                        NotificationDelivery.channel.in_(["MOBILE_PUSH", "WEB_PUSH", "EMAIL"]),
                        NotificationDelivery.status.in_(["SENDING", "ACCEPTED", "DELIVERED"]),
                    )
                )
            ).all()

        # Fold the flat rows into one bucket per notification, then attribute per type in Python
        # where the per-type success set and window live.
        from .taxonomy import NOTIFICATION_SPECS

        per_notification: dict[str, dict[str, Any]] = {}
        for notification_id, type_, eligible_at, event, occurred_at in rows:
            bucket = per_notification.setdefault(
                notification_id,
                {"type": type_, "eligible_at": eligible_at, "events": []},
            )
            if event is not None:
                bucket["events"].append((event, occurred_at))

        summary: dict[str, dict[str, int]] = {}
        for bucket in per_notification.values():
            spec = NOTIFICATION_SPECS.get(bucket["type"])
            if spec is None:
                continue
            stats = summary.setdefault(
                bucket["type"],
                {"interruptions": 0, "opened": 0, "acted": 0, "dismissed": 0, "noResponse": 0},
            )
            stats["interruptions"] += 1
            events = bucket["events"]
            opened = any(e in ("OPENED", "CLICKED", "READ", "SEEN") for e, _ in events)
            acted = any(
                spec.counts_as_success(e, delivered_at=bucket["eligible_at"], occurred_at=at)
                for e, at in events
            )
            dismissed = any(e in ("DISMISSED", "UNSUBSCRIBED", "DECLINED") for e, _ in events)
            if opened:
                stats["opened"] += 1
            if acted:
                stats["acted"] += 1
            if dismissed:
                stats["dismissed"] += 1
            if not events:
                stats["noResponse"] += 1

        return [
            {
                "notificationType": type_,
                "interruptions": s["interruptions"],
                "opened": s["opened"],
                "meaningfulActions": s["acted"],
                "dismissed": s["dismissed"],
                "noResponse": s["noResponse"],
                # The exit-criterion number, guarded against divide-by-zero.
                "actionPerInterruption": (
                    round(s["acted"] / s["interruptions"], 4) if s["interruptions"] else 0.0
                ),
            }
            for type_, s in sorted(summary.items())
        ]

    async def is_address_suppressed(self, address_hash: str) -> str | None:
        """Return the active suppression reason for an address, or ``None``."""

        factory = get_session_factory()
        async with factory() as session:
            return await session.scalar(
                select(EmailSuppression.reason).where(
                    EmailSuppression.address_hash == address_hash,
                    EmailSuppression.released_at.is_(None),
                )
            )

    async def suppress_address(
        self,
        address_hash: str,
        *,
        reason: str,
        provider: str | None = None,
        provider_event_id: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """Record an active suppression, or leave an existing one alone.

        Returns whether this call created the suppression. Idempotent by the partial unique
        index, so a retried webhook cannot stack duplicate rows for one address.
        """

        factory = get_session_factory()
        async with factory() as session:
            try:
                async with session.begin():
                    existing = await session.scalar(
                        select(EmailSuppression.id).where(
                            EmailSuppression.address_hash == address_hash,
                            EmailSuppression.released_at.is_(None),
                        )
                    )
                    if existing is not None:
                        return False
                    session.add(
                        EmailSuppression(
                            address_hash=address_hash,
                            reason=reason,
                            provider=provider,
                            provider_event_id=provider_event_id,
                            detail=(detail or "")[:500] or None,
                        )
                    )
                    await session.flush()
                    return True
            except IntegrityError:
                # Another worker inserted it between the check and the flush, which is the
                # outcome we wanted anyway.
                await session.rollback()
                return False

    async def release_address_suppression(self, address_hash: str) -> bool:
        """Lift an active suppression, keeping the row as history."""

        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(EmailSuppression)
                .where(
                    EmailSuppression.address_hash == address_hash,
                    EmailSuppression.released_at.is_(None),
                )
                .values(released_at=now)
            )
            return bool(getattr(result, "rowcount", 0))

    async def email_recipient(self, user_id: str) -> tuple[str, str | None] | None:
        """The address to email and the name to greet, or ``None`` when unusable.

        An inactive or deleted account is not emailed. Unverified addresses are allowed
        because the verification mail itself is transactional and does not come through here.
        """

        from src.domains.identity.db_models import User

        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    select(User.email, User.name, User.is_active).where(User.id == user_id)
                )
            ).first()
        if row is None or not row.is_active or not (row.email or "").strip():
            return None
        return row.email.strip(), row.name

    async def channel_policy(
        self, user_id: str, notification_type: str, category: str, channel: str
    ) -> dict[str, Any]:
        """Engagement state plus the most specific preference row for one channel.

        The exact-type override outranks the category row, which is why the ordering puts
        non-null `notificationType` first rather than relying on insertion order.
        """

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
                    NotificationPreference.channel == channel,
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
