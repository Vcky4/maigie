"""Turning a balance into a Plus pass.

The cheap reward rail, and the one we would rather testers took: a pass costs us COGS rather than cash, and
it puts a tester inside the product they are testing. At the signed-off amounts one high-severity finding
(₦1,500) buys a 7-day pass outright — which is the offer the landing page leads with.

**Prices come from config, never from this module.** `PRICE_NGN_PLUS_PASS_*` is what Paystack charges for
the same product, so reading it here is what stops the programme quietly selling a pass at a price the
catalogue abandoned. The season's `passUpliftPercent` is the discount, on the season row so it can be tuned
between runs.

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
Between seasons the uplift falls back to the config default.

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
    #: What comes off the balance, after the season's uplift.
    charge_kobo: int
    uplift_percent: int
    duration_minutes: int
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


def uplift_percent(program: BugHuntProgram | None) -> int:
    """The discount for taking a pass instead of cash.

    From the season when one is open, and from the module default in the gap between seasons — because the
    rail stays open and a redemption then still needs a number. Clamped, so a misconfigured season cannot
    make a pass free.
    """
    raw = (
        program.pass_uplift_percent if program is not None else rewards.DEFAULT_PASS_UPLIFT_PERCENT
    )
    return max(0, min(90, int(raw)))


def charge_for(*, product_id: str, program: BugHuntProgram | None) -> int:
    """What a pass costs off the balance, in kobo.

    Integer arithmetic, floored — so a percentage that does not divide cleanly rounds in the **tester's**
    favour. The alternative is charging a kobo more than the arithmetic implies, which is a rounding rule
    nobody would defend out loud.
    """
    price = _catalogue_price_kobo(product_id)
    return price * (100 - uplift_percent(program)) // 100


async def options(*, user_id: str) -> list[RedemptionOption]:
    """What this tester can buy right now, cheapest first.

    `affordable` is computed here rather than left to the client so the wallet never offers a pass it will
    then refuse — the same reasoning `points_service.redeemable` follows.
    """
    from src.domains.billing.services import pass_service

    program = await program_service.current()
    balance = await ledger_service.balance(user_id)
    percent = uplift_percent(program)

    out: list[RedemptionOption] = []
    for product_id in REDEEMABLE_PRODUCTS:
        product = pass_service.PASS_PRODUCTS.get(product_id)
        if product is None:  # pragma: no cover - catalogue drift
            continue
        charge = charge_for(product_id=product_id, program=program)
        out.append(
            RedemptionOption(
                product_id=product_id,
                label=PRODUCT_LABELS.get(product_id, product_id),
                price_kobo=_catalogue_price_kobo(product_id),
                charge_kobo=charge,
                uplift_percent=percent,
                duration_minutes=product.duration_minutes,
                # The NGN allowance, not the global one. Passes are sized by market and every participant
                # in this programme is Nigerian, so inheriting the more generous global total would give
                # away something the product does not sell here — the same correction `points_service`
                # makes for a points-redeemed pass.
                units_allowance=(
                    pass_service.units_allowance_for_market(product_id, "ngn")
                    or product.units_allowance
                ),
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
    if charge <= 0:  # pragma: no cover - guarded by the uplift clamp and a CHECK
        raise ValidationError("That pass has no price configured.")

    # 1. Debit. Locked, and refused if the balance will not cover it — so nothing below runs on money the
    #    tester does not have.
    entry = await ledger_service.debit(
        user_id=user_id,
        kind="pass_redemption",
        amount_kobo=-charge,
        note=f"{PRODUCT_LABELS.get(product_id, product_id)} ({uplift_percent(program)}% off)",
    )

    # 2. Grant. Anything that goes wrong from here is recoverable, because the money is already accounted
    #    for and can be given back.
    try:
        granted = await pass_service.grant(
            user_id=user_id,
            product_id=product_id,
            purchase_id=None,
            source=PASS_SOURCE,
            units_allowance=pass_service.units_allowance_for_market(product_id, "ngn"),
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
        "bug_hunt: user=%s redeemed %s for %d kobo -> pass %s (inventory)",
        user_id,
        product_id,
        charge,
        granted.id,
    )
    return {
        "pass": granted,
        "entry": refreshed,
        "chargeKobo": charge,
        "balanceKobo": await ledger_service.balance(user_id),
    }
