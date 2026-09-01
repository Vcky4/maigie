"""Signed unsubscribe links that work without a session.

An unsubscribe link is followed from an inbox, often on a device that is not logged in, and
increasingly by a mail provider's own machinery rather than a person — Gmail and Yahoo expect
bulk senders to honour one-click unsubscribe (RFC 8058). So it cannot require authentication,
which means the link itself has to carry proof of who it belongs to.

The token is a signed statement, not a lookup key: `userId`, the scope being switched off, and
a version, with an HMAC over all of it. Nothing is stored, so there is no table to grow and no
row to miss when a learner is deleted. The signature uses `SECRET_KEY`, so rotating that
invalidates every outstanding link — acceptable, because the footer link in a new email is
regenerated on every send.

**Why there is no expiry.** An email can sit in an inbox for a year, and a learner clicking
unsubscribe on an old message means it now. Refusing because the link aged would be a worse
outcome than honouring it: they would mark the message as spam instead, which is the same
signal with a permanent reputational cost attached.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Literal

from src.config import get_settings

logger = logging.getLogger(__name__)

#: `ALL` switches every optional email off. A category scope switches off one settings group,
#: which is what a learner usually means by "stop these" — so both are offered and the email
#: footer names which one it is.
UnsubscribeScope = Literal["ALL", "LEARNING", "PROGRESS", "SOCIAL_CLASSROOM", "PRODUCT_UPDATES"]
_SCOPES: frozenset[str] = frozenset(
    {"ALL", "LEARNING", "PROGRESS", "SOCIAL_CLASSROOM", "PRODUCT_UPDATES"}
)
_VERSION = 1


@dataclass(frozen=True)
class UnsubscribeRequest:
    user_id: str
    scope: UnsubscribeScope


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(payload: bytes) -> str:
    secret = get_settings().SECRET_KEY.encode("utf-8")
    return _b64encode(hmac.new(secret, payload, hashlib.sha256).digest())


def create_unsubscribe_token(user_id: str, scope: UnsubscribeScope = "ALL") -> str:
    """Return a self-contained, tamper-evident unsubscribe token."""

    if scope not in _SCOPES:
        raise ValueError(f"unknown unsubscribe scope: {scope}")
    payload = json.dumps(
        {"v": _VERSION, "u": user_id, "s": scope}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return f"{_b64encode(payload)}.{_signature(payload)}"


def parse_unsubscribe_token(token: str) -> UnsubscribeRequest | None:
    """Validate a token and return what it asks for, or ``None`` if it is not trustworthy.

    Returns ``None`` rather than raising for every failure — a malformed link is not an
    exceptional condition, it is a truncated URL in a mail client, and the caller answers the
    same way regardless.
    """

    try:
        encoded_payload, provided_signature = token.split(".", 1)
        payload = _b64decode(encoded_payload)
    except (ValueError, TypeError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None

    # Constant time: a fast reject on the first differing byte would leak whether a guess is
    # getting closer, and this endpoint is unauthenticated by design.
    if not hmac.compare_digest(provided_signature, _signature(payload)):
        logger.warning("Rejected an unsubscribe token whose signature did not match")
        return None

    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != _VERSION:
        return None
    user_id = data.get("u")
    scope = data.get("s")
    if not isinstance(user_id, str) or not user_id or scope not in _SCOPES:
        return None
    return UnsubscribeRequest(user_id=user_id, scope=scope)  # type: ignore[arg-type]
