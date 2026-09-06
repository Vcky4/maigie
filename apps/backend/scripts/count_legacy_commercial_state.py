"""Count the commercial state that Phase 2a's `LEGACY_PLUS_TIERS` exists to protect.

`MAIGIE_PLUS_COMMERCIAL_PLAN.md` Phase 2b opens with this count, and every deletion revision 4
derived from "there are no paying subscribers" rests on it. The plan says so in those words, so it
should be answerable by running one command rather than by trusting a memory of a conversation.

**Strictly read-only.** `SELECT COUNT(*)` and one `SELECT ... GROUP BY`, no writes, no DDL, no
transaction that could leave a lock behind. Safe to run against production, which is the only place
the answer exists.

Usage:
    python scripts/count_legacy_commercial_state.py
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

# Each entry is a count that must be **zero** for revision 4's deletions to be safe.
#
# `PREMIUM_MONTHLY` is deliberately not among them. It is the one tier still on sale, so a non-zero
# count there is the product working rather than a blocker — an earlier version of this script
# checked it and reported a failure for a single hand-set row, which is the kind of false alarm that
# teaches people to ignore the script. The tier breakdown below reports it for context instead.
CHECKS: list[tuple[str, str]] = [
    (
        "Users on a RETIRED tier (PREMIUM_YEARLY / STUDY_CIRCLE_* / SQUAD_*)",
        'SELECT COUNT(*) FROM "User" WHERE tier IN '
        "('PREMIUM_YEARLY','STUDY_CIRCLE_MONTHLY','STUDY_CIRCLE_YEARLY',"
        "'SQUAD_MONTHLY','SQUAD_YEARLY')",
    ),
    (
        "Users with a Stripe subscription id",
        'SELECT COUNT(*) FROM "User" WHERE "stripeSubscriptionId" IS NOT NULL',
    ),
    (
        "Users with a Paystack subscription code",
        'SELECT COUNT(*) FROM "User" WHERE "paystackSubscriptionCode" IS NOT NULL',
    ),
    (
        "Users with a Google Play purchase token",
        'SELECT COUNT(*) FROM "User" WHERE "googlePlayPurchaseToken" IS NOT NULL',
    ),
    (
        "Users with a non-zero purchased credit balance",
        'SELECT COUNT(*) FROM "User" WHERE COALESCE("purchasedCreditsBalance", 0) <> 0',
    ),
    (
        "CreditPurchaseTransaction rows that COMPLETED (real money)",
        "SELECT COUNT(*) FROM \"CreditPurchaseTransaction\" WHERE status = 'completed'",
    ),
]

# Reported, not gated on. An abandoned checkout is not a customer: a `pending` row means someone
# opened a payment page and left, which is worth seeing but does not stop the table being dropped.
CONTEXT: list[tuple[str, str]] = [
    (
        "CreditPurchaseTransaction rows, any status",
        'SELECT COUNT(*) FROM "CreditPurchaseTransaction"',
    ),
]

TIER_BREAKDOWN = 'SELECT tier, COUNT(*) FROM "User" GROUP BY tier ORDER BY 2 DESC'


async def main() -> int:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, poolclass=None)
    findings: list[tuple[str, int]] = []
    try:
        async with engine.connect() as conn:
            for label, sql in CHECKS:
                try:
                    value = (await conn.execute(text(sql))).scalar_one()
                except Exception as e:  # noqa: BLE001 — a missing table is itself an answer
                    print(f"  {label}: could not read ({type(e).__name__})")
                    continue
                findings.append((label, int(value)))
                print(f"  {'ZERO ' if value == 0 else '>>>  '}{label}: {value}")

            print("\n  Context (not gated on):")
            for label, sql in CONTEXT:
                try:
                    value = (await conn.execute(text(sql))).scalar_one()
                    print(f"    {label}: {value}")
                except Exception as e:  # noqa: BLE001
                    print(f"    {label}: could not read ({type(e).__name__})")

            print("\n  Tier breakdown:")
            for tier, count in (await conn.execute(text(TIER_BREAKDOWN))).all():
                print(f"    {tier}: {count}")
    finally:
        await engine.dispose()

    nonzero = [(label, n) for label, n in findings if n != 0]
    print()
    if not nonzero:
        print("All gated counts zero. " + _describe_gate())
        print("Revision 4's deletions are safe; LEGACY_PLUS_TIERS can go.")
        return 0

    print("NOT all zero. The following are non-empty:")
    for label, n in nonzero:
        print(f"  - {label}: {n}")
    print("\nKeep LEGACY_PLUS_TIERS, keep the credit tables, and re-plan Phase 8.")
    return 1


def _describe_gate() -> str:
    """What a zero result licenses, in one place, so the plan and the script cannot drift."""
    return (
        "Zero on every check means: no learner holds a withdrawn tier, no payment provider "
        "relationship exists, and no credit pack was ever paid for."
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
