"""The live-voice balance: granted seconds, purchased seconds, and what spends them.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.3. Voice is the one metered thing in the product that does **not**
draw from the 5-hour unit window, and unbundling it is what makes the NGN ladder affordable. At 200
units/minute a voice minute costs 40× a Flash-Lite chat turn, so a single allowance covering both had
to be priced against the voice case and was spent almost entirely on the text case — the worst of
both, because the price had to be defensible against a cost almost nobody incurred.

**Two balances, and the distinction is the design.**

`voice_seconds_remaining` is *granted*. It belongs to whatever entitlement granted it, and it is
re-derived on read: when the entitlement's `voice_allowance_source_id` stops matching the value stored
on `User`, the granted balance resets to that source's allowance and the new id is written alongside.

`voice_seconds_purchased` is *bought*, via `plus_voice_30`, and never resets. A learner who paid $1.49
for 30 minutes owns them across a period boundary, and swallowing them at a renewal would be a refund
request with a good argument behind it. Spending draws granted seconds first, because those are the
ones that expire — using the perishable balance before the permanent one is the only order that does
not quietly destroy something the learner paid for.

**There is no sweep, and that is a deliberate departure from the plan.** §6.3's checklist said "the
sweep must zero the balance when its source pass or subscription period ends, or a pass's voice
minutes outlive the pass". Re-deriving on read is strictly stronger than a sweep: there is no job to
fail, no interval between a pass ending and a sweep noticing it, and no possibility of a learner
finding minutes that should have expired. It is also how the rest of this domain already behaves —
`credit_consumption_service.window_state` rolls the window over on read and
`entitlement_service._subscription_lapsed` expires a subscription on read — so it is the established
pattern rather than a new one.

**Reads do not write; only spending and topping up do.** `resolve` is pure and reports whether a
re-grant is *due*; `read_balance` persists one when it finds it. That split exists because
`GET /billing/voice/balance` is a read a client may poll, and a read that writes turns a polling
client into a write load. The consequence to know is that a learner who never starts a session never
has a row written, which is correct: the grant is not a fact about them until something spends it.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domains.billing.services import entitlement_service
from src.domains.identity.db_models import User
from src.domains.identity.repository import IdentityRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceBalance:
    """A learner's voice balance as of now, with any stale grant already refreshed."""

    available: bool
    """Whether voice is a capability this learner has at all.

    Distinct from `total_seconds == 0`, and the two need different copy. A free learner is not out of
    voice minutes; they do not have voice. Telling them they have used up an allowance of nothing is
    both confusing and a missed conversion moment — §6.3 makes voice the clearest thing a pass sells.
    """

    granted_seconds: int
    purchased_seconds: int
    source_id: str | None

    #: True when `resolve` found a stale grant, so the caller knows the stored row disagrees with
    #: this and a write is needed to persist it.
    refreshed: bool

    @property
    def total_seconds(self) -> int:
        return self.granted_seconds + self.purchased_seconds

    @property
    def total_minutes(self) -> int:
        """Whole minutes, rounded **down**, for display.

        Down because a learner told "1 minute left" who gets 50 seconds has been misled, whereas one
        told "0 minutes left" who gets 50 seconds has been under-promised. The seconds are what the
        meter spends; minutes are only ever what a human reads.
        """
        return self.total_seconds // 60


def resolve(user: User, entitlement: entitlement_service.Entitlement) -> VoiceBalance:
    """The balance implied by this row and this entitlement, re-granting a stale source.

    Pure: computes, never writes. See the module docstring for why a read that writes would be the
    wrong shape for a pollable endpoint.
    """
    stored_source = user.voice_allowance_source_id
    purchased = max(0, user.voice_seconds_purchased or 0)

    if not entitlement.voice_available:
        # No voice on this entitlement. Purchased seconds are still reported, and still spendable:
        # a learner who bought minutes and then let their subscription lapse owns those minutes, and
        # the alternative is confiscating something they paid for because they stopped paying for
        # something else. So `available` follows the *balance* here, not the tier.
        return VoiceBalance(
            available=purchased > 0,
            granted_seconds=0,
            purchased_seconds=purchased,
            source_id=None,
            # A lapsed learner holding a stale granted balance needs it cleared, which is a write.
            refreshed=bool(stored_source) or (user.voice_seconds_remaining or 0) > 0,
        )

    if stored_source == entitlement.voice_allowance_source_id:
        return VoiceBalance(
            available=True,
            granted_seconds=max(0, user.voice_seconds_remaining or 0),
            purchased_seconds=purchased,
            source_id=stored_source,
            refreshed=False,
        )

    # The source changed: a first grant, a renewal, a new pass, or a trial that restarted. Reset to
    # the full allowance rather than adding to what is there — a renewal grants a period's minutes, it
    # does not accumulate them, and 60 minutes a month that rolled over would be an unbounded balance
    # for a dormant subscriber.
    return VoiceBalance(
        available=True,
        granted_seconds=entitlement.voice_seconds_included,
        purchased_seconds=purchased,
        source_id=entitlement.voice_allowance_source_id,
        refreshed=True,
    )


