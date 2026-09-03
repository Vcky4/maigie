"""Passes: grant into inventory, activate once, end on the clock or on the allowance.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Decisions A, C, D and E. `entitlement_service` has known how to
*resolve* a pass since Phase 2 — `_compose`'s pass branch was written and tested against a shape with
no table behind it, and `_read_active_pass` was a named seam returning `None`. This is what fills it.

**Decision A: a pass is inventory until the learner activates it.** Buying writes a row with
`status='inventory'` and no expiry; `activate` sets the clock. That is the product rather than an
implementation detail — a $0.99 five-hour pass bought on Tuesday and spent on Saturday's revision
session is worth buying, and one whose clock starts at the checkout screen is not.

**Decision D: activating while Plus is already active is refused, not queued.** `409 PASS_REDUNDANT`
against a subscription, a trial or another active pass. The learner keeps the pass; a refused activation
consumes nothing. Queuing would make "how long am I Plus for" unanswerable at a glance and turn expiry
ordering into a support queue.

**Decision E: two ways to end.** The wall clock, and the allowance. The second is what stops a pass
being a product that loses money the more it is used, and it needs `PlusPass.unitsUsed` because the
window and month counters on `User` both reset while a pass total must not.

**The one-active invariant belongs to the database.** A partial unique index on `(userId) WHERE
status='active'` means two concurrent activations produce one winner and one `IntegrityError`, which
`activate` turns into the same `409` a sequential redundant activation gets. There is a pre-check too,
but it is there to give a *good* error in the common case, not to hold the invariant — a pre-check alone
loses the race it exists to prevent.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domains.billing.db_models import PlusPass
from src.domains.billing.services import entitlement_service
from src.domains.identity.repository import IdentityRepository
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, NotFoundError

logger = logging.getLogger(__name__)


# ===========================================================================
# The products
# ===========================================================================


@dataclass(frozen=True)
class PassProduct:
    """What a pass id is worth, at the moment it is sold.

    Both figures are **snapshotted onto the row** by `grant`, never read back from here at use time.
    Re-pricing or re-timing a product must not change a pass already sold, and it also lets a market
    carry its own allowance without a branch in every reader.
    """

    duration_minutes: int
    units_allowance: int


#: Duration and total allowance per pass product, in **USD-market** terms.
#:
#: §6.8 gives the launch market smaller allowances — 1 800 units on the 5-hour pass against 2 000 here,
#: 4 500 on the 7-day against 10 000 — because NGN prices are set from what Nigerians pay rather than by
#: FX, and Decision Q derives the allowance from the price rather than the reverse. Those are not in this
#: table: `grant` takes an override, so the NGN rail passes its own figures and the snapshot on the row
#: is what any reader sees. A second table keyed by currency would be a second place to forget.
#:
#: **Two figures in §8 and §6.3 are stale and this table follows §6.3 and §6.8 instead.** §8's `PlusPass`
#: row still says `unitsAllowance` is "3 000 | 10 000", but revision 10 moved the 5-hour pass to 2 000
#: when the monthly went to $9.99 — leaving it at 3 000 would make a $0.99 pass cheaper per unit than
#: the $9.99 subscription, which is the inverted ladder that revision existed to fix. And §6.3's table
#: says the Term Pass gets "20 000/month", which over four months is 80 000 units — $8.00 of COGS
#: against $3.65 of net revenue, a loss. §6.8's margin table, which is the one the NGN-only product
#: actually lives in, says 20 000 units total at a 45% floor margin. That is the coherent reading and it
#: is the one used here.
PASS_PRODUCTS: dict[str, PassProduct] = {
    "plus_pass_5h": PassProduct(duration_minutes=5 * 60, units_allowance=2_000),
    "plus_pass_7d": PassProduct(duration_minutes=7 * 24 * 60, units_allowance=10_000),
    # Four months, as 120 days. The academic term this is priced against is not a calendar quarter, and
    # a fixed day count is what makes the expiry a date the learner can be told on the purchase screen.
    "plus_pass_term": PassProduct(duration_minutes=120 * 24 * 60, units_allowance=20_000),
}

#: Statuses a pass can hold. `inventory` → `active` → `consumed`, or `refunded` from either.
STATUS_INVENTORY = "inventory"
STATUS_ACTIVE = "active"
STATUS_CONSUMED = "consumed"
STATUS_REFUNDED = "refunded"

#: Why a pass ended. Kept apart from `status` because "your five hours are up" and "you've used this
#: pass's allowance" are different facts that need different copy.
REASON_EXPIRED = "expired"
REASON_EXHAUSTED = "exhausted"
REASON_REFUND = "refund"


def is_pass_product(product_id: str) -> bool:
    """Whether this catalogue id is a pass at all.

    `plus_voice_30` is deliberately absent: a voice pack is a balance on `User`, not a pass, and a
    `PlusPass` row for one would grant entitlement the pack did not sell (Decision R). Phase 5's rail
    routes on this rather than on a string comparison it can get wrong.
    """
    return product_id in PASS_PRODUCTS


# ===========================================================================
# Grant
# ===========================================================================


async def grant(
    *,
    user_id: str,
    product_id: str,
    purchase_id: str | None = None,
    source: str = "purchase",
    duration_minutes: int | None = None,
    units_allowance: int | None = None,
) -> PlusPass:
    """Put a pass in the learner's inventory. **Does not start its clock.**

    Called after a purchase is verified and persisted, in that order (Decision G) — and by Phase 4b's
    points redemption with `source="points"` and no `purchase_id`, since nothing was purchased.

    `duration_minutes` and `units_allowance` override the product table so a market can sell its own
    allowance (§6.8) without this module knowing about currencies. Whatever is passed is snapshotted on
    the row and is what every later reader sees.
    """
    product = PASS_PRODUCTS.get(product_id)
    if product is None:
        # Not a `ValidationError` about a bad id, because the caller is a purchase rail rather than a
        # learner: reaching here means a product was sold that cannot be granted, which is a defect
        # worth naming rather than a request worth rejecting.
        raise ConflictError(
            message="That product cannot be granted as a pass.",
            detail=f"product_id={product_id} is not in PASS_PRODUCTS",
            code="NOT_A_PASS_PRODUCT",
        )

    row = PlusPass(
        user_id=user_id,
        product_id=product_id,
        duration_minutes=duration_minutes or product.duration_minutes,
        units_allowance=units_allowance or product.units_allowance,
        status=STATUS_INVENTORY,
        purchase_id=purchase_id,
        source=source,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)

    logger.info(
        "pass: granted %s to user=%s as %s (source=%s, %d units, %d minutes)",
        product_id,
        user_id,
        row.id,
        source,
        row.units_allowance,
        row.duration_minutes,
    )
    return row


# ===========================================================================
# Activate
# ===========================================================================


async def activate(*, user_id: str, pass_id: str) -> PlusPass:
    """Start a pass's clock, and reset the usage window with it.

    **Decision D's refusals come first, and the learner keeps the pass through all of them.** An active
    subscription, a running trial or another active pass each answer `409 PASS_REDUNDANT` — with the
    reason in `detail`, because a client that wants to explain *why* needs to know which of the three it
    was, and "you already have Plus" reads differently from "you already have a pass running".

    **Activation resets the usage window** (Decision E). Without it, a five-hour pass activated at minute
    290 of a Free window would deliver ten minutes of allowance and then a wall — the product mis-sold on
    a technicality.
    """
    entitlement = await entitlement_service.resolve(user_id)
    if entitlement.tier == "plus":
        # Named per source rather than as one message. Decision D refuses all three, but they are three
        # different situations for the learner and only one of them is their own doing.
        reasons = {
            "subscription": "You already have Maigie Plus, so there is nothing for a pass to add.",
            "trial": "Your Plus trial is still running, so there is nothing for a pass to add.",
            "pass": "You already have a pass running. It will keep its remaining time.",
        }
        raise ConflictError(
            message=reasons.get(entitlement.source, "You already have Maigie Plus."),
            detail=f"active_source={entitlement.source}",
            code="PASS_REDUNDANT",
        )

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(PlusPass).where(PlusPass.id == pass_id, PlusPass.user_id == user_id)
            )
        ).scalar_one_or_none()
        # Scoped to the learner in the query rather than checked afterwards, so another learner's pass
        # id is indistinguishable from one that does not exist. A 404 that confirms a row exists is a
        # small enumeration oracle.
        if row is None:
            raise NotFoundError("Pass", pass_id)
        if row.status != STATUS_INVENTORY:
            raise ConflictError(
                message=(
                    "That pass has already been used."
                    if row.status in (STATUS_ACTIVE, STATUS_CONSUMED)
                    else "That pass is no longer available."
                ),
                detail=f"status={row.status}",
                code="PASS_NOT_IN_INVENTORY",
            )

        row.status = STATUS_ACTIVE
        row.activated_at = now
        row.expires_at = now + timedelta(minutes=row.duration_minutes)
        try:
            await session.commit()
        except IntegrityError:
            # The partial unique index fired: another activation won the race between `resolve` above
            # and this commit. Same answer as the sequential case, because it is the same rule — and
            # this is the path that actually holds the invariant, the pre-check only making the common
            # case explain itself.
            await session.rollback()
            logger.info(
                "pass: activation of %s for user=%s lost the one-active race", pass_id, user_id
            )
            raise ConflictError(
                message="You already have a pass running. It will keep its remaining time.",
                detail="active_source=pass, concurrent",
                code="PASS_REDUNDANT",
            ) from None
        await session.refresh(row)

    # Cached onto `User` (Decision C) and the window reset, in one write. The window reset is not
    # bookkeeping — it is what makes the five hours the learner was sold five usable hours.
    await IdentityRepository().update(
        user_id,
        {
            "activePlusPassId": row.id,
            "activePlusPassExpiresAt": row.expires_at,
            "usageWindowStartedAt": now,
            "usageWindowUnitsUsed": 0,
        },
    )
    # The entitlement was resolved at the top of this function and is memoised for the rest of the
    # request. Without this, anything gated after activation would still see the learner as free —
    # the same defect `trial_service.start_trial` fixes for trials, and for the same reason.
    entitlement_service.invalidate(user_id)

    logger.info(
        "pass: user=%s activated %s (%s) until %s",
        user_id,
        row.id,
        row.product_id,
        row.expires_at,
    )
    return row


# ===========================================================================
# Read
# ===========================================================================


async def list_passes(user_id: str) -> list[PlusPass]:
    """Every pass the learner holds, newest first.

    **This is also how iOS "restores" a consumable.** StoreKit does not return finished consumables from
    `Transaction.currentEntitlements`, so a reinstalled app cannot recover a purchased-but-unactivated
    pass from the device (Decision G). Restore is a read of this endpoint, not a StoreKit operation.

    Returns consumed and refunded passes too. A learner asking "what happened to my pass" is asking
    about one that ended, and an inventory that hides them cannot answer.
    """
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PlusPass)
                    .where(PlusPass.user_id == user_id)
                    .order_by(PlusPass.created_at.desc())
                )
            )
            .scalars()
            .all()
        )


# ===========================================================================
# End
# ===========================================================================


async def expire(*, pass_id: str, reason: str) -> PlusPass | None:
    """End an active pass and clear the cached columns. Idempotent.

    `reason` is `expired` when the wall clock ran out and `exhausted` when the allowance did — Decision
    E's two endings, kept distinct because they need different copy.

    **Clears the cached `User` columns only if they still point at this pass.** A blind clear would wipe
    a *different* pass the learner activated in the meantime, which is the failure mode a denormalised
    column invites and the reason it has exactly one writer.
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(PlusPass).where(PlusPass.id == pass_id))
        ).scalar_one_or_none()
        if row is None or row.status != STATUS_ACTIVE:
            # Idempotent by design: the sweep runs every five minutes and lazy expiry has already made
            # the pass free on read, so re-ending an ended pass has to be a no-op rather than an error.
            return row
        row.status = STATUS_CONSUMED
        row.ended_reason = reason
        await session.commit()
        await session.refresh(row)

    repo = IdentityRepository()
    user = await repo.find_by_id(row.user_id)
    if user is not None and user.active_plus_pass_id == row.id:
        await repo.update(row.user_id, {"activePlusPassId": None, "activePlusPassExpiresAt": None})
    entitlement_service.invalidate(row.user_id)

    logger.info("pass: %s ended for user=%s (%s)", row.id, row.user_id, reason)
    return row


