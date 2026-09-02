"""Turning one canonical notification into one Web Push message, and reporting what happened.

The shape mirrors `email_delivery.py` deliberately: this module owns the transport and returns
an outcome the delivery ledger can record, and the dispatcher owns policy. Nothing here raises
for a provider failure, because a raised exception would abort a batch and lose the evidence
for the message that failed.

**What a Web Push send actually is.** Three separate pieces have to be right at once:

1. *Encryption* (RFC 8291). The body is encrypted to the browser's own key material, so the
   push service is a blind relay — it cannot read what it carries. `http_ece` does this.
2. *Identification* (RFC 8292/VAPID). A JWT signed with our private key, plus our public key in
   the same header. This is self-asserted: it does not prove we are allowed to send, it gives
   the push service a stable identity to rate-limit and contact.
3. *Delivery* (RFC 8030). An HTTP POST to the endpoint, whose status code is the only signal
   about the subscription's health.

**Why the status code matters more than for any other channel.** 404 and 410 mean the
subscription is gone — the browser was uninstalled, the site data cleared, or the learner
revoked permission — and the push service is telling us to stop. Unlike a bounced email, this
is authoritative and immediate, so it prunes the installation rather than counting a failure.
Retrying a 410 forever is how a push integration ends up permanently failing on rows that will
never work again.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urlsplit

import http_ece
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt

from src.config import settings

from .web_push_endpoint import InvalidPushSubscription, validate_push_endpoint

logger = logging.getLogger(__name__)

#: Every push service must accept at least 4096 bytes of encrypted payload (RFC 8291); many
#: accept no more. aes128gcm adds a 21-byte header plus the ephemeral public key, a padding
#: delimiter, and a 16-byte tag — about 103 bytes in total — so this is the plaintext ceiling
#: with room to spare. Overrunning it earns a 413 that no retry can fix.
MAX_PAYLOAD_BYTES = 3800

#: VAPID tokens may live at most 24 hours (RFC 8292). Twelve hours is signed, and a cached
#: token is re-signed once under thirty minutes remain, so a long-running worker never presents
#: one that expires mid-flight.
_TOKEN_LIFETIME_SECONDS = 12 * 3600
_TOKEN_REFRESH_MARGIN_SECONDS = 30 * 60

#: Cached `origin -> (token, expires_at)`. Signing is not expensive, but a batch of 50 pushes
#: to the same service would otherwise sign 50 identical tokens.
_token_cache: dict[str, tuple[str, float]] = {}


class WebPushConfigurationError(Exception):
    """The sender cannot operate because VAPID configuration is missing or malformed."""


@dataclass(frozen=True)
class WebPushOutcome:
    """One push service attempt, described in the terms the delivery ledger records."""

    accepted: bool
    provider: str
    provider_message_id: str | None
    retryable: bool
    #: The subscription is gone for good and the installation must be pruned. Distinct from
    #: `retryable=False`: a permanent *send* failure says nothing about the subscription, while
    #: this says the address itself will never work again.
    expired: bool
    error_code: str | None
    error_detail: str | None
    duration_ms: int
    #: Seconds the service asked us to wait, from `Retry-After`, when it gave one.
    retry_after_seconds: int | None = None


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def web_push_configured() -> bool:
    """Whether a push could actually be signed and sent right now.

    Checked separately from the `WEB_PUSH_ENABLED` kill switch so the two failure modes stay
    distinguishable: "switched off" is a decision, "unconfigured" is a mistake.
    """

    if not (settings.WEB_PUSH_VAPID_PUBLIC_KEY and settings.WEB_PUSH_VAPID_PRIVATE_KEY):
        return False
    try:
        _vapid_private_key(settings.WEB_PUSH_VAPID_PRIVATE_KEY)
    except WebPushConfigurationError:
        return False
    return True


def vapid_public_key() -> str | None:
    """The `applicationServerKey` a browser must subscribe with, if one is configured."""

    return settings.WEB_PUSH_VAPID_PUBLIC_KEY or None


@lru_cache(maxsize=2)
def _vapid_private_key(encoded: str) -> ec.EllipticCurvePrivateKey:
    """Load the base64url raw 32-byte scalar into a usable key.

    Cached on the encoded value, so a configuration change is picked up rather than pinned,
    while the common case does not re-parse on every send.
    """

    try:
        raw = _b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise WebPushConfigurationError("VAPID private key is not base64url") from exc
    if len(raw) != 32:
        raise WebPushConfigurationError(
            f"VAPID private key must decode to 32 bytes, got {len(raw)}"
        )
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except ValueError as exc:
        raise WebPushConfigurationError("VAPID private key is not a valid P-256 scalar") from exc


def _vapid_headers(endpoint: str) -> dict[str, str]:
    """Build the `Authorization: vapid` header for this endpoint's origin.

    The audience is the endpoint's origin with no path. Including the path is a common mistake
    and some services reject it, which presents as an unexplained 401.
    """

    parts = urlsplit(endpoint)
    origin = f"{parts.scheme}://{parts.netloc}"
    now = time.time()
    cached = _token_cache.get(origin)
    if cached is not None and cached[1] - now > _TOKEN_REFRESH_MARGIN_SECONDS:
        token = cached[0]
    else:
        subject = settings.WEB_PUSH_VAPID_SUBJECT.strip()
        if not subject:
            # RFC 8292 requires `sub`, and some services silently drop pushes without it.
            # Failing here beats a push that vanishes with a 201.
            raise WebPushConfigurationError("WEB_PUSH_VAPID_SUBJECT is not configured")
        expires_at = now + _TOKEN_LIFETIME_SECONDS
        token = jwt.encode(
            {"aud": origin, "exp": int(expires_at), "sub": subject},
            _vapid_private_key(settings.WEB_PUSH_VAPID_PRIVATE_KEY).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            algorithm="ES256",
        )
        _token_cache[origin] = (token, expires_at)
    return {"Authorization": f"vapid t={token},k={settings.WEB_PUSH_VAPID_PUBLIC_KEY}"}


def encrypt_payload(payload: bytes, *, p256dh: str, auth: str) -> bytes:
    """Encrypt `payload` to one subscription's key material, aes128gcm."""

    return http_ece.encrypt(
        payload,
        salt=os.urandom(16),
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=_b64decode(p256dh),
        auth_secret=_b64decode(auth),
        version="aes128gcm",
    )