async def read_balance(user_id: str) -> VoiceBalance:
    """Resolve the balance, persisting a re-grant if one was due."""
    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if user is None:
        return VoiceBalance(
            available=False,
            granted_seconds=0,
            purchased_seconds=0,
            source_id=None,
            refreshed=False,
        )

    entitlement = await entitlement_service.resolve(user_id)
    balance = resolve(user, entitlement)
    if balance.refreshed:
        await _persist(repo, user_id, balance)
    return balance


async def spend(user_id: str, seconds: int) -> VoiceBalance:
    """Take `seconds` off the balance and return what is left.

    Granted seconds first, then purchased. Spends what is there and no more — the caller is a live
    relay that has already used the provider minutes, so there is nothing left to refuse and
    over-spending would write a negative balance that the next grant would silently forgive.

    Charging *after* the time has been used is the same posture as `record_units`: the money is spent
    and the learner has had the conversation. What stops an exhausted learner continuing is the
    caller noticing `total_seconds == 0` and ending the session, not this function raising.
    """
    if seconds <= 0:
        return await read_balance(user_id)

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if user is None:
        logger.error("voice: cannot spend %ds, user %s not found", seconds, user_id)
        return VoiceBalance(
            available=False,
            granted_seconds=0,
            purchased_seconds=0,
            source_id=None,
            refreshed=False,
        )

    entitlement = await entitlement_service.resolve(user_id)
    balance = resolve(user, entitlement)

    from_granted = min(balance.granted_seconds, seconds)
    from_purchased = min(balance.purchased_seconds, seconds - from_granted)

    spent = VoiceBalance(
        available=balance.available,
        granted_seconds=balance.granted_seconds - from_granted,
        purchased_seconds=balance.purchased_seconds - from_purchased,
        source_id=balance.source_id,
        refreshed=True,
    )
    await _persist(repo, user_id, spent)

    if from_granted + from_purchased < seconds:
        # Logged rather than raised. A voice session can overrun its balance by up to one flush
        # interval, exactly as a usage window can be exceeded by one operation in flight, and the
        # honest response is to record the shortfall rather than to pretend it did not happen.
        logger.info(
            "voice: user=%s spent %ds of %ds requested — balance was short by %ds",
            user_id,
            from_granted + from_purchased,
            seconds,
            seconds - (from_granted + from_purchased),
        )
    else:
        logger.info(
            "voice: user=%s spent %ds (granted=%d purchased=%d remaining=%ds)",
            user_id,
            seconds,
            from_granted,
            from_purchased,
            spent.total_seconds,
        )
    return spent


async def add_purchased(user_id: str, seconds: int) -> VoiceBalance:
    """Add bought seconds to the balance that never expires.

    The fulfilment half of `plus_voice_30`. Phase 5 builds the one-time rail that calls it; it exists
    now because the counter it writes to exists now, and because a top-up that lands in the *granted*
    balance by mistake would be silently deleted at the next renewal.

    Additive rather than idempotent on purpose: a learner may buy the pack repeatedly, and that is the
    product working. Phase 5 owns not fulfilling the same *purchase* twice, which is a property of the
    purchase record rather than of this counter.
    """
    if seconds <= 0:
        return await read_balance(user_id)

    repo = IdentityRepository()
    user = await repo.find_by_id(user_id)
    if user is None:
        logger.error("voice: cannot credit %ds, user %s not found", seconds, user_id)
        return VoiceBalance(
            available=False,
            granted_seconds=0,
            purchased_seconds=0,
            source_id=None,
            refreshed=False,
        )

    entitlement = await entitlement_service.resolve(user_id)
    balance = resolve(user, entitlement)
    topped_up = VoiceBalance(
        # A top-up makes voice available to a learner who had none, which is the case Decision R
        # exists for: a subscriber out of minutes cannot activate a pass, so this is the only thing
        # they can buy.
        available=True,
        granted_seconds=balance.granted_seconds,
        purchased_seconds=balance.purchased_seconds + seconds,
        source_id=balance.source_id,
        refreshed=True,
    )
    await _persist(repo, user_id, topped_up)
    logger.info(
        "voice: user=%s credited %ds purchased (total now %ds)",
        user_id,
        seconds,
        topped_up.total_seconds,
    )
    return topped_up


async def _persist(repo: IdentityRepository, user_id: str, balance: VoiceBalance) -> None:
    """Write the three columns. Failures are logged, never raised.

    Same reasoning as `record_units`: every failure mode here — a lost connection, a vanished row, a
    serialisation conflict — is a reason to under-charge rather than a reason to fail a caller who is
    mid-conversation with a learner.
    """
    try:
        await repo.update(
            user_id,
            {
                "voiceSecondsRemaining": max(0, balance.granted_seconds),
                "voiceSecondsPurchased": max(0, balance.purchased_seconds),
                "voiceAllowanceSourceId": balance.source_id,
            },
        )
    except Exception:
        logger.exception("voice: failed to persist balance for user=%s", user_id)
