"""Turning a verified payment into a grant, once and only once, on every rail.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Phase 5, Decision G. Stripe, Paystack, Google Play and Apple each
verify a payment their own way, but they all end here: **persist a `PlusPurchase`, then grant.** The
persist comes first because a pass the learner paid for but the server never recorded is a refund we
owe (a reinstalled iOS app cannot recover a finished consumable from StoreKit), and the grant comes
second because it references the purchase row.

**The unique `providerReference` is the whole idempotency story.** A webhook replay, a client retry
and an iOS `restore()` re-presenting the same token all collapse onto one row: the second insert
raises `IntegrityError`, and this module reads that as "already handled" rather than "grant again". A
reference already bound to a *different* learner is the standard cross-account IAP abuse vector, and
it is refused with `409 PURCHASE_ALREADY_CLAIMED` — by the database constraint, not a check that can
be forgotten.

**Pass fulfilment is idempotent by construction; voice fulfilment gates on the purchase being new.**
A pass grant checks whether a `PlusPass` already exists for the purchase, so a crash between the
insert and the grant is repaired by the retry rather than doubled. A voice top-up is additive and has
no such marker, so it is credited only when the `PlusPurchase` row was newly created — a lost credit
is recoverable through support, a double credit is a giveaway, and the plan puts that idempotency on
the purchase record rather than on the counter (Decision R).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domains.billing.db_models import PlusPass, PlusPurchase
from src.domains.billing.services import (
    entitlement_service,
    pass_service,
    voice_service,
)
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError

logger = logging.getLogger(__name__)

#: The voice pack, the one purchase that grants no entitlement and no pass — only seconds (Decision R).
VOICE_PACK_PRODUCT_ID = "plus_voice_30"


def configured_store_amount(product_id: str, region_code: str | None) -> tuple[int, str]:
    """The price we set for this product in the given store region, from config, as (minor, currency).

    The store `products.get`/receipt does not reliably carry the charged amount, so a store rail records
    the price it was configured with. §5.7.6's by-hand parity check guarantees the store price equals
    config, so this is the real amount rather than a guess — Nigeria pays kobo, everywhere else pays
    USD cents. An unknown product returns `0`, which never happens for a verified store SKU.
    """
    from src.config import get_settings

    settings = get_settings()
    if (region_code or "").upper() == "NG":
        ngn = {
            "plus_pass_5h": settings.PRICE_NGN_PLUS_PASS_5H,
            "plus_pass_7d": settings.PRICE_NGN_PLUS_PASS_7D,
            "plus_pass_term": settings.PRICE_NGN_PLUS_PASS_TERM,
            VOICE_PACK_PRODUCT_ID: settings.PRICE_NGN_PLUS_VOICE_30,
        }
        return int(ngn.get(product_id, 0)), "NGN"
    usd = {
        "plus_pass_5h": settings.PRICE_CENTS_PLUS_PASS_5H,
        "plus_pass_7d": settings.PRICE_CENTS_PLUS_PASS_7D,
        VOICE_PACK_PRODUCT_ID: settings.PRICE_CENTS_PLUS_VOICE_30,
    }
    return int(usd.get(product_id, 0)), "USD"


async def fulfill_purchase(
    *,
    user_id: str,
    product_id: str,
    provider: str,
    provider_reference: str,
    amount_minor: int,
    currency: str,
    raw_payload: dict | None = None,
    duration_minutes: int | None = None,
    units_allowance: int | None = None,
) -> PlusPurchase:
    """Persist a verified purchase and grant what it bought. Idempotent on `provider_reference`.

    `duration_minutes`/`units_allowance` override the pass product table so a market can sell its own
    allowance (§6.8); the NGN rail passes its own figures and the snapshot on the resulting `PlusPass`
    is what every later reader sees. They are ignored for the voice pack.

    Returns the `PlusPurchase` — the freshly written one, or the existing row on a replay. Raises
    `ConflictError(code="PURCHASE_ALREADY_CLAIMED")` when the reference belongs to another learner.
    """
    now = datetime.now(UTC)
    factory = get_session_factory()
    newly_created = False
    async with factory() as session:
        purchase = PlusPurchase(
            user_id=user_id,
            product_id=product_id,
            # Both passes and the voice pack are `pass`-kind. Subscriptions do not come through this
            # path — they are tracked on `User` by the subscription webhook.
            product_kind="pass",
            provider=provider,
            provider_reference=provider_reference,
            amount_minor=amount_minor,
            currency=currency,
            status="completed",
            completed_at=now,
            raw_payload=raw_payload,
        )
        session.add(purchase)
        try:
            await session.commit()
            await session.refresh(purchase)
            newly_created = True
        except IntegrityError:
            # The unique `providerReference` fired: this payment was already recorded. Find the row it
            # collapsed onto and decide whether this is a replay (same learner) or a claim attempt.
            await session.rollback()
            existing = (
                await session.execute(
                    select(PlusPurchase).where(
                        PlusPurchase.provider_reference == provider_reference
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.user_id != user_id:
                raise ConflictError(
                    message="This purchase is already associated with another account.",
                    detail=(f"provider_reference={provider_reference} belongs to a different user"),
                    code="PURCHASE_ALREADY_CLAIMED",
                )
            purchase = existing

    await _grant_for_purchase(
        purchase=purchase,
        newly_created=newly_created,
        duration_minutes=duration_minutes,
        units_allowance=units_allowance,
    )
    return purchase


async def _grant_for_purchase(
    *,
    purchase: PlusPurchase,
    newly_created: bool,
    duration_minutes: int | None,
    units_allowance: int | None,
) -> None:
    """Grant the pass or credit the voice seconds behind a persisted purchase."""
    product_id = purchase.product_id

    if pass_service.is_pass_product(product_id):
        # Idempotent: a pass is granted only if the purchase does not already have one, so a webhook
        # retry after a crash between the insert and the grant is repaired rather than doubled.
        factory = get_session_factory()
        async with factory() as session:
            existing_pass = (
                await session.execute(
                    select(PlusPass.id).where(PlusPass.purchase_id == purchase.id)
                )
            ).scalar_one_or_none()
        if existing_pass is None:
            await pass_service.grant(
                user_id=purchase.user_id,
                product_id=product_id,
                purchase_id=purchase.id,
                source="purchase",
                duration_minutes=duration_minutes,
                units_allowance=units_allowance,
            )
        return

    if product_id == VOICE_PACK_PRODUCT_ID:
        # Additive and unmarked, so credited only for a newly recorded purchase — the idempotency
        # lives on the purchase row, not the counter (Decision R).
        if newly_created:
            await voice_service.add_purchased(
                purchase.user_id, entitlement_service.VOICE_SECONDS_TOP_UP
            )
        return

    # A product was sold that this rail cannot grant. Naming it is worth more than swallowing it — the
    # purchase is recorded, so the learner is not lost, but something upstream let an ungrantable id
    # through.
    raise ConflictError(
        message="That product cannot be fulfilled.",
        detail=f"product_id={product_id} is neither a pass nor the voice pack",
        code="NOT_A_PURCHASABLE_PRODUCT",
    )


async def refund_purchase(*, provider_reference: str) -> bool:
    """Mark a purchase refunded and revoke what it granted. Returns whether a purchase was found.

    The revocation path every rail's refund/void notification funnels into — Stripe's `charge.refunded`,
    Google's voided-purchase RTDN, Apple's `REFUND`/`REVOKE`. Finds the `PlusPurchase` by the provider's
    reference and, for a pass, calls `pass_service.revoke`, which marks every `PlusPass` from that
    purchase refunded and clears the entitlement cache.

    A charge with no matching purchase (a subscription refund, an unrelated charge) is a no-op — this is
    keyed on the one-time purchase reference and simply finds nothing. Idempotent: an already-refunded
    purchase returns `True` without revoking twice.

    **Voice seconds are not clawed back.** A refunded voice pack's minutes may already be spent, and
    reaching into a live balance to subtract them risks driving it negative or removing seconds a later
    purchase funded; the purchase is marked refunded for the record, and the seconds stand.
    """
    factory = get_session_factory()
    async with factory() as session:
        purchase = (
            await session.execute(
                select(PlusPurchase).where(PlusPurchase.provider_reference == provider_reference)
            )
        ).scalar_one_or_none()
        if purchase is None:
            logger.info(
                "purchase: refund for reference=%s matched no purchase (ignored)",
                provider_reference,
            )
            return False
        if purchase.refunded_at is not None:
            return True
        purchase.refunded_at = datetime.now(UTC)
        purchase.status = "refunded"
        purchase_id = purchase.id
        product_id = purchase.product_id
        await session.commit()

    if pass_service.is_pass_product(product_id):
        await pass_service.revoke(purchase_id=purchase_id)
    else:
        logger.info(
            "purchase: refunded voice pack purchase=%s; seconds left in place",
            purchase_id,
        )
    return True
