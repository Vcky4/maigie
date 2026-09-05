"""Sending the EMAIL deliveries the orchestrator planned.

Structured to mirror the mobile-push dispatcher, because the failure modes are the same and
solving them twice differently is how the two channels drift apart:

  1. claim a bounded batch and mark it `SENDING` before any provider call, so a crash costs
     an attempt rather than repeating forever;
  2. recheck the *authoritative* state immediately before sending — consent, quiet hours,
     rollout, and whether the learner has already read the item in the app — because
     planning happened earlier and any of those may have changed since;
  3. record one attempt row per provider request, and move the delivery to a status that
     names only what is actually known.

Provider acceptance is recorded as `ACCEPTED`, not `DELIVERED`. Nothing here can observe an
inbox; that needs the provider webhooks in the next slice.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import get_settings
from src.shared.time import (
    LearnerTimezone,
    is_within_quiet_hours,
    next_end_of_quiet_hours,
    parse_hhmm,
)

from .email_delivery import address_reference, send_notification_email
from .feature_flags import capability_enabled_for
from .metrics import EMAIL_CLAIMED, EMAIL_OUTCOMES
from .repository import notification_repo
from .service import PERIODIC_EMAIL_TYPES

logger = logging.getLogger(__name__)


def _retry_at(delivery_id: str, attempt: int, now: datetime) -> datetime:
    """Exponential backoff with per-delivery jitter, so retries do not arrive in lockstep."""

    base = min(3600, 300 * (2 ** max(0, attempt - 1)))
    jitter = int.from_bytes(
        hashlib.sha256(f"{delivery_id}:{attempt}".encode()).digest()[:2], "big"
    ) % max(1, base // 4)
    return now + timedelta(seconds=base + jitter)


async def _email_allowed(notification, now: datetime) -> tuple[bool, str | None, datetime | None]:
    """Recheck consent and timing at send time, returning a defer time where relevant."""

    settings = get_settings()
    if not settings.NOTIFICATION_EMAIL_ENABLED:
        return False, "EMAIL_CHANNEL_DISABLED", None
    if not capability_enabled_for("EMAIL", notification.user_id, settings=settings):
        # Deferred rather than suppressed: a cohort the learner is not yet in may include
        # them later, and throwing the row away would need the producer to run again.
        return False, None, now + timedelta(minutes=15)

    decision = await notification_repo.channel_policy(
        notification.user_id,
        notification.type,
        notification.category or "LEARNING",
        "EMAIL",
    )
    policy = decision["policy"]
    override = decision["override"]
    # The policy is the master gate; the legacy `UserPreferences.notifications` column was normalized
    # into it (identical for every user at retirement), so it no longer needs its own check.
    if policy is None or not policy.engagement_enabled:
        return False, "ENGAGEMENT_DISABLED", None
    if override is None:
        return False, "EMAIL_CONSENT_MISSING", None
    if not override.enabled:
        return False, "CHANNEL_DISABLED", None
    if override.frequency == "DIGEST" and notification.type not in PERIODIC_EMAIL_TYPES:
        # Not a refusal any more: the digest planner collects this notification and emails it
        # with the rest of its period, so the individual send is held rather than dropped.
        return False, "HELD_FOR_DIGEST", None
    if override.frequency not in ("IMMEDIATE", "DIGEST"):
        return False, "CHANNEL_DISABLED", None

    # The settings screen promises quiet hours apply to notifications, so email honours them
    # too — deferred to the end of the window rather than dropped.
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


async def dispatch_due_email(*, limit: int | None = None) -> int:
    """Send one bounded batch of due notification emails. Returns messages attempted."""

    settings = get_settings()
    batch = min(limit or settings.NOTIFICATION_EMAIL_BATCH, settings.NOTIFICATION_EMAIL_BATCH)
    now = datetime.now(UTC)
    claimed = await notification_repo.claim_due_email_deliveries(limit=batch, now=now)
    EMAIL_CLAIMED.inc(len(claimed))
    if not claimed:
        return 0

    sent = 0
    for delivery, notification in claimed:
        allowed, reason, deferred_until = await _email_allowed(notification, now)
        if not allowed:
            if deferred_until is not None:
                await notification_repo.defer_delivery(delivery.id, next_attempt_at=deferred_until)
                EMAIL_OUTCOMES.labels(stage="policy", outcome="deferred").inc()
            else:
                await notification_repo.suppress_delivery(delivery.id, reason or "POLICY")
                EMAIL_OUTCOMES.labels(stage="policy", outcome="suppressed").inc()
            continue

        # Re-read the address at send time. It may have changed since planning, and the
        # account may have been deactivated.
        recipient = await notification_repo.email_recipient(notification.user_id)
        if recipient is None:
            await notification_repo.suppress_delivery(delivery.id, "NO_USABLE_ADDRESS")
            EMAIL_OUTCOMES.labels(stage="policy", outcome="suppressed").inc()
            continue
        address, name = recipient

        # Rechecked here even though planning also checks it: a bounce or complaint webhook
        # may have arrived in between, and that is exactly the case where sending again does
        # lasting damage to the sending domain.
        suppression = await notification_repo.is_address_suppressed(address_reference(address))
        if suppression is not None:
            await notification_repo.suppress_delivery(delivery.id, f"SUPPRESSED_{suppression}")
            EMAIL_OUTCOMES.labels(stage="policy", outcome="address_suppressed").inc()
            continue

        requested_at = datetime.now(UTC)
        outcome = await send_notification_email(
            to_email=address,
            recipient_name=name,
            title=notification.title,
            body=notification.body,
            category=notification.category,
            notification_id=notification.id,
            user_id=notification.user_id,
        )
        await notification_repo.record_email_result(
            delivery.id,
            requested_at=requested_at,
            duration_ms=outcome.duration_ms,
            accepted=outcome.accepted,
            provider=outcome.provider,
            provider_message_id=outcome.provider_message_id,
            retryable=outcome.retryable,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            next_attempt_at=_retry_at(delivery.id, delivery.attempt_count, datetime.now(UTC)),
        )
        result = "accepted" if outcome.accepted else ("retry" if outcome.retryable else "failed")
        EMAIL_OUTCOMES.labels(stage="send", outcome=result).inc()
        if outcome.accepted:
            sent += 1

    logger.info(
        "Canonical notification email dispatch completed",
        extra={"claimed": len(claimed), "accepted": sent},
    )
    return len(claimed)