def build_payload(
    *,
    notification_id: str,
    title: str,
    body: str,
    action: dict | None,
    category: str | None,
) -> bytes:
    """Serialise what the service worker needs to render and route one notification.

    Only the canonical action is sent, never a URL. The client owns route mapping, so the
    worker resolves the action against its own allowlist; a URL from the server would be a
    second, unreviewable routing authority and an open-redirect waiting to happen.
    """

    document = {
        "v": 1,
        "id": notification_id,
        "title": title,
        "body": body,
        "category": category,
        "action": action or None,
    }

    def encode(doc: dict) -> bytes:
        return json.dumps(doc, separators=(",", ":")).encode("utf-8")

    encoded = encode(document)
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return encoded

    # Trim the body rather than dropping the message: the title and the action are what make it
    # actionable, and a truncated body still opens the full item in the app.
    #
    # Found by binary search on the character count, because the relationship between characters
    # and bytes is not fixed: an ellipsis is three bytes, an emoji JSON-escapes to twelve, and a
    # quote to two. Subtracting the byte overflow from the character count therefore overshoots
    # wildly on non-ASCII text — enough to empty the body of a message that only needed a few
    # words removed — while measuring each candidate keeps as much of it as actually fits.
    def with_body(length: int) -> bytes:
        document["body"] = f"{body[:length].rstrip()}…" if length else ""
        return encode(document)

    if len(with_body(0)) > MAX_PAYLOAD_BYTES:
        # The title alone overruns the budget. Returned so the send path reports one honest
        # `WEB_PUSH_PAYLOAD_TOO_LARGE` rather than silently mangling the title too.
        return with_body(0)

    low, high = 0, len(body)
    while low < high:
        middle = (low + high + 1) // 2
        if len(with_body(middle)) <= MAX_PAYLOAD_BYTES:
            low = middle
        else:
            high = middle - 1
    return with_body(low)


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        # Only the delta-seconds form is honoured. The HTTP-date form is legal but rare here,
        # and misreading it as seconds would produce a nonsensical delay.
        return max(0, min(86400, int(raw)))
    except ValueError:
        return None


