"""Canonical notification orchestration and lifecycle operations."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from src.config import get_settings
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
from .email_delivery import address_reference
from .feature_flags import capability_enabled_for
from .models import (
    MobilePushInstallationUpsert,
    NotificationCategorySetting,
    NotificationInteractionCreate,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
)
from .repository import EmailPlan, notification_repo
from .taxonomy import (
    canonical_action_payload,
    notification_spec,
    validate_action,
    validate_action_for_type,
)
from .unsubscribe import parse_unsubscribe_token

logger = logging.getLogger(__name__)

PRIORITY_TIME_CRITICAL = 1
DEFAULT_MAX_DAILY = 5
MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 100

_SETTINGS_CATEGORIES: dict[str, tuple[str, ...]] = {
    "LEARNING": ("LEARNING",),
    "PROGRESS": ("PROGRESS",),
    "SOCIAL_CLASSROOM": ("SOCIAL", "CLASSROOM"),
    "PRODUCT_UPDATES": ("OPERATIONS",),
}
_DEFAULT_IN_APP = {"LEARNING": True, "PROGRESS": True}


def _effective_category_setting(
    key: str, preferences: list[Any], legacy: Any
) -> NotificationCategorySetting:
    categories = _SETTINGS_CATEGORIES[key]

    def rows(channel: str, *, exact: bool) -> list[Any]:
        return [
            row
            for row in preferences
            if row.category in categories
            and row.channel == channel
            and (row.notification_type is not None) is exact
        ]

    def enabled(channel: str, default: bool = False) -> bool:
        category_rows = rows(channel, exact=False)
        if category_rows:
            return all(row.enabled and row.frequency == "IMMEDIATE" for row in category_rows)
        exact_rows = rows(channel, exact=True)
        if exact_rows:
            return any(row.enabled and row.frequency == "IMMEDIATE" for row in exact_rows)
        return default

    email_rows = rows("EMAIL", exact=False) or rows("EMAIL", exact=True)
    if any(row.enabled and row.frequency == "DIGEST" for row in email_rows):
        email_frequency = "WEEKLY"
    elif any(row.enabled and row.frequency == "IMMEDIATE" for row in email_rows):
        email_frequency = "IMMEDIATE"
    else:
        email_frequency = "OFF"

    # Legacy fallback is used only when normalized rows are absent.
    if not email_rows and legacy is not None:
        if key == "LEARNING" and legacy.email_schedule_reminder:
            email_frequency = "IMMEDIATE"
        elif key == "PROGRESS" and legacy.email_weekly_tips:
            email_frequency = "WEEKLY"

    mobile_default = False
    if not rows("MOBILE_PUSH", exact=False) and not rows("MOBILE_PUSH", exact=True):
        if key == "LEARNING" and legacy is not None:
            mobile_default = bool(legacy.push_schedule_reminder or legacy.push_study_tips)

    return NotificationCategorySetting(
        category=key,
        in_app=enabled("IN_APP", _DEFAULT_IN_APP.get(key, False)),
        mobile_push=enabled("MOBILE_PUSH", mobile_default),
        email_frequency=email_frequency,
    )


async def get_notification_settings(*, user_id: str) -> NotificationSettingsResponse:
    snapshot = await notification_repo.notification_settings_snapshot(user_id)
    policy = snapshot["policy"]
    legacy = snapshot["legacy"]
    profile = snapshot["profile"]
    preferences = snapshot["preferences"]
    return NotificationSettingsResponse(
        engagement_enabled=(
            bool(policy.engagement_enabled)
            if policy is not None
            else bool(legacy.notifications if legacy is not None else False)
        ),
        timezone=(
            policy.timezone
            if policy is not None
            else (legacy.timezone if legacy is not None else "UTC")
        ),
        timezone_source=(
            policy.timezone_source
            if policy is not None
            else (legacy.timezone_source if legacy is not None else None)
        ),
        quiet_hours_start=(
            policy.quiet_hours_start
            if policy is not None
            else getattr(profile, "quiet_hours_start", None)
        ),
        quiet_hours_end=(
            policy.quiet_hours_end
            if policy is not None
            else getattr(profile, "quiet_hours_end", None)
        ),
        max_daily_notifications=min(
            5,
            max(
                1,
                (
                    policy.max_daily_notifications
                    if policy is not None
                    else (getattr(profile, "max_daily_notifications", None) or 5)
                ),
            ),
        ),
        digest_local_time=(
            policy.digest_local_time if policy and policy.digest_local_time else "09:00"
        ),
        digest_day_of_week=(
            policy.digest_day_of_week if policy and policy.digest_day_of_week is not None else 0
        ),
        categories=[
            _effective_category_setting(key, preferences, legacy) for key in _SETTINGS_CATEGORIES
        ],
    )


async def update_notification_settings(
    *, user_id: str, request: NotificationSettingsUpdate
) -> NotificationSettingsResponse:
    current = await notification_repo.notification_settings_snapshot(user_id)
    policy = current["policy"]
    legacy = current["legacy"]
    preferences: list[dict[str, Any]] = []
    by_key = {item.category: item for item in request.categories}
    for key, database_categories in _SETTINGS_CATEGORIES.items():
        item = by_key[key]
        email_enabled = item.email_frequency != "OFF"
        email_frequency = "DIGEST" if item.email_frequency == "WEEKLY" else item.email_frequency
        for category in database_categories:
            preferences.extend(
                [
                    {
                        "category": category,
                        "channel": "IN_APP",
                        "enabled": item.in_app,
                        "frequency": "IMMEDIATE" if item.in_app else "OFF",
                        "digest_period": None,
                    },
                    {
                        "category": category,
                        "channel": "MOBILE_PUSH",
                        "enabled": item.mobile_push,
                        "frequency": "IMMEDIATE" if item.mobile_push else "OFF",
                        "digest_period": None,
                    },
                    {
                        "category": category,
                        "channel": "EMAIL",
                        "enabled": email_enabled,
                        "frequency": email_frequency,
                        "digest_period": "WEEKLY" if item.email_frequency == "WEEKLY" else None,
                    },
                ]
            )

    learning = by_key["LEARNING"]
    progress = by_key["PROGRESS"]
    await notification_repo.update_notification_settings(
        user_id,
        policy_values={
            "engagement_enabled": request.engagement_enabled,
            "timezone": (
                policy.timezone
                if policy is not None
                else (legacy.timezone if legacy is not None else "UTC")
            ),
            "timezone_source": (
                policy.timezone_source
                if policy is not None
                else (legacy.timezone_source if legacy is not None else None)
            ),
            "quiet_hours_start": request.quiet_hours_start,
            "quiet_hours_end": request.quiet_hours_end,
            "max_daily_notifications": request.max_daily_notifications,
            "digest_local_time": request.digest_local_time,
            "digest_day_of_week": request.digest_day_of_week,
        },
        preferences=preferences,
        legacy_values={
            "notifications": request.engagement_enabled,
            "email_schedule_reminder": learning.email_frequency == "IMMEDIATE",
            "email_weekly_tips": progress.email_frequency == "WEEKLY",
            "email_morning_schedule": False,
            "push_schedule_reminder": learning.mobile_push,
            "push_study_tips": learning.mobile_push,
        },
    )
    return await get_notification_settings(user_id=user_id)


#: Types that are themselves a periodic summary, so a learner asking for a "weekly" email is
#: asking for these to arrive on their own schedule. Every other type treats a digest
#: preference as "not yet": bundling many notifications into one email is a separate piece of
#: work, and emailing each one immediately instead would be the opposite of what was asked.
PERIODIC_EMAIL_TYPES = frozenset(
    {
        "progress.weekly_summary",
        "learning.morning_schedule",
    }
)


async def _email_plan(user_id: str, *, type: str, spec: Any) -> EmailPlan | None:
    """Decide whether this notification should also be emailed, and to what address.

    Resolved at plan time so the overwhelming majority of learners — who have email off —
    never accumulate delivery rows. Consent is rechecked immediately before sending, so a
    preference changed after planning still suppresses the message rather than racing it.
    """

    if "EMAIL" not in spec.allowed_channels:
        return None
    settings = get_settings()
    if not settings.NOTIFICATION_EMAIL_ENABLED:
        return None
    if not capability_enabled_for("EMAIL", user_id, settings=settings):
        return None

    decision = await notification_repo.channel_policy(
        user_id, type, spec.category or "LEARNING", "EMAIL"
    )
    policy = decision["policy"]
    legacy = decision["legacy"]
    override = decision["override"]
    # Fail closed on every missing record: absent consent is not consent.
    if policy is None or not policy.engagement_enabled:
        return None
    if legacy is None or not legacy.notifications:
        return None
    if override is None or not override.enabled:
        return None
    if override.frequency == "DIGEST" and type not in PERIODIC_EMAIL_TYPES:
        return None
    if override.frequency not in ("IMMEDIATE", "DIGEST"):
        return None

    recipient = await notification_repo.email_recipient(user_id)
    if recipient is None:
        return None
    address, _name = recipient
    address_ref = address_reference(address)
    # A suppressed address is a hard stop the provider asked for. Checking it here as well as
    # at dispatch keeps rows out of the ledger that could never be sent.
    if await notification_repo.is_address_suppressed(address_ref):
        return None
    return EmailPlan(
        address_ref=address_ref,
        provider="SMTP_RESEND",
        max_attempts=settings.NOTIFICATION_EMAIL_MAX_ATTEMPTS,
    )


async def apply_unsubscribe(*, token: str) -> bool:
    """Honour a signed unsubscribe link. Returns whether a valid token was applied.

    Switches email off for the requested scope through the same settings write the UI uses, so
    the change is visible in the settings screen rather than living in a hidden side table —
    a learner who unsubscribes and later looks at their settings must see it already off.

    Mandatory mail is unaffected: security and account-recovery messages are transactional and
    never planned through the consent matrix, so there is nothing here to switch off.
    """

    request = parse_unsubscribe_token(token)
    if request is None:
        return False

    current = await get_notification_settings(user_id=request.user_id)
    scopes = (
        {"LEARNING", "PROGRESS", "SOCIAL_CLASSROOM", "PRODUCT_UPDATES"}
        if request.scope == "ALL"
        else {request.scope}
    )
    updated = NotificationSettingsUpdate(
        engagement_enabled=current.engagement_enabled,
        quiet_hours_start=current.quiet_hours_start,
        quiet_hours_end=current.quiet_hours_end,
        max_daily_notifications=current.max_daily_notifications,
        digest_local_time=current.digest_local_time,
        digest_day_of_week=current.digest_day_of_week,
        categories=[
            item.model_copy(update={"email_frequency": "OFF"}) if item.category in scopes else item
            for item in current.categories
        ],
    )
    await update_notification_settings(user_id=request.user_id, request=updated)
    logger.info(
        "Applied an unsubscribe request",
        extra={"scope": request.scope, "categories": len(scopes)},
    )
    return True


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
        plan_mobile_push="MOBILE_PUSH" in spec.default_channels,
        plan_email=await _email_plan(user_id, type=type, spec=spec),
    )
    if mutation:
        if replaced_id is not None:
            await _emit_hint("notification.updated", user_id, replaced_id)
        await _emit_hint(f"notification.{mutation}", user_id, row.id)
        await _emit_unread_count(user_id)
    return row


async def lifecycle_metrics() -> dict[str, Any]:
    """Return redacted database-backed operational metrics for staff."""

    data = await notification_repo.lifecycle_metrics(now=datetime.now(UTC))
    logger.info(
        "Notification lifecycle metrics inspected",
        extra={
            "actionable_groups": len(data["actionableDeliveries"]),
            "failure_groups_24h": len(data["failuresLast24Hours"]),
            "interaction_groups_24h": len(data["interactionsLast24Hours"]),
        },
    )
    return data


async def list_push_installations(*, user_id: str):
    return await notification_repo.list_installations(user_id)


async def upsert_mobile_push_installation(*, user_id: str, request: MobilePushInstallationUpsert):
    return await notification_repo.upsert_mobile_installation(user_id, request.model_dump())


async def revoke_push_installation(*, installation_id: str, revocation_secret: str) -> None:
    await notification_repo.revoke_installation(installation_id, revocation_secret)


async def disable_push_installation(*, user_id: str, installation_id: str) -> bool:
    return await notification_repo.disable_installation(user_id, installation_id)


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
