"""Why can a stored payout account number not be read?

Read-only. **Never prints an account number**, decrypted or otherwise: it reports whether each row opens,
the scheme prefix, the ciphertext length and the last four that are stored in the clear anyway. That is
enough to tell a key mismatch from a corrupted row without putting a payable account number in a terminal
scrollback.

    .venv/bin/python scripts/diagnose_payout_crypto.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.domains.bug_hunt import payout_crypto  # noqa: E402
from src.domains.bug_hunt.db_models import BugHuntPayoutAccount  # noqa: E402
from src.shared.database import connect_db, disconnect_db, get_session_factory  # noqa: E402


def key_fingerprint() -> str:
    """A short, non-reversible handle for the *derived* key, so two environments can be compared.

    Fingerprints the derived subkey rather than `SECRET_KEY` itself, so this can be run and pasted
    without disclosing anything that would help derive it.
    """
    settings = get_settings()
    if not settings.SECRET_KEY:
        return "NO SECRET_KEY CONFIGURED"
    derived = payout_crypto._key(settings.SECRET_KEY)
    return hashlib.sha256(derived).hexdigest()[:16]


async def main() -> int:
    print(f"derived-key fingerprint: {key_fingerprint()}")
    print(
        f"SECRET_KEY is the dev default: {get_settings().SECRET_KEY == 'dev-secret-key-change-in-production'}"
    )

    # A round trip under the currently loaded key. If this fails, the problem is the code or the key,
    # not the stored rows.
    try:
        sealed = payout_crypto.encrypt("0123456789")
        assert payout_crypto.decrypt(sealed) == "0123456789"
        print("round trip under the current key: OK")
    except Exception as exc:
        print(f"round trip under the current key: FAILED ({type(exc).__name__}: {exc})")
        return 1

    await connect_db()
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(BugHuntPayoutAccount))).scalars().all()

    if not rows:
        print("\nNo payout accounts stored.")
        await disconnect_db()
        return 0

    print(f"\n{len(rows)} payout account row(s):")
    unreadable = 0
    for row in rows:
        stored = row.account_number_enc or ""
        scheme = stored.partition(".")[0] if stored else "(empty)"
        try:
            recovered = payout_crypto.decrypt(stored)
            # Consistency check that discloses nothing: the decrypted tail must match the plaintext
            # `last4` written beside it. A mismatch would mean the two columns describe different
            # accounts, which is worse than an unreadable row.
            agrees = recovered[-4:] == row.account_number_last4
            print(
                f"  {row.id}  user={row.user_id}  scheme={scheme}  "
                f"len={len(stored)}  last4={row.account_number_last4}  "
                f"decrypt=OK  last4-agrees={agrees}"
            )
        except payout_crypto.PayoutAccountUnreadable as exc:
            unreadable += 1
            print(
                f"  {row.id}  user={row.user_id}  scheme={scheme}  "
                f"len={len(stored)}  last4={row.account_number_last4}  "
                f"decrypt=UNREADABLE ({exc})"
            )

    await disconnect_db()

    if unreadable:
        print(
            f"\n{unreadable} row(s) cannot be read under the current key.\n"
            "If the round trip above passed, the code is fine and these rows were written under a "
            "different SECRET_KEY. Rotation is not supported by design: the recovery is that each "
            "tester re-enters their details, which is what the payout console already asks for."
        )
        return 1
    print("\nEvery stored account number opens under the current key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
