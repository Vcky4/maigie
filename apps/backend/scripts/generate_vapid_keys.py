"""Generate a VAPID (RFC 8292) P-256 key pair for Web Push.

    python scripts/generate_vapid_keys.py

Run this once per deployment and store the output in that deployment's environment.

The pair is a long-lived *identity*, not a rotating secret. Every browser subscription is
created against a specific public key and is refused if a later push is signed by a
different one, so regenerating these keys silently breaks every subscription already in
the field. The only repair is for every learner to subscribe again. Staging and production
should hold different pairs; a single deployment should hold one pair forever.

The private key never leaves the server. The public key is not secret — the web client
needs it as its `applicationServerKey` — but it must match the private key exactly.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(raw: bytes) -> str:
    """Unpadded base64url, the encoding both the Push API and RFC 8292 use."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate() -> tuple[str, str]:
    """Return `(public_key, private_key)` as unpadded base64url strings."""
    key = ec.generate_private_key(ec.SECP256R1())
    # The raw 65-byte uncompressed point is what a browser expects; a PEM or DER
    # SubjectPublicKeyInfo wrapper here is the most common cause of a subscribe() failure.
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    # Fixed 32 bytes, big-endian: a short scalar must stay zero-padded or the key
    # reloads as a different one.
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public_raw), _b64url(private_raw)


def main() -> None:
    public_key, private_key = generate()
    print("# Add these to the environment of exactly one deployment.")
    print("# Keep the private key secret; publish nothing but the public key.")
    print(f"WEB_PUSH_VAPID_PUBLIC_KEY={public_key}")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={private_key}")
    print("WEB_PUSH_VAPID_SUBJECT=mailto:support@maigie.com")
    print()
    print("# The web client needs the same public key as its applicationServerKey:")
    print(f"VITE_WEB_PUSH_VAPID_PUBLIC_KEY={public_key}")


if __name__ == "__main__":
    main()
