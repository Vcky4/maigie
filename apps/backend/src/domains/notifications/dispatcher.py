"""Canonical mobile-push planning execution and Expo receipt reconciliation."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import get_settings
from src.shared.infrastructure.expo_push import ExpoMessage, ExpoPushAdapter
from src.shared.time import (
    LearnerTimezone,
    is_within_quiet_hours,
    next_end_of_quiet_hours,
    parse_hhmm,
)

from .feature_flags import capability_enabled_for
from .metrics import (
    MOBILE_PUSH_CLAIMED,
    MOBILE_PUSH_LAST_BATCH,
    MOBILE_PUSH_OUTCOMES,
    MOBILE_PUSH_STALE_RECOVERED,
)
from .repository import notification_repo
from .taxonomy import android_channel_id

logger = logging.getLogger(__name__)
DISPATCH_BATCH = 100
RECEIPT_BATCH = 100


def mobile_push_enabled_for(user_id: str) -> bool:
    """Compatibility wrapper around the shared notification capability gate."""

    return capability_enabled_for("MOBILE_PUSH", user_id)


def _retry_at(delivery_id: str, attempt: int, now: datetime) -> datetime:
    base = min(3600, 60 * (2 ** max(0, attempt - 1)))
    jitter = int.from_bytes(
        hashlib.sha256(f"{delivery_id}:{attempt}".encode()).digest()[:2], "big"
    ) % max(1, base // 4)
    return now + timedelta(seconds=base + jitter)


def _safe_data(action: dict | None, notification_id: str, delivery_id: str) -> dict:
    return {
        "action": action or {"version": 1, "kind": "NONE"},
        "notificationId": notification_id,
        "deliveryId": delivery_id,
    }


async def _dispatch_allowed(
    delivery, notification, installation, now: datetime
) -> tuple[bool, str | None, datetime | None]:
    if installation.permission_state == "DENIED":
        return False, "PERMISSION_DENIED", None
    policy_data = await notification_repo.dispatch_policy(
        notification.user_id, notification.type, notification.category or "LEARNING"
    )
    policy = policy_data["policy"]
    legacy = policy_data["legacy"]
    override = policy_data["override"]
    if policy is None or not policy.engagement_enabled:
        return False, "ENGAGEMENT_DISABLED", None
    if legacy is None or not legacy.notifications:
        return False, "LEGACY_MASTER_DISABLED", None
    if override is None:
        return False, "MOBILE_PUSH_CONSENT_MISSING", None
    if not override.enabled or override.frequency != "IMMEDIATE":
        return False, "CHANNEL_DISABLED", None

    try:
        timezone_ = LearnerTimezone(
            zone=ZoneInfo(policy.timezone),
            name=policy.timezone,
            is_known=True,
            source=policy.timezone_source,
        )
    except ZoneInfoNotFoundError:
        timezone_ = LearnerTimezone(zone=ZoneInfo("UTC"), name="UTC", is_known=False, source=None)
    quiet_from = parse_hhmm(policy.quiet_hours_start)
    quiet_to = parse_hhmm(policy.quiet_hours_end)
    if is_within_quiet_hours(now, timezone_, quiet_from, quiet_to):
        return False, "QUIET_HOURS", next_end_of_quiet_hours(now, timezone_, quiet_to)
    return True, None, None


async def dispatch_due(*, limit: int = DISPATCH_BATCH) -> int:
    now = datetime.now(UTC)
    claimed = await notification_repo.claim_due_deliveries(
        limit=min(limit, DISPATCH_BATCH), now=now
    )
    MOBILE_PUSH_CLAIMED.inc(len(claimed))
    MOBILE_PUSH_LAST_BATCH.labels(kind="dispatch").set(len(claimed))
    if not claimed:
        return 0

    candidates = []
    for delivery, notification, installation in claimed:
        if not await notification_repo.delivery_still_sendable(delivery.id):
            await notification_repo.suppress_delivery(delivery.id, "IN_APP_LIFECYCLE_CHANGED")
            MOBILE_PUSH_OUTCOMES.labels(stage="policy", outcome="lifecycle_suppressed").inc()
            continue
        if not mobile_push_enabled_for(notification.user_id):
            await notification_repo.defer_delivery(
                delivery.id, next_attempt_at=now + timedelta(minutes=15)
            )
            MOBILE_PUSH_OUTCOMES.labels(stage="policy", outcome="rollout_deferred").inc()
            continue
        allowed, reason, deferred_until = await _dispatch_allowed(
            delivery, notification, installation, now
        )
        if not allowed:
            if deferred_until is not None:
                await notification_repo.defer_delivery(delivery.id, next_attempt_at=deferred_until)
                MOBILE_PUSH_OUTCOMES.labels(stage="policy", outcome="quiet_deferred").inc()
            else:
                await notification_repo.suppress_delivery(delivery.id, reason or "POLICY")
                MOBILE_PUSH_OUTCOMES.labels(stage="policy", outcome="suppressed").inc()
            continue
        badge = await notification_repo.unread_count(notification.user_id)
        candidates.append((delivery, notification, installation, badge))

    if not candidates:
        return 0

    sendable = []
    messages: list[ExpoMessage] = []
    unavailable: list[str] = []
    requested_at = datetime.now(UTC)
    async with notification_repo.current_delivery_tokens(
        [delivery.id for delivery, _notification, _installation, _badge in candidates],
        now=requested_at,
    ) as current_tokens:
        for delivery, notification, installation, badge in candidates:
            current_token = current_tokens.get(delivery.id)
            if current_token is None:
                unavailable.append(delivery.id)
                continue
            sendable.append((delivery, notification, installation))
            messages.append(
                ExpoMessage(
                    token=current_token,
                    title=notification.title,
                    body=notification.body,
                    data=_safe_data(notification.action, notification.id, delivery.id),
                    channel_id=android_channel_id(notification.type),
                    badge=badge,
                )
            )
        if messages:
            async with ExpoPushAdapter() as adapter:
                outcomes = await adapter.send(messages)
        else:
            outcomes = []

    for delivery_id in unavailable:
        await notification_repo.suppress_delivery(delivery_id, "DESTINATION_UNAVAILABLE")
        MOBILE_PUSH_OUTCOMES.labels(stage="policy", outcome="lifecycle_suppressed").inc()
    if not messages:
        return 0
    duration_ms = int((datetime.now(UTC) - requested_at).total_seconds() * 1000)
    settings = get_settings()
    receipt_at = datetime.now(UTC) + timedelta(seconds=settings.MOBILE_PUSH_RECEIPT_DELAY_SECONDS)
    for (delivery, _notification, _installation), outcome in zip(sendable, outcomes, strict=True):
        next_attempt = (
            receipt_at
            if outcome.ticket_id
            else _retry_at(delivery.id, delivery.attempt_count, datetime.now(UTC))
        )
        await notification_repo.record_ticket_result(
            delivery.id,
            requested_at=requested_at,
            duration_ms=duration_ms,
            ticket_id=outcome.ticket_id,
            retryable=outcome.retryable,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            next_attempt_at=next_attempt,
            disable_destination=outcome.disable_destination,
        )
        result = "accepted" if outcome.ticket_id else ("retry" if outcome.retryable else "failed")
        MOBILE_PUSH_OUTCOMES.labels(stage="ticket", outcome=result).inc()
    logger.info(
        "Canonical mobile push dispatch completed",
        extra={"claimed": len(claimed), "requested": len(messages)},
    )
    return len(messages)


async def reconcile_receipts(*, limit: int = RECEIPT_BATCH) -> int:
    now = datetime.now(UTC)
    deliveries = await notification_repo.accepted_for_receipts(
        limit=min(limit, RECEIPT_BATCH), now=now
    )
    MOBILE_PUSH_LAST_BATCH.labels(kind="receipts").set(len(deliveries))
    if not deliveries:
        return 0
    ticket_ids = [row.provider_message_id for row in deliveries if row.provider_message_id]
    async with ExpoPushAdapter() as adapter:
        outcomes = await adapter.receipts(ticket_ids)
    missing: list[str] = []
    for delivery in deliveries:
        ticket_id = delivery.provider_message_id
        if not ticket_id:
            continue
        outcome = outcomes[ticket_id]
        if outcome.pending:
            missing.append(delivery.id)
            MOBILE_PUSH_OUTCOMES.labels(stage="receipt", outcome="pending").inc()
            continue
        await notification_repo.record_receipt(
            delivery.id,
            delivered=outcome.delivered,
            retryable=outcome.retryable,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            next_attempt_at=_retry_at(delivery.id, delivery.attempt_count, now),
            disable_destination=outcome.disable_destination,
        )
        MOBILE_PUSH_OUTCOMES.labels(
            stage="receipt", outcome="delivered" if outcome.delivered else "failed"
        ).inc()
    await notification_repo.defer_missing_receipts(
        missing,
        next_attempt_at=now + timedelta(seconds=get_settings().MOBILE_PUSH_RECEIPT_DELAY_SECONDS),
    )
    logger.info(
        "Expo receipt reconciliation completed",
        extra={"checked": len(deliveries), "pending": len(missing)},
    )
    return len(deliveries)


async def recover_stale_sending() -> int:
    now = datetime.now(UTC)
    expired = await notification_repo.expire_due_deliveries(now=now)
    stale_before = now - timedelta(seconds=get_settings().MOBILE_PUSH_STALE_SENDING_SECONDS)
    count = await notification_repo.recover_stale_sending(stale_before=stale_before)
    MOBILE_PUSH_STALE_RECOVERED.inc(count)
    if count or expired:
        logger.warning(
            "Reconciled canonical mobile push lifecycle",
            extra={"stale_recovered": count, "expired": expired},
        )
    return count + expired