def _classify(response: httpx.Response, started: datetime) -> WebPushOutcome:
    """Map a push service response onto the ledger's vocabulary."""

    status = response.status_code
    common = {
        "provider": "WEB_PUSH",
        "duration_ms": _elapsed_ms(started),
        "retry_after_seconds": _retry_after_seconds(response),
    }
    if status in (200, 201, 202):
        return WebPushOutcome(
            accepted=True,
            provider_message_id=response.headers.get("location") or None,
            retryable=False,
            expired=False,
            error_code=None,
            error_detail=None,
            **common,
        )
    detail = (response.text or "")[:500]
    if status in (404, 410):
        # Authoritative: this subscription no longer exists. Prune, do not retry.
        return WebPushOutcome(
            accepted=False,
            provider_message_id=None,
            retryable=False,
            expired=True,
            error_code=f"WEB_PUSH_{status}",
            error_detail=detail,
            **common,
        )
    if status == 429 or status >= 500:
        return WebPushOutcome(
            accepted=False,
            provider_message_id=None,
            retryable=True,
            expired=False,
            error_code=f"WEB_PUSH_{status}",
            error_detail=detail,
            **common,
        )
    # Remaining 4xx are our fault, not the network's: a malformed request, a rejected VAPID
    # token, or an oversized payload. Retrying reproduces them exactly, so they are terminal
    # and logged loudly enough to be found.
    logger.warning("Web push rejected with %s: %s", status, detail)
    return WebPushOutcome(
        accepted=False,
        provider_message_id=None,
        retryable=False,
        expired=False,
        error_code=f"WEB_PUSH_{status}",
        error_detail=detail,
        **common,
    )


async def send_web_push(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: bytes,
    ttl_seconds: int | None = None,
    urgency: str = "normal",
) -> WebPushOutcome:
    """Encrypt and POST one push message, returning the outcome rather than raising."""

    started = datetime.now(UTC)

    def failure(
        *, code: str, detail: str, retryable: bool, expired: bool = False
    ) -> WebPushOutcome:
        return WebPushOutcome(
            accepted=False,
            provider="WEB_PUSH",
            provider_message_id=None,
            retryable=retryable,
            expired=expired,
            error_code=code,
            error_detail=detail[:500],
            duration_ms=_elapsed_ms(started),
        )

    try:
        # Re-checked at send time, not only at subscribe time: a row stored before the
        # allowlist was tightened must not keep its permission to be POSTed to.
        validate_push_endpoint(endpoint)
    except InvalidPushSubscription as exc:
        return failure(code="WEB_PUSH_ENDPOINT_REJECTED", detail=str(exc), retryable=False)

    try:
        headers = {
            **_vapid_headers(endpoint),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(settings.WEB_PUSH_TTL_SECONDS if ttl_seconds is None else ttl_seconds),
            "Urgency": urgency,
        }
        encrypted = encrypt_payload(payload, p256dh=p256dh, auth=auth)
    except WebPushConfigurationError as exc:
        # Not retryable by attempt count, but not the subscription's fault either. Terminal
        # here so a misconfiguration surfaces as a failed delivery with a readable reason
        # instead of a queue that never drains.
        logger.error("Web push is misconfigured: %s", exc)
        return failure(code="WEB_PUSH_MISCONFIGURED", detail=str(exc), retryable=False)
    except Exception as exc:
        # Encryption failing means the stored key material is unusable, which no retry fixes.
        logger.warning("Web push encryption failed: %s", exc)
        return failure(code="WEB_PUSH_ENCRYPT_FAILED", detail=str(exc), retryable=False)

    if len(encrypted) > 4096:
        return failure(
            code="WEB_PUSH_PAYLOAD_TOO_LARGE",
            detail=f"encrypted payload is {len(encrypted)} bytes",
            retryable=False,
        )

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(endpoint, content=encrypted, headers=headers)
    except httpx.TimeoutException as exc:
        return failure(code="WEB_PUSH_TIMEOUT", detail=str(exc), retryable=True)
    except httpx.HTTPError as exc:
        return failure(code="WEB_PUSH_TRANSPORT", detail=str(exc), retryable=True)

    return _classify(response, started)
