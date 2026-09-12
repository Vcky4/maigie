"""Encryption at rest for a tester's bank account number.

A Nigerian account number plus an account name is enough to attempt a transfer *to* somebody, and enough
for a convincing impersonation of us in a message to them. It is the most sensitive value this domain
stores, and it exists only so that one person can read it once while making a payment.

**The scheme is the one this codebase already uses.** `notifications/subscription_crypto.py` derives an
AES-GCM key from `SECRET_KEY` with HKDF-SHA256 under a distinct `info` label, prefixes the ciphertext with
a scheme version, and collapses every decryption failure into one exception. This mirrors it deliberately
rather than introducing a second key-management story: an extra environment variable is an extra thing to
lose, and the earlier open question about "where does the key live" is answered by the fact that the
question was already answered here.

A distinct `info` label means this subkey cannot be used to forge anything else derived from the same root
— unsubscribe tokens, push subscription secrets — and vice versa.

**Where the failure handling diverges, and why.** An unreadable push subscription is discarded and the
learner is asked to subscribe again; the cost is one lost notification. An unreadable *account number* means
we cannot pay somebody who is owed money, and no amount of retrying fixes it. So:

- `decrypt` raises `PayoutAccountUnreadable` and callers surface it to staff as "ask the tester to re-enter
  their bank details" rather than as a 500.
- `accountNumberLast4` is stored in the clear alongside, so an unreadable row can still be *identified* —
  which is what lets a human match it against a bank statement or ask the right question.
- Reading a withdrawal never depends on this succeeding. The `…Snapshot` columns on `BugHuntWithdrawal`
  carry the bank name, account name and last four, so a historic payout stays reconcilable even if the
  ciphertext is gone.

**Rotation is not supported.** Rotating `SECRET_KEY` makes every stored account number unreadable, and the
recovery is that testers re-enter them. That is acceptable for a table with tens of rows and a manual payout
process; it would not be for anything larger, and `MultiFernet`-style key lists are the way out if this ever
grows. Recorded here rather than discovered later.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import base64
import os
import re
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import get_settings

#: Bumped only if the scheme changes. Stored as a prefix so a future scheme can be introduced without
#: guessing at the format of rows written by an older one.
_SCHEME = "v1"
_NONCE_BYTES = 12
_INFO = b"maigie.bug_hunt.payout.account_number.v1"

#: NUBAN: exactly ten digits. Validated before encryption, because a mistyped account number is discovered
#: either here or by a failed transfer to a stranger, and the first is very much better.
_NUBAN = re.compile(r"^\d{10}$")


class PayoutAccountUnreadable(Exception):
    """A stored account number cannot be decrypted, so nobody can be paid from this row."""


class InvalidAccountNumber(ValueError):
    """The supplied account number is not a ten-digit NUBAN."""


@lru_cache(maxsize=1)
def _key(secret_key: str) -> bytes:
    """Derive the 32-byte AES key. Cached, because HKDF would otherwise run on every read."""
    if not secret_key:
        # Never reachable in a configured deployment. Failing loudly beats encrypting under an empty key,
        # which would be indistinguishable from encryption that works.
        raise PayoutAccountUnreadable("SECRET_KEY is not configured")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
        secret_key.encode("utf-8")
    )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def normalise_account_number(value: str) -> str:
    """Strip spacing and check the shape. Raises `InvalidAccountNumber`.

    Nigerian account numbers are ten digits, and testers type them with spaces in them. Normalising here
    rather than at the route means the stored value, the `last4` and any future provider call all agree on
    one form.
    """
    cleaned = re.sub(r"[\s-]", "", value or "")
    if not _NUBAN.match(cleaned):
        raise InvalidAccountNumber("A Nigerian account number is ten digits.")
    return cleaned


def last4(account_number: str) -> str:
    """The four digits every screen shows. Plaintext by design — it identifies without disclosing."""
    return normalise_account_number(account_number)[-4:]


def encrypt(account_number: str) -> str:
    """Return `v1.<base64url(nonce||ciphertext)>` for a normalised account number."""
    cleaned = normalise_account_number(account_number)
    nonce = os.urandom(_NONCE_BYTES)
    key = _key(get_settings().SECRET_KEY)
    sealed = AESGCM(key).encrypt(nonce, cleaned.encode("utf-8"), _SCHEME.encode("ascii"))
    return f"{_SCHEME}.{_b64encode(nonce + sealed)}"


def decrypt(stored: str) -> str:
    """Recover an account number, raising `PayoutAccountUnreadable` if we cannot.

    Every failure mode collapses to one exception, because the caller's only sane response to a corrupted,
    truncated or wrong-key row is the same: tell staff to ask the tester for their details again. Nothing
    here retries, and nothing here falls back to a partial value — half an account number is worse than
    none, because somebody might try to use it.
    """
    if not stored:
        raise PayoutAccountUnreadable("account number is empty")
    scheme, _, payload = stored.partition(".")
    if scheme != _SCHEME or not payload:
        raise PayoutAccountUnreadable(f"unsupported account-number scheme {scheme!r}")
    try:
        raw = _b64decode(payload)
    except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise PayoutAccountUnreadable("account number is not base64url") from exc
    if len(raw) <= _NONCE_BYTES:
        raise PayoutAccountUnreadable("account number is truncated")
    key = _key(get_settings().SECRET_KEY)
    try:
        opened = AESGCM(key).decrypt(
            raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _SCHEME.encode("ascii")
        )
    except InvalidTag as exc:
        raise PayoutAccountUnreadable(
            "account number failed authentication; SECRET_KEY may have been rotated"
        ) from exc
    return opened.decode("utf-8")
