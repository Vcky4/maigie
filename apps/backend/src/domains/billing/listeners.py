"""Billing's domain event handlers.

**Why this file exists.** `identity.services.link_referral` has emitted `BillingEvents.REFERRAL_LINKED`
since the referral rewrite, with a comment saying "billing domain handles rewards". Nothing listened.
The event was dispatched into an empty handler list, and `bus.emit` returns silently when nothing is
registered, so there was no signal at any log level above debug.

The consequence, measured in production on 2026-09-14: 60 referral signups recorded between January and
April, and **zero** `PointsLedgerEntry` rows of any kind. Referrals could not earn, because the row the
qualification job walks was never written.

`points_service.qualify_referral` reads pending `signup` rows from `ReferralReward` — that table is
"kept but no longer written" as a *reward* record (`docs/MAIGIE_PLUS_COMMERCIAL_PLAN.md`, Decision O)
but is still the **input to qualification**. `track_referral_signup` is what writes it, and it had no
call sites at all.

Registered through `shared/events/registry.py`, which is the only thing that imports handler modules;
`tests/test_event_bus.py` fails if a module holding a `@listen` is missing from that tuple, so this
cannot silently become unreachable again the way the referral handler did.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging

from src.shared.events import BillingEvents, listen

logger = logging.getLogger(__name__)


@listen(BillingEvents.REFERRAL_LINKED)
async def record_referral_relationship(data: dict) -> None:
    """Write the `signup` row that makes a referral eligible to qualify later.

    **Grants nothing.** Under Decision O a referral earns 100 points on the referred learner's seventh
    distinct billable day, and this row is the precondition the nightly `billing.qualify_referrals` job
    walks. Granting here would pay for a signup, which is the farm-able version the seven-day gate
    exists to prevent.

    Idempotent through `track_referral_signup`, which checks for an existing row and then relies on the
    unique index rather than on the check. Both entry points converge here: `link_referral` for a code
    entered after signup, and `signup` for one that arrived with the account.

    Failures are logged and swallowed by the bus, which is correct: a lost referral row is recoverable
    (the code is still on the user, and this can be replayed) whereas failing the signup that emitted it
    is not.
    """
    user_id = data.get("user_id")
    referral_code = data.get("referral_code")
    if not user_id or not referral_code:
        logger.warning(
            "referral_linked event missing user_id or referral_code: keys=%s", sorted(data)
        )
        return

    from src.domains.billing.services.referral_rewards_service import track_referral_signup

    referrer_id = await track_referral_signup(str(user_id), str(referral_code))
    if referrer_id is None:
        # An unknown code or a self-referral. Not an error worth raising: the learner has an account
        # either way, and refusing here would be refusing after the fact.
        logger.info("referral_linked: code %s resolved to no referrer", referral_code)
