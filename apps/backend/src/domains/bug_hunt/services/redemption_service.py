"""Turning a balance into a Plus pass.

The cheap reward rail, and the one we would rather testers took: a pass costs us COGS rather than cash, and
it puts a tester inside the product they are testing. At the signed-off amounts one high-severity finding
(₦1,500) buys a 7-day pass outright — which is the offer the landing page leads with.

**Prices come from config, never from this module.** `PRICE_NGN_PLUS_PASS_*` is what Paystack charges for
the same product, so reading it here is what stops the programme quietly selling a pass at a price the
catalogue abandoned. A tester pays that price **in full**.

**The premium is paid in duration, not in price**, and that is the whole shape of this rail. The season's
`passBonusPercent` grants *more pass* rather than charging *less balance*: at 25, ₦1,500 buys a 9-day pass
instead of a 7-day one, and the ₦1,500 is fully consumed.

It was a price discount until migration `084`. The problem was arithmetic. A ₦1,500 balance buying a
₦1,125 pass hands over the pass — which costs us compute — and **still owes ₦375**, which costs us naira.
Every discounted redemption converted a compute cost into a cash cost, and left a stub below the ₦1,000
withdrawal minimum that reads to a tester as money they earned and cannot reach. A premium of some kind is
still necessary, because cash is fungible and a pass is not, so at parity a rational tester always takes
the cash and the programme pays real money on every accepted finding. Paying it in duration extinguishes
the whole balance, gives the tester more than anyone paying Paystack, and costs us the cheapest thing we
have.

**The bonus scales the allowance as well as the clock.** A 9-day pass carrying seven days' worth of units
is a pass that stops working on day seven, which would make the bonus a promise about the calendar that
the product does not keep.

**The order of operations is the whole design.**

    debit → grant → annotate, and reverse the debit if the grant fails.

Granting first and debiting second is the version that hands out free passes: if the debit is refused —
insufficient balance, a lost race, a dropped connection — the pass already exists and the tester keeps it.
Debiting first can only fail the other way, which is a reversal we can write. `points_service` grants first
on the argument that "if the grant fails, no points have been spent", and that is true; but it also means
its balance check and its spend are not in the same transaction, which is the race this domain refuses to
inherit (see `ledger_service.debit`).

**Available with no season open.** An earned balance is permanent and the gap between seasons is where most
of the year is spent, so a redemption rail that closed with the season would strand money for months.
Between seasons the bonus falls back to the config default.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, update

from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, ValidationError

from .. import rewards
from ..db_models import BugHuntLedgerEntry, BugHuntProgram
from . import ledger_service, program_service

logger = logging.getLogger(__name__)

#: The source recorded on a `PlusPass` granted this way, beside the existing `purchase`, `points` and
#: `admin_comp`. Not cosmetic: it is how billing can tell what a pass cost us, and it is what makes a
#: programme-granted pass distinguishable from a bought one in any later revenue read.
PASS_SOURCE = "bug_hunt"

#: Every pass a Bug Hunt balance can buy. Deliberately the whole pass catalogue and nothing else —
#: `plus_voice_30` is absent because a voice pack is a balance on `User` rather than a pass, and granting
#: one through this rail would give entitlement the pack never sold (billing Decision R).
#:
#: Order is cheapest first, which is the order the wallet screen lists them in: a tester with ₦525 should
#: see what they *can* afford before what they cannot.
REDEEMABLE_PRODUCTS: tuple[str, ...] = ("plus_pass_5h", "plus_pass_7d", "plus_pass_term")

#: Human labels. Held here rather than fetched from the billing catalogue because that endpoint describes
#: products *for sale* with prices in the buyer's currency, and this screen is not a shop — the tester is
#: spending a balance, not choosing a purchase.
PRODUCT_LABELS: dict[str, str] = {
    "plus_pass_5h": "5-hour pass",
    "plus_pass_7d": "7-day pass",
    "plus_pass_term": "4-month term pass",
}


@dataclass(frozen=True)
class RedemptionOption:
    """One pass, and what it costs this tester right now."""

    product_id: str
    label: str
    #: The catalogue price in kobo — what somebody paying Paystack would be charged.
    price_kobo: int
    #: What comes off the balance. Equal to `price_kobo` since `084`: the premium is in what is
    #: granted, not in what is charged. Kept as its own field because the client renders both, and
    #: because a future season could reintroduce a discount without a shape change here.
    charge_kobo: int
    bonus_percent: int
    #: What the catalogue gives for this price, and what a Bug Hunt balance gets. The client shows both,
    #: since "9 days instead of 7" is the offer and a lone "9 days" is just a number.
    base_duration_minutes: int
    duration_minutes: int
    base_units_allowance: int
    units_allowance: int
    affordable: bool


def _catalogue_price_kobo(product_id: str) -> int:
    """The NGN price of a pass, in kobo, from config.

    One place, and it is the same constant the Paystack charge uses. A second copy of these numbers in this
    domain would be a second thing to forget when the catalogue moves.
    """
    from src.config import get_settings

    settings = get_settings()
    prices = {
        "plus_pass_5h": settings.PRICE_NGN_PLUS_PASS_5H,
        "plus_pass_7d": settings.PRICE_NGN_PLUS_PASS_7D,
        "plus_pass_term": settings.PRICE_NGN_PLUS_PASS_TERM,
    }
    price = prices.get(product_id)
    if price is None:
        raise ValidationError(f"{product_id} is not a pass a Bug Hunt balance can buy.")
    return int(price)


def bonus_percent(program: BugHuntProgram | None) -> int:
    """How much *more* pass a balance buys, as a percentage.

    From the season when one is open, and from the module default in the gap between seasons — because the
    rail stays open and a redemption then still needs a number. Clamped at 100, a doubled pass: beyond
    that the allowance stops resembling the product being tested.
    """
    raw = program.pass_bonus_percent if program is not None else rewards.DEFAULT_PASS_BONUS_PERCENT
    return max(0, min(100, int(raw)))


def charge_for(*, product_id: str, program: BugHuntProgram | None) -> int:
    """What a pass costs off the balance, in kobo: the full catalogue price.

    Takes `program` it does not read, deliberately. Every caller has one, the signature is what a future
    season-varying charge would need, and the alternative is changing five call sites the day somebody
    wants a discount back. The premium lives in `granted_duration_minutes` instead.
    """
    return _catalogue_price_kobo(product_id)


def granted_duration_minutes(base_minutes: int, percent: int) -> int:
    """The clock a Bug Hunt pass actually runs for.

    Rounded **up, and to the unit the pass is sold in**. A 7-day pass at 25% is 8.75 days, which becomes
    9 days; a 5-hour pass becomes 7 hours. Rounding to a smaller unit is arithmetically tidier and
    produces "8 days 18 hours", which is not a number a tester can repeat to a friend or that we can put
    in an email subject line. The offer has to be sayable.

    Two consequences, both accepted deliberately:

    *Short passes get a larger effective bonus.* 5 hours to 7 is 40%, not 25%, because an hour is a coarse
    unit at that scale. The overshoot costs minutes of compute on the cheapest pass we grant, and the
    alternative is a 6-hour-15-minute pass.

    *Rounding is always in the tester's favour.* Rounding down would mean advertising a bonus and then
    granting slightly less than it, which is the kind of small dishonesty that is expensive to be caught
    doing on a programme whose entire proposition is that we pay what we say.
    """
    if percent <= 0:
        return base_minutes

    bonused = base_minutes * (100 + percent) / 100
    day = 24 * 60
    # The unit the product is sold in, inferred from the base rather than passed in, so a new pass length
    # in the catalogue needs no change here.
    unit = day if base_minutes % day == 0 else 60
    units = -(-int(bonused) // unit)  # ceil, in integer arithmetic
    return units * unit


def granted_units(base_units: int, percent: int) -> int:
    """The allowance, scaled by the same bonus.

    Without this a 9-day pass carries seven days of units and stops working on day seven, which makes the
    duration bonus a promise about the calendar that the product does not keep. Floored, because units are
    discrete and half a unit is not a thing anybody can spend.
    """
    return base_units * (100 + percent) // 100


async def options(*, user_id: str) -> list[RedemptionOption]:
    """What this tester can buy right now, cheapest first.

    `affordable` is computed here rather than left to the client so the wallet never offers a pass it will
    then refuse — the same reasoning `points_service.redeemable` follows.
    """
    from src.domains.billing.services import pass_service

    program = await program_service.current()
    balance = await ledger_service.balance(user_id)
    percent = bonus_percent(program)

    out: list[RedemptionOption] = []
    for product_id in REDEEMABLE_PRODUCTS:
        product = pass_service.PASS_PRODUCTS.get(product_id)
        if product is None:  # pragma: no cover - catalogue drift
            continue
        charge = charge_for(product_id=product_id, program=program)
        # The NGN allowance, not the global one. Passes are sized by market and every participant in this
        # programme is Nigerian, so inheriting the more generous global total would give away something
        # the product does not sell here — the same correction `points_service` makes for a
        # points-redeemed pass. The bonus is applied *on top of* the market figure, not the global one.
        base_units = (
            pass_service.units_allowance_for_market(product_id, "ngn") or product.units_allowance
        )
        out.append(
            RedemptionOption(
                product_id=product_id,
                label=PRODUCT_LABELS.get(product_id, product_id),
                price_kobo=_catalogue_price_kobo(product_id),
                charge_kobo=charge,
                bonus_percent=percent,
                base_duration_minutes=product.duration_minutes,
                duration_minutes=granted_duration_minutes(product.duration_minutes, percent),
                base_units_allowance=base_units,
                units_allowance=granted_units(base_units, percent),
                affordable=balance >= charge,
            )
        )
    return out


async def redeem(*, user_id: str, product_id: str) -> dict:
    """Spend a balance on a Plus pass. Returns the pass and the entry that paid for it.

    Debit, grant, annotate — and reverse the debit if the grant raises. See the module docstring for why
    that order and not the other one.

    The pass lands in **inventory**, not active: the tester starts its clock when they want it, exactly as
    with a bought pass. Activating it here would burn a 5-hour pass at the moment they redeemed it, which is
    the product mis-sold on a technicality — and `pass_service.activate` has refusals of its own (an active
    subscription or a running trial makes a pass redundant) that belong to the learner, not to us.
    """
    from src.domains.billing.services import pass_service

    if product_id not in REDEEMABLE_PRODUCTS:
        raise ValidationError(f"A Bug Hunt balance buys a pass: {', '.join(REDEEMABLE_PRODUCTS)}.")

    program = await program_service.current()
    charge = charge_for(product_id=product_id, program=program)
    if charge <= 0:  # pragma: no cover - guarded by the catalogue prices being positive
        raise ValidationError("That pass has no price configured.")

    percent = bonus_percent(program)
    product = pass_service.PASS_PRODUCTS.get(product_id)
    if product is None:  # pragma: no cover - guarded by REDEEMABLE_PRODUCTS above
        raise ValidationError(f"{product_id} is not a pass that can be granted.")
    base_units = (
        pass_service.units_allowance_for_market(product_id, "ngn") or product.units_allowance
    )
    duration = granted_duration_minutes(product.duration_minutes, percent)
    units = granted_units(base_units, percent)

    # 1. Debit. Locked, and refused if the balance will not cover it — so nothing below runs on money the
    #    tester does not have.
    entry = await ledger_service.debit(
        user_id=user_id,
        kind="pass_redemption",
        amount_kobo=-charge,
        # The note is what the tester reads on their own ledger, so it records the premium they were
        # given rather than a discount they were not: they paid full price and got a bigger pass.
        note=(
            f"{PRODUCT_LABELS.get(product_id, product_id)}"
            + (f" (+{percent}% Bug Hunt bonus)" if percent > 0 else "")
        ),
    )

    # 2. Grant. Anything that goes wrong from here is recoverable, because the money is already accounted
    #    for and can be given back.
    try:
        # Both overrides are snapshotted on the `PlusPass` row by `grant`, which is what makes the
        # bonus a durable property of the pass rather than something recomputed later from a season that
        # may since have changed its mind.
        granted = await pass_service.grant(
            user_id=user_id,
            product_id=product_id,
            purchase_id=None,
            source=PASS_SOURCE,
            duration_minutes=duration,
            units_allowance=units,
        )
    except Exception as error:
        # The compensating credit. Written before the failure is re-raised, so a tester never loses a
        # balance to an error on our side — and written as a new row rather than by deleting the debit,
        # so the history shows what was attempted.
        await ledger_service.reverse(
            entry=entry,
            note=f"Refunded: the {PRODUCT_LABELS.get(product_id, product_id)} could not be granted.",
        )
        logger.error(
            "bug_hunt: pass grant failed for user=%s product=%s, debit %s reversed: %s",
            user_id,
            product_id,
            entry.id,
            error,
        )
        raise ConflictError(
            message="We could not issue that pass. Your balance has not been touched.",
            detail=str(error),
            code="PASS_GRANT_FAILED",
        ) from error

    # 3. Annotate the debit with the pass it bought.
    #
    #    This is a single-column update on an append-only table, and it is worth being explicit that it does
    #    not violate the invariant: the invariant is about **amounts**, which are never altered, and the
    #    alternative is a ledger line that says "pass redemption" without saying which pass. The id cannot be
    #    known before the grant, and granting first is the failure mode this whole ordering avoids.
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(BugHuntLedgerEntry)
            .where(BugHuntLedgerEntry.id == entry.id)
            .values(pass_id=granted.id)
        )
        await session.commit()
        refreshed = (
            await session.execute(
                select(BugHuntLedgerEntry).where(BugHuntLedgerEntry.id == entry.id)
            )
        ).scalar_one()

    logger.info(
        "bug_hunt: user=%s redeemed %s for %d kobo (+%d%% bonus: %d min, %d units) "
        "-> pass %s (inventory)",
        user_id,
        product_id,
        charge,
        percent,
        duration,
        units,
        granted.id,
    )
    return {
        "pass": granted,
        "entry": refreshed,
        "chargeKobo": charge,
        "balanceKobo": await ledger_service.balance(user_id),
    }
