"""Sending the WEB_PUSH deliveries the orchestrator planned.

Deliberately the same four steps as the email and mobile dispatchers, because the failure modes
are the same and solving them three different ways is how channels drift apart:

  1. claim a bounded batch and mark it `SENDING` before any provider call, so a crash costs an
     attempt rather than repeating forever;
  2. recheck the *authoritative* state immediately before sending — consent, quiet hours,
     rollout, and whether the learner has already read the item in the app;
  3. record one attempt row per request, and move the delivery to a status naming only what is
     actually known;
  4. prune the subscription when the push service says it is gone.

Step 4 is what web push adds. A 404 or 410 is not a failure to retry: it is the push service
telling us this browser no longer exists, which is more authoritative than anything the email
channel ever learns. The same treatment covers a subscription whose stored key material cannot
be decrypted, because a learner who resubscribes is the only repair for either.
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

from .feature_flags import capability_enabled_for
from .metrics import WEB_PUSH_CLAIMED, WEB_PUSH_OUTCOMES, WEB_PUSH_PRUNED
from .repository import notification_repo
from .subscription_crypto import SubscriptionSecretUnreadable, decrypt_subscription_secret
from .web_push_delivery import build_payload, send_web_push, web_push_configured

logger = logging.getLogger(__name__)


def _retry_at(delivery_id: str, attempt: int, now: datetime) -> datetime:
    """Exponential backoff with per-delivery jitter, so retries do not arrive in lockstep."""

    base = min(3600, 60 * (2 ** max(0, attempt - 1)))
    jitter = int.from_bytes(
        hashlib.sha256(f"web-push:{delivery_id}:{attempt}".encode()).digest()[:2], "big"
    ) % max(1, base // 4)
    return now + timedelta(seconds=base + jitter)


async def _web_push_allowed(
    notification, now: datetime
) -> tuple[bool, str | None, datetime | None]:
    """Recheck consent and timing at send time, returning a defer time where relevant."""

    settings = get_settings()
    if not settings.WEB_PUSH_ENABLED:
        return False, "WEB_PUSH_CHANNEL_DISABLED", None
    if not web_push_configured():
        # A configuration gap is not the learner's fault and is not permanent, so the row waits
        # rather than being burned. It is also logged, because nothing else would surface it.
        logger.error("Web push deliveries are due but VAPID configuration is missing")
        return False, None, now + timedelta(minutes=15)
    if not capability_enabled_for("WEB_PUSH", notification.user_id, settings=settings):
        # Deferred rather than suppressed: a cohort the learner is not yet in may include them
        # later, and discarding the row would need the producer to run again.
        return False, None, now + timedelta(minutes=15)

    decision = await notification_repo.channel_policy(
        notification.user_id,
        notification.type,
        notification.category or "LEARNING",
        "WEB_PUSH",
    )
    policy = decision["policy"]
    override = decision["override"]
    # The policy is the master gate; the legacy `UserPreferences.notifications` column was normalized
    # into it (identical for every user at retirement), so it no longer needs its own check.
    if policy is None or not policy.engagement_enabled:
        return False, "ENGAGEMENT_DISABLED", None
    if override is None:
        return False, "WEB_PUSH_CONSENT_MISSING", None
    if not override.enabled:
        return False, "CHANNEL_DISABLED", None
    # A digest is an email arrangement. Holding an interruption channel for a digest would mean
    # a push that arrives days late, so a digest preference simply means no push for this item.
    if override.frequency != "IMMEDIATE":
        return False, "HELD_FOR_DIGEST", None

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


async def dispatch_due_web_push(*, limit: int | None = None) -> int:
    """Send one bounded batch of due web pushes. Returns rows claimed."""

    settings = get_settings()
    batch = min(limit or settings.WEB_PUSH_BATCH, settings.WEB_PUSH_BATCH)
    now = datetime.now(UTC)
    claimed = await notification_repo.claim_due_web_push_deliveries(limit=batch, now=now)
    WEB_PUSH_CLAIMED.inc(len(claimed))
    if not claimed:
        return 0

    sent = 0
    for delivery, notification, installation in claimed:
        allowed, reason, deferred_until = await _web_push_allowed(notification, now)
        if not allowed:
            if deferred_until is not None:
                await notification_repo.defer_delivery(delivery.id, next_attempt_at=deferred_until)
                WEB_PUSH_OUTCOMES.labels(stage="policy", outcome="deferred").inc()
            else:
                await notification_repo.suppress_delivery(delivery.id, reason or "POLICY")
                WEB_PUSH_OUTCOMES.labels(stage="policy", outcome="suppressed").inc()
            continue

        requested_at = datetime.now(UTC)
        try:
            p256dh = decrypt_subscription_secret(installation.p256dh_encrypted or "")
            auth = decrypt_subscription_secret(installation.auth_encrypted or "")
        except SubscriptionSecretUnreadable as exc:
            # Unreadable key material is as final as a 410: nothing can be encrypted to this
            # subscription again. Pruned so the learner is asked to resubscribe, rather than
            # failing identically on every future run.
            logger.warning(
                "Pruning web push subscription with unreadable key material",
                extra={"installation_id": installation.id, "reason": str(exc)},
            )
            await notification_repo.record_web_push_result(
                delivery.id,
                requested_at=requested_at,
                duration_ms=0,
                accepted=False,
                provider_message_id=None,
                retryable=False,
                expired=True,
                error_code="WEB_PUSH_KEYS_UNREADABLE",
                error_detail=str(exc),
                next_attempt_at=None,
            )
            WEB_PUSH_OUTCOMES.labels(stage="send", outcome="failed").inc()
            WEB_PUSH_PRUNED.labels(reason="keys_unreadable").inc()
            continue

        payload = build_payload(
            notification_id=notification.id,
            title=notification.title,
            body=notification.body,
            action=notification.action,
            category=notification.category,
        )
        outcome = await send_web_push(
            endpoint=installation.endpoint or "",
            p256dh=p256dh,
            auth=auth,
            payload=payload,
            # A time-critical item is worth waking a device for; everything else can wait for
            # the browser to check in, which costs the learner less battery.
            urgency="high" if notification.priority == 1 else "normal",
        )
        retry_at = _retry_at(delivery.id, delivery.attempt_count, datetime.now(UTC))
        if outcome.retry_after_seconds is not None:
            # The push service asked for a specific delay. Honour whichever is later, so our
            # backoff cannot undercut an explicit rate-limit instruction.
            requested_delay = datetime.now(UTC) + timedelta(seconds=outcome.retry_after_seconds)
            retry_at = max(retry_at, requested_delay)
        await notification_repo.record_web_push_result(
            delivery.id,
            requested_at=requested_at,
            duration_ms=outcome.duration_ms,
            accepted=outcome.accepted,
            provider_message_id=outcome.provider_message_id,
            retryable=outcome.retryable,
            expired=outcome.expired,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            next_attempt_at=retry_at,
        )
        if outcome.expired:
            WEB_PUSH_PRUNED.labels(reason="gone").inc()
        result = "accepted" if outcome.accepted else ("retry" if outcome.retryable else "failed")
        WEB_PUSH_OUTCOMES.labels(stage="send", outcome=result).inc()
        if outcome.accepted:
            sent += 1

    logger.info(
        "Canonical web push dispatch completed",
        extra={"claimed": len(claimed), "accepted": sent},
    )
    return len(claimed)
