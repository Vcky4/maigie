"""Encryption at rest for the two secrets in a Web Push subscription.

A browser subscription is three values: an endpoint URL, the client public key `p256dh`, and a
16-byte shared secret `auth`. Together they are *sending authority*. A push service does not
check who a sender is — VAPID is self-asserted, and any key pair will do — so anyone holding
these three values can deliver arbitrary notifications to that learner's browser, styled as us.
The endpoint alone is close to a bearer token already; `p256dh` and `auth` complete it.

That is why the columns are named `p256dhEncrypted` and `authEncrypted`, and why they hold
ciphertext. A database dump, a read-replica leak, or an over-broad support query should not
hand anyone the ability to push messages to learners.

**Key derivation.** The key is derived from `SECRET_KEY` with HKDF-SHA256 under a distinct
`info` label, rather than added as another environment variable to keep in sync. This follows
what unsubscribe tokens already do, and a separate `info` means this subkey cannot be used to
forge anything else derived from the same root.

**Rotation.** Rotating `SECRET_KEY` makes existing rows unreadable. That is handled honestly
rather than retried forever: `decrypt_subscription_secret` raises `SubscriptionSecretUnreadable`,
and the dispatcher prunes the installation so the learner is asked to subscribe again. The
alternative — a permanently failing delivery on every run — would look like a broken push
service instead of a rotated key.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import get_settings

#: Bumped only if the scheme changes. Stored as a prefix so a future scheme can be introduced
#: without guessing at the format of rows written by an older one.
_SCHEME = "v1"
_NONCE_BYTES = 12
_INFO = b"maigie.notifications.webpush.subscription.v1"


class SubscriptionSecretUnreadable(Exception):
    """A stored subscription secret cannot be decrypted, so the subscription is unusable."""


@lru_cache(maxsize=1)
def _key(secret_key: str) -> bytes:
    """Derive the 32-byte AES key. Cached because HKDF runs on every send otherwise."""
    if not secret_key:
        # Never reachable in a configured deployment; fail loudly rather than encrypting
        # under an empty key, which would be indistinguishable from encryption that works.
        raise SubscriptionSecretUnreadable("SECRET_KEY is not configured")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_INFO,
    ).derive(secret_key.encode("utf-8"))


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encrypt_subscription_secret(value: str) -> str:
    """Return `v1.<base64url(nonce||ciphertext)>` for a base64url subscription field."""
    if not value:
        raise ValueError("refusing to encrypt an empty subscription secret")
    nonce = os.urandom(_NONCE_BYTES)
    key = _key(get_settings().SECRET_KEY)
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), _SCHEME.encode("ascii"))
    return f"{_SCHEME}.{_b64encode(nonce + sealed)}"


def decrypt_subscription_secret(stored: str) -> str:
    """Recover a subscription field, raising `SubscriptionSecretUnreadable` if we cannot.

    Every failure mode collapses to one exception on purpose. The caller's only sane response
    to a corrupted, truncated, or wrong-key row is the same: stop trying to use this
    subscription and ask the browser for a new one.
    """
    if not stored:
        raise SubscriptionSecretUnreadable("subscription secret is empty")
    scheme, _, payload = stored.partition(".")
    if scheme != _SCHEME or not payload:
        raise SubscriptionSecretUnreadable(f"unsupported subscription secret scheme {scheme!r}")
    try:
        raw = _b64decode(payload)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise SubscriptionSecretUnreadable("subscription secret is not base64url") from exc
    if len(raw) <= _NONCE_BYTES:
        raise SubscriptionSecretUnreadable("subscription secret is truncated")
    key = _key(get_settings().SECRET_KEY)
    try:
        opened = AESGCM(key).decrypt(
            raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _SCHEME.encode("ascii")
        )
    except InvalidTag as exc:
        raise SubscriptionSecretUnreadable(
            "subscription secret failed authentication; SECRET_KEY may have been rotated"
        ) from exc
    return opened.decode("utf-8")
