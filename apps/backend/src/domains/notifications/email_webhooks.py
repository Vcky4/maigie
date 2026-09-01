"""Ingesting what the email provider tells us afterwards.

Until now an email's ledger entry stopped at `ACCEPTED`, because acceptance was the last thing
this system could honestly observe. A provider webhook is the only source that can say more, so
this module is what finally lets `DELIVERED` mean delivered — and lets a bounce or a complaint
become a suppression instead of a silent repeat offence.

Three properties matter more than the mapping itself:

**Signatures, or nothing.** The endpoint is public. An unsigned or unconfigured deployment
refuses events rather than trusting them; a forged `delivered` would launder a failure, and a
forged `bounced` would suppress an address on someone else's say-so.

**Replay safety.** Providers retry and do not promise ordering. Every event is recorded under
the provider's own id with a unique constraint, in the same transaction as its effect, so a
retry is a no-op rather than a second suppression.

**No overwriting a terminal truth.** A late `delivered` arriving after a `bounced` must not turn
a real failure into a success, so transitions are explicit about which states they may leave.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config import get_settings

from .email_delivery import address_reference
from .metrics import EMAIL_OUTCOMES
from .repository import notification_repo

logger = logging.getLogger(__name__)

PROVIDER = "resend"

#: Resend event types this system acts on. Anything else is recorded as `IGNORED` rather than
#: dropped, so a provider adding an event type is visible instead of silent.
_DELIVERED = "email.delivered"
_BOUNCED = "email.bounced"
_COMPLAINED = "email.complained"
_DELIVERY_DELAYED = "email.delivery_delayed"


@dataclass(frozen=True)
class WebhookResult:
    accepted: bool
    outcome: str


def _verify_svix_signature(
    *, body: bytes, svix_id: str, svix_timestamp: str, svix_signature: str, secret: str
) -> bool:
    """Verify Resend's Svix-style signature over ``{id}.{timestamp}.{body}``.

    Implemented here rather than adding the `svix` dependency: it is one HMAC over a documented
    string, and the header can carry several space-separated versioned signatures during a
    secret rotation, all of which must be considered.
    """

    if not (secret and svix_id and svix_timestamp and svix_signature):
        return False
    try:
        key = base64.b64decode(secret.split("_", 1)[1] if "_" in secret else secret)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False

    signed = f"{svix_id}.{svix_timestamp}.{body.decode('utf-8', 'replace')}".encode()
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in svix_signature.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


def _occurred_at(payload: dict[str, Any]) -> datetime:
    raw = payload.get("created_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        # A provider timestamp we cannot parse must not lose the event; the ingestion time is
        # a worse but usable answer, and the provider keeps the authoritative copy.
        return datetime.now(UTC)


def _recipients(data: dict[str, Any]) -> list[str]:
    to = data.get("to")
    if isinstance(to, str):
        return [to]
    if isinstance(to, list):
        return [item for item in to if isinstance(item, str)]
    return []


def _is_hard_bounce(data: dict[str, Any]) -> bool:
    """Only a permanent failure suppresses an address.

    A full mailbox or a temporary rejection is a soft bounce; suppressing on that would cut off
    a learner whose mail would work again tomorrow. Resend reports the class in `bounce.type`,
    and an unlabelled bounce is treated as soft — the retry budget bounds the damage, whereas a
    wrong suppression is invisible and permanent.
    """

    bounce = data.get("bounce")
    kind = ""
    if isinstance(bounce, dict):
        kind = str(bounce.get("type") or bounce.get("subType") or "").lower()
    return "permanent" in kind or "hard" in kind


async def process_resend_event(
    *,
    body: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    payload: dict[str, Any],
) -> WebhookResult:
    """Verify, record, and apply one Resend event. Never raises for provider content."""

    settings = get_settings()
    if not _verify_svix_signature(
        body=body,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        secret=settings.RESEND_WEBHOOK_SECRET,
    ):
        EMAIL_OUTCOMES.labels(stage="webhook", outcome="rejected").inc()
        return WebhookResult(accepted=False, outcome="INVALID_SIGNATURE")

    event_type = str(payload.get("type") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    provider_message_id = str(data.get("email_id") or data.get("id") or "") or None
    recipients = _recipients(data)
    address_hash = address_reference(recipients[0]) if recipients else None
    occurred_at = _occurred_at(payload)

    # `svix-id` is the provider's delivery id and is stable across retries of the same event.
    event_id = svix_id

    outcome = "IGNORED"
    if event_type == _DELIVERED:
        outcome = "DELIVERED"
    elif event_type == _COMPLAINED:
        outcome = "COMPLAINED"
    elif event_type == _BOUNCED:
        outcome = "HARD_BOUNCE" if _is_hard_bounce(data) else "SOFT_BOUNCE"
    elif event_type == _DELIVERY_DELAYED:
        # Nothing to change: the delivery is already `ACCEPTED` and the provider is still
        # trying. Recorded so a rise in delays is visible.
        outcome = "DELAYED"

    recorded = await notification_repo.record_email_provider_event(
        provider=PROVIDER,
        provider_event_id=event_id,
        event_type=event_type or "unknown",
        provider_message_id=provider_message_id,
        address_hash=address_hash,
        occurred_at=occurred_at,
        outcome=outcome,
    )
    if not recorded:
        # Already ingested. Returning accepted stops the provider retrying a settled event.
        EMAIL_OUTCOMES.labels(stage="webhook", outcome="replayed").inc()
        return WebhookResult(accepted=True, outcome="REPLAY")

    if outcome == "DELIVERED" and provider_message_id:
        await notification_repo.mark_email_delivered(
            provider_message_id=provider_message_id, delivered_at=occurred_at
        )
    elif outcome in ("HARD_BOUNCE", "COMPLAINED"):
        if provider_message_id:
            await notification_repo.mark_email_failed(
                provider_message_id=provider_message_id,
                failure_code=outcome,
                failed_at=occurred_at,
            )
        if address_hash:
            await notification_repo.suppress_address(
                address_hash,
                reason="HARD_BOUNCE" if outcome == "HARD_BOUNCE" else "COMPLAINT",
                provider=PROVIDER,
                provider_event_id=event_id,
                detail=f"{event_type} reported by {PROVIDER}",
            )
    elif outcome == "SOFT_BOUNCE" and provider_message_id:
        # A soft bounce is a failure of this attempt, not of the address.
        await notification_repo.mark_email_failed(
            provider_message_id=provider_message_id,
            failure_code="SOFT_BOUNCE",
            failed_at=occurred_at,
        )

    EMAIL_OUTCOMES.labels(stage="webhook", outcome=outcome.lower()).inc()
    logger.info(
        "Processed an email provider event",
        extra={"provider": PROVIDER, "eventType": event_type, "outcome": outcome},
    )
    return WebhookResult(accepted=True, outcome=outcome)
