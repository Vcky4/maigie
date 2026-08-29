"""Canonical notification orchestration and lifecycle operations."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from src.core.websocket import manager
from src.shared.time import (
    UNKNOWN_TIMEZONE,
    LearnerTimezone,
    ensure_utc,
    is_within_quiet_hours,
    local_day_bounds,
    next_end_of_quiet_hours,
    parse_hhmm,
    resolve_learner_timezone,
)

from .db_models import Notification, NotificationInteraction
from .models import NotificationInteractionCreate
from .repository import notification_repo
from .taxonomy import (
    canonical_action_payload,
    notification_spec,
    validate_action,
    validate_action_for_type,
)

logger = logging.getLogger(__name__)

PRIORITY_TIME_CRITICAL = 1
DEFAULT_MAX_DAILY = 5
MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 100


async def create_notification(
    *,
    user_id: str,
    type: str,
    title: str,
    body: str,
    action: dict[str, object],
    idempotency_key: str,
    priority: int = 5,
    action_data: dict | None = None,
    scheduled_at: datetime | None = None,
    source_domain: str | None = None,
    source_entity_type: str | None = None,
    source_entity_id: str | None = None,
    group_key: str | None = None,
) -> Notification:
    """Validate, schedule, deduplicate, and durably create one canonical item."""

    spec = notification_spec(type)
    canonical_action = canonical_action_payload(validate_action_for_type(type, action))
    if group_key is not None and not spec.groupable:
        raise ValueError(f"Notification type {type} is not groupable")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be empty")

    timezone_ = await _timezone_or_unknown(user_id)
    from src.domains.personal_learning.repository import personal_learning_repo

    profile = await personal_learning_repo.get_profile_by_user(user_id)
    moment = ensure_utc(scheduled_at) if scheduled_at else datetime.now(UTC)
    quiet_from = parse_hhmm(getattr(profile, "quiet_hours_start", None))
    quiet_to = parse_hhmm(getattr(profile, "quiet_hours_end", None))
    status = "PENDING"
    eligible_at = moment
    if is_within_quiet_hours(moment, timezone_, quiet_from, quiet_to):
        status = "QUEUED"
        eligible_at = next_end_of_quiet_hours(moment, timezone_, quiet_to)
    elif priority > PRIORITY_TIME_CRITICAL and await _allowance_spent(
        user_id, profile=profile, timezone_=timezone_, moment=moment
    ):
        status = "QUEUED"
        _, eligible_at = local_day_bounds(moment, timezone_)

    row, mutation, replaced_id = await notification_repo.create_canonical(
        {
            "user_id": user_id,
            "type": type,
            "title": title,
            "body": body,
            "priority": priority,
            "action_data": action_data,
            "scheduled_at": eligible_at,
            "status": status,
            "schema_version": 1,
            "category": spec.category,
            "urgency": spec.urgency,
            "action": canonical_action,
            "source_domain": source_domain or type.split(".", 1)[0],
            "source_entity_type": source_entity_type,
            "source_entity_id": source_entity_id,
            "idempotency_key": idempotency_key,
            "group_key": group_key,
            "eligible_at": eligible_at,
            "expires_at": eligible_at + spec.ttl if spec.ttl else None,
        },
        group_window=spec.dedupe_window if spec.groupable else None,
    )
    if mutation:
        if replaced_id is not None:
            await _emit_hint("notification.updated", user_id, replaced_id)
        await _emit_hint(f"notification.{mutation}", user_id, row.id)
        await _emit_unread_count(user_id)
    return row


async def list_history(
    *,
    user_id: str,
    limit: int,
    cursor: str | None,
    status: str,
    category: str | None,
) -> tuple[list[Notification], str | None, int]:
    if not MIN_HISTORY_LIMIT <= limit <= MAX_HISTORY_LIMIT:
        raise ValueError(f"limit must be between {MIN_HISTORY_LIMIT} and {MAX_HISTORY_LIMIT}")
    if status not in {"all", "unread", "read", "dismissed", "archived"}:
        raise ValueError("invalid notification status filter")
    decoded = _decode_cursor(cursor) if cursor else None
    rows, has_more = await notification_repo.list_history(
        user_id,
        limit=limit,
        cursor=decoded,
        status=status,
        category=category,
    )
    next_cursor = _encode_cursor(rows[-1]) if has_more and rows else None
    return rows, next_cursor, await notification_repo.unread_count(user_id)


async def legacy_unread(*, user_id: str) -> list[Notification]:
    return await notification_repo.list_legacy_unread(user_id)


async def unread_count(*, user_id: str) -> int:
    return await notification_repo.unread_count(user_id)


async def mark_read(*, user_id: str, notification_id: str) -> None:
    if await notification_repo.mark_read(user_id, notification_id):
        await _emit_hint("notification.updated", user_id, notification_id)
        await _emit_unread_count(user_id)


async def dismiss(*, user_id: str, notification_id: str) -> None:
    if await notification_repo.dismiss(user_id, notification_id):
        await _emit_hint("notification.updated", user_id, notification_id)
        await _emit_unread_count(user_id)


async def mark_all_read(*, user_id: str) -> tuple[int, int]:
    updated = await notification_repo.mark_all_read(user_id)
    count = await notification_repo.unread_count(user_id)
    if updated:
        await _emit_hint("notification.updated", user_id, None)
        await _emit_hint("unread_count.changed", user_id, None, unread_count=count)
    return updated, count


async def append_interaction(
    *, user_id: str, notification_id: str, request: NotificationInteractionCreate
) -> NotificationInteraction | None:
    action = None
    if request.action is not None:
        try:
            action = canonical_action_payload(validate_action(request.action))
        except (ValueError, PydanticValidationError) as exc:
            raise ValueError("invalid notification interaction action") from exc
    row, created = await notification_repo.append_interaction(
        user_id,
        notification_id,
        {
            "delivery_id": request.delivery_id,
            "idempotency_id": request.idempotency_id,
            "event": request.event,
            "surface": request.surface,
            "action": action,
            "source_metadata": request.source_metadata,
            "occurred_at": request.occurred_at or datetime.now(UTC),
        },
    )
    if created and row is not None:
        await _emit_hint("notification.updated", user_id, notification_id)
    return row


def _encode_cursor(row: Notification) -> str:
    payload = json.dumps(
        [ensure_utc(row.created_at).isoformat(), row.id], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError
        return ensure_utc(datetime.fromisoformat(value[0])), value[1]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid notification cursor") from exc


async def _emit_hint(
    event: str, user_id: str, notification_id: str | None, *, unread_count: int | None = None
) -> None:
    payload: dict[str, Any] = {"type": event}
    if notification_id is not None:
        payload["notificationId"] = notification_id
    if unread_count is not None:
        payload["unreadCount"] = unread_count
    try:
        await manager.send_to_user(user_id, payload)
    except Exception:
        logger.warning("Could not emit notification hint", exc_info=True)


async def _emit_unread_count(user_id: str) -> None:
    try:
        count = await notification_repo.unread_count(user_id)
        await _emit_hint("unread_count.changed", user_id, None, unread_count=count)
    except Exception:
        logger.warning("Could not emit unread-count hint", exc_info=True)


async def _allowance_spent(user_id: str, *, profile: Any, timezone_: Any, moment: datetime) -> bool:
    since, until = local_day_bounds(moment, timezone_)
    maximum = getattr(profile, "max_daily_notifications", None) or DEFAULT_MAX_DAILY
    return (
        await notification_repo.count_delivered_between(user_id, since=since, until=until)
        >= maximum
    )


async def _timezone_or_unknown(user_id: str) -> LearnerTimezone:
    try:
        return await resolve_learner_timezone(user_id)
    except Exception:
        logger.exception("Could not resolve learner timezone", extra={"user_id": user_id})
        return UNKNOWN_TIMEZONE
