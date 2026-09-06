"""Validation of the two client-supplied halves of a Web Push subscription.

A subscription arrives from a browser as a URL plus two key values, and the server later POSTs
to that URL from a background worker. That makes the endpoint the most dangerous field in the
notification system: it is a request destination chosen by whoever calls the API.

Two things follow. First, the endpoint is checked against an allowlist of push-service hosts
(`WEB_PUSH_ALLOWED_ENDPOINT_HOSTS`) and must be `https`, so the worker cannot be aimed at
cloud metadata, a private range, or an internal service. Second, the same check runs again at
send time rather than only at subscribe time, because a row written before the allowlist was
tightened would otherwise keep its permission forever.

The key material is validated here too, and strictly. `p256dh` must be a point that actually
lies on P-256 and `auth` must be exactly 16 bytes, because the alternative is accepting a
subscription that cannot be encrypted to and only discovering it on the first send — by which
time the learner has been told notifications are on.
"""

from __future__ import annotations

import base64
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec

from src.config import get_settings

#: RFC 8291 fixes this: the authentication secret is 16 octets.
_AUTH_BYTES = 16
#: An uncompressed P-256 point: the 0x04 tag plus two 32-byte coordinates.
_P256DH_BYTES = 65


class InvalidPushSubscription(ValueError):
    """A subscription cannot be stored or used as given."""


def _b64decode(value: str) -> bytes:
    # Browsers emit unpadded base64url, but some clients re-pad it. Accept either, and accept
    # standard base64 too, since a client that swaps the alphabet is a common and harmless
    # mistake to be tolerant of.
    normalised = value.strip().replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(normalised + "=" * (-len(normalised) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidPushSubscription("subscription key is not valid base64url") from exc


def host_is_allowed(host: str) -> bool:
    """Whether `host` matches the configured allowlist."""
    host = host.lower()
    for entry in get_settings().WEB_PUSH_ALLOWED_ENDPOINT_HOSTS:
        candidate = entry.strip().lower()
        if not candidate:
            continue
        if candidate.startswith("."):
            # A suffix entry must match a *label* boundary. Comparing with `endswith` alone
            # would let `evilnotify.windows.com` through on a `.notify.windows.com` entry.
            if host.endswith(candidate) and len(host) > len(candidate):
                return True
        elif host == candidate:
            return True
    return False


def validate_push_endpoint(endpoint: str) -> str:
    """Return the endpoint unchanged, or raise `InvalidPushSubscription`."""

    value = endpoint.strip()
    if not value:
        raise InvalidPushSubscription("endpoint is required")
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise InvalidPushSubscription("endpoint must use https")
    if parts.username or parts.password:
        # `https://allowed.host@internal/` reads as the allowed host to a careless parser.
        raise InvalidPushSubscription("endpoint must not carry credentials")
    host = parts.hostname or ""
    if not host:
        raise InvalidPushSubscription("endpoint has no host")
    if not host_is_allowed(host):
        raise InvalidPushSubscription(f"endpoint host {host!r} is not an allowed push service")
    if parts.port is not None and parts.port != 443:
        # Every real push service listens on 443. A custom port on an allowed host is either
        # a mistake or an attempt to reach something else that happens to share the name.
        raise InvalidPushSubscription("endpoint must use the default https port")
    return value


def validate_p256dh(value: str) -> str:
    """Check the client public key is a real P-256 point, returning it unchanged."""

    raw = _b64decode(value)
    if len(raw) != _P256DH_BYTES:
        raise InvalidPushSubscription(
            f"p256dh must be {_P256DH_BYTES} bytes, got {len(raw)}",
        )
    try:
        # Raises for a point that is not on the curve, which is the case worth catching:
        # encrypting to it would fail, and a bad point is also how curve attacks start.
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise InvalidPushSubscription("p256dh is not a valid P-256 public key") from exc
    return value.strip()


def validate_auth(value: str) -> str:
    """Check the authentication secret is exactly 16 bytes, returning it unchanged."""

    raw = _b64decode(value)
    if len(raw) != _AUTH_BYTES:
        raise InvalidPushSubscription(f"auth must be {_AUTH_BYTES} bytes, got {len(raw)}")
    return value.strip()
