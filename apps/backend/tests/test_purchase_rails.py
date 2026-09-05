"""The purchase rails: one verified payment becomes one grant, once, and a refund takes it back.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Phase 5, Decision G. Every rail — Stripe, Paystack, Google Play, Apple
— ends in `purchase_service.fulfill_purchase`, so the properties that matter are properties of that
seam: a replayed reference grants nothing, a reference bound to another learner is refused, a voice
pack is not doubled, and a refund revokes the pass. Those are tested here against the seam directly
rather than through four provider mocks.

The construction facts — which products each rail sells, how a store amount is priced — are pure and
run everywhere. The seam's database behaviour is opt-in via `RUN_DB_TESTS`, like the rest of the
suite's database-backed tests:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://... pytest tests/test_purchase_rails.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import uuid

import pytest

from src.domains.billing.services import purchase_service
from src.shared.exceptions import ConflictError

# ===========================================================================
# Construction — no database, run everywhere
# ===========================================================================


class TestEachRailSellsTheRightProducts:
    def test_stripe_sells_the_two_usd_passes_and_voice_but_not_the_term_pass(self):
        from src.domains.billing.services import stripe_service

        assert set(stripe_service.ONE_TIME_PRICE_SETTINGS) == {
            "plus_pass_5h",
            "plus_pass_7d",
            "plus_voice_30",
        }
        # The Term Pass is NGN-only; a Stripe price for it would put an unbuyable product one call
        # from sale (§5.7.1).
        assert "plus_pass_term" not in stripe_service.ONE_TIME_PRICE_SETTINGS

    def test_paystack_sells_all_three_passes_and_voice(self):
        from src.domains.billing.services import paystack_service

        assert set(paystack_service.NGN_ONE_TIME_SETTINGS) == {
            "plus_pass_5h",
            "plus_pass_7d",
            "plus_pass_term",
            "plus_voice_30",
        }

    def test_apple_maps_its_skus_and_omits_the_subscription(self):
        from src.domains.billing.services import apple_service

        assert apple_service.APPLE_PRODUCT_MAP == {
            "com.maigie.plus.pass5h": "plus_pass_5h",
            "com.maigie.plus.pass7d": "plus_pass_7d",
            "com.maigie.plus.passterm": "plus_pass_term",
            "com.maigie.plus.voice30": "plus_voice_30",
        }
        # The subscription is verified elsewhere, not by the one-time pass rail.
        assert "com.maigie.plus.monthly.sub" not in apple_service.APPLE_PRODUCT_MAP


class TestStoreAmountIsPricedNotConverted:
    def test_nigeria_is_priced_in_kobo(self):
        amount, currency = purchase_service.configured_store_amount("plus_pass_5h", "NG")
        assert (amount, currency) == (70_000, "NGN")

    def test_the_term_pass_is_only_priced_in_nigeria(self):
        amount, currency = purchase_service.configured_store_amount("plus_pass_term", "NG")
        assert (amount, currency) == (550_000, "NGN")

    def test_everywhere_else_is_priced_in_usd_cents(self):
        amount, currency = purchase_service.configured_store_amount("plus_pass_7d", "US")
        assert (amount, currency) == (399, "USD")

    def test_the_voice_pack_has_a_price_in_both_markets(self):
        assert purchase_service.configured_store_amount("plus_voice_30", "NG") == (150_000, "NGN")
        assert purchase_service.configured_store_amount("plus_voice_30", "US") == (149, "USD")


def test_the_voice_pack_is_the_one_product_that_grants_no_pass():
    """`plus_voice_30` credits seconds, never a pass — the fulfilment fork turns on this id."""
    assert purchase_service.VOICE_PACK_PRODUCT_ID == "plus_voice_30"
    from src.domains.billing.services import pass_service

    assert not pass_service.is_pass_product("plus_voice_30")


# ===========================================================================
# The seam — database-backed, opt-in via RUN_DB_TESTS
# ===========================================================================


@pytest.fixture
async def world(db):
    """A scratch world over the real database: make a learner, drive the seam, clean up after."""
    from sqlalchemy import delete, func, select

    from src.domains.billing.db_models import PlusPass, PlusPurchase
    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    created_user_ids: list[str] = []

    async def make_user() -> str:
        uid = str(uuid.uuid4())
        async with factory() as session:
            session.add(User(id=uid, email=f"rail_{uid}@example.com"))
            await session.commit()
        created_user_ids.append(uid)
        return uid

    async def count_purchases(user_id: str) -> int:
        async with factory() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(PlusPurchase)
                    .where(PlusPurchase.user_id == user_id)
                )
            ).scalar() or 0

    async def passes_for(purchase_id: str) -> list:
        async with factory() as session:
            return list(
                (await session.execute(select(PlusPass).where(PlusPass.purchase_id == purchase_id)))
                .scalars()
                .all()
            )

    async def voice_purchased(user_id: str) -> int:
        async with factory() as session:
            return (
                await session.execute(
                    select(User.voice_seconds_purchased).where(User.id == user_id)
                )
            ).scalar() or 0

    yield type(
        "World",
        (),
        {
            "factory": factory,
            "make_user": staticmethod(make_user),
            "count_purchases": staticmethod(count_purchases),
            "passes_for": staticmethod(passes_for),
            "voice_purchased": staticmethod(voice_purchased),
        },
    )

    async with factory() as session:
        if created_user_ids:
            await session.execute(delete(User).where(User.id.in_(created_user_ids)))
            await session.commit()


class TestFulfilment:
    @pytest.mark.asyncio
    async def test_a_verified_pass_purchase_writes_a_purchase_and_an_inventory_pass(self, world):
        user_id = await world.make_user()
        purchase = await purchase_service.fulfill_purchase(
            user_id=user_id,
            product_id="plus_pass_5h",
            provider="stripe",
            provider_reference=f"pi_{uuid.uuid4()}",
            amount_minor=99,
            currency="USD",
        )
        passes = await world.passes_for(purchase.id)
        assert len(passes) == 1
        assert passes[0].status == "inventory"
        assert passes[0].source == "purchase"

    @pytest.mark.asyncio
    async def test_a_replayed_reference_grants_nothing_the_second_time(self, world):
        """Webhook retry, client retry, iOS restore — all re-present the same reference, and the
        unique constraint collapses them onto one purchase and one pass."""
        user_id = await world.make_user()
        reference = f"pi_{uuid.uuid4()}"
        first = await purchase_service.fulfill_purchase(
            user_id=user_id,
            product_id="plus_pass_7d",
            provider="stripe",
            provider_reference=reference,
            amount_minor=399,
            currency="USD",
        )
        second = await purchase_service.fulfill_purchase(
            user_id=user_id,
            product_id="plus_pass_7d",
            provider="stripe",
            provider_reference=reference,
            amount_minor=399,
            currency="USD",
        )
        assert first.id == second.id
        assert await world.count_purchases(user_id) == 1
        assert len(await world.passes_for(first.id)) == 1

    @pytest.mark.asyncio
    async def test_a_reference_bound_to_another_learner_is_refused(self, world):
        """The cross-account IAP abuse vector, defended by the database constraint (Decision G)."""
        owner = await world.make_user()
        attacker = await world.make_user()
        reference = f"gp_{uuid.uuid4()}"
        await purchase_service.fulfill_purchase(
            user_id=owner,
            product_id="plus_pass_5h",
            provider="google_play",
            provider_reference=reference,
            amount_minor=70_000,
            currency="NGN",
        )
        with pytest.raises(ConflictError) as excinfo:
            await purchase_service.fulfill_purchase(
                user_id=attacker,
                product_id="plus_pass_5h",
                provider="google_play",
                provider_reference=reference,
                amount_minor=70_000,
                currency="NGN",
            )
        assert excinfo.value.code == "PURCHASE_ALREADY_CLAIMED"

    @pytest.mark.asyncio
    async def test_the_voice_pack_credits_seconds_and_creates_no_pass(self, world):
        user_id = await world.make_user()
        purchase = await purchase_service.fulfill_purchase(
            user_id=user_id,
            product_id="plus_voice_30",
            provider="stripe",
            provider_reference=f"pi_{uuid.uuid4()}",
            amount_minor=149,
            currency="USD",
        )
        assert await world.passes_for(purchase.id) == []
        assert await world.voice_purchased(user_id) == 1_800

    @pytest.mark.asyncio
    async def test_a_replayed_voice_purchase_does_not_double_the_seconds(self, world):
        """The voice top-up is additive, so its idempotency lives on the purchase record: a replay of
        the same reference must not credit a second 30 minutes."""
        user_id = await world.make_user()
        reference = f"pi_{uuid.uuid4()}"
        for _ in range(2):
            await purchase_service.fulfill_purchase(
                user_id=user_id,
                product_id="plus_voice_30",
                provider="stripe",
                provider_reference=reference,
                amount_minor=149,
                currency="USD",
            )
        assert await world.voice_purchased(user_id) == 1_800
        assert await world.count_purchases(user_id) == 1


class TestRefund:
    @pytest.mark.asyncio
    async def test_a_refund_revokes_the_pass(self, world):
        user_id = await world.make_user()
        reference = f"pi_{uuid.uuid4()}"
        purchase = await purchase_service.fulfill_purchase(
            user_id=user_id,
            product_id="plus_pass_5h",
            provider="stripe",
            provider_reference=reference,
            amount_minor=99,
            currency="USD",
        )

        found = await purchase_service.refund_purchase(provider_reference=reference)

        assert found is True
        passes = await world.passes_for(purchase.id)
        assert passes[0].status == "refunded"
        assert passes[0].ended_reason == "refund"

    @pytest.mark.asyncio
    async def test_a_refund_for_an_unknown_reference_is_a_no_op(self, world):
        """A subscription refund, or a charge that was never one of ours, matches no purchase."""
        assert await purchase_service.refund_purchase(provider_reference="pi_nonexistent") is False