async def revoke(*, purchase_id: str) -> list[PlusPass]:
    """Mark every pass from a refunded purchase as refunded, ending it if it was running.

    Apple and Google decide refunds unilaterally and neither asks first, so this is reached from a
    webhook rather than from a request. A pass already consumed still becomes `refunded`: the question
    the status answers afterwards is "what happened to the money", and "it ended and was then refunded"
    is a different answer from "it ended".
    """
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            (await session.execute(select(PlusPass).where(PlusPass.purchase_id == purchase_id)))
            .scalars()
            .all()
        )
        was_active = [row.id for row in rows if row.status == STATUS_ACTIVE]
        for row in rows:
            row.status = STATUS_REFUNDED
            row.ended_reason = REASON_REFUND
        if rows:
            await session.commit()

    for row in rows:
        if row.id in was_active:
            repo = IdentityRepository()
            user = await repo.find_by_id(row.user_id)
            if user is not None and user.active_plus_pass_id == row.id:
                await repo.update(
                    row.user_id, {"activePlusPassId": None, "activePlusPassExpiresAt": None}
                )
            entitlement_service.invalidate(row.user_id)

    if rows:
        logger.info(
            "pass: revoked %d pass(es) from purchase=%s (%d were active)",
            len(rows),
            purchase_id,
            len(was_active),
        )
    return rows
