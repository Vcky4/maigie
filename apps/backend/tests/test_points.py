"""Points: the ledger that a referral earns and a pass spends (§6.9, Decision O).

Two kinds of test live here, and the split is deliberate.

The **construction guarantees** are pure and run everywhere: the redeemable set is exactly the two
passes, the subscription is not in it, and `redeem` refuses a non-pass id before it reads a thing.
These are the properties §6.9 leans on being true *by construction* rather than by a validation a
later ticket could drop, so they are asserted without a database in the way.

The **ledger arithmetic** — FIFO across grants, lazy expiry, once-per-referral, the seven-day
qualification — is SQL, and faking a session cannot exercise `SUM ... WHERE NOT expired`, a partial
unique index, or `COUNT(DISTINCT date(...))` honestly. Those tests take the same shape as the rest of
the suite's database-backed tests: they request the `db` fixture and so skip unless `RUN_DB_TESTS=1`
points them at a scratch Postgres. Run them with::

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://... pytest tests/test_points.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.domains.billing.services import points_service
from src.shared.exceptions import ConflictError, ValidationError

# ===========================================================================
# Construction guarantees — no database, run everywhere
# ===========================================================================


class TestPointsBuyPassesAndNothingElse:
    """The subscription is unreachable from points by construction, not by a check (§6.9)."""

    def test_the_redeemable_set_is_exactly_the_two_passes(self):
        assert set(points_service.POINTS_COST) == {"plus_pass_5h", "plus_pass_7d"}

    def test_the_subscription_is_not_a_redeemable_product(self):
        # If a subscription id ever appears in POINTS_COST, points could buy subscription time — the
        # one thing Decision O forbids. This is the guard that fails loudly if that dict grows.
        for subscription_id in ("maigie_plus_monthly", "plus_monthly", "maigie_plus_yearly"):
            assert subscription_id not in points_service.POINTS_COST

    @pytest.mark.asyncio
    async def test_redeem_refuses_a_non_pass_id_before_reading_anything(self):
        """A non-pass id is refused before a session is opened, so there is no path — not even a
        failed one — from points to a subscription. Asserted with no database precisely to show the
        refusal precedes any read.
        """
        with pytest.raises(ValidationError):
            await points_service.redeem(user_id="whoever", product_id="maigie_plus_monthly")


class TestTheRedeemableProperty:
    """`PointsBalance.redeemable` is what a client offers, so it must never name an unaffordable pass."""

    def _balance(self, points: int) -> points_service.PointsBalance:
        return points_service.PointsBalance(
            balance=points, next_expiry_points=None, next_expiry_at=None
        )

    def test_a_hundred_points_can_take_only_the_5h_pass(self):
        assert self._balance(100).redeemable == ["plus_pass_5h"]

    def test_two_hundred_and_fifty_can_take_both_cheapest_first(self):
        assert self._balance(250).redeemable == ["plus_pass_5h", "plus_pass_7d"]

    def test_ninety_nine_points_can_take_nothing(self):
        assert self._balance(99).redeemable == []


# ===========================================================================
# Ledger arithmetic — database-backed, opt-in via RUN_DB_TESTS
# ===========================================================================


@pytest.fixture
async def world(db):
    """A scratch world over the real database: make users, drive the service, clean up after.

    Requesting `db` is what makes `conftest.db_lifecycle` connect the engine and skip the test when
    `RUN_DB_TESTS` is unset — the same opt-in every database-backed test in the suite uses.
    """
    from sqlalchemy import delete

    from src.domains.billing.db_models import (
        PlusPass,
        PlusPurchase,
        PointsLedgerEntry,
        ReferralReward,
        UsageEvent,
    )
    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    created_user_ids: list[str] = []

    async def make_user(*, referral_code: str | None = None, referred_by_code: str | None = None):
        uid = str(uuid.uuid4())
        async with factory() as session:
            session.add(
                User(
                    id=uid,
                    email=f"points_{uid}@example.com",
                    referral_code=referral_code,
                    referred_by_code=referred_by_code,
                )
            )
            await session.commit()
        created_user_ids.append(uid)
        return uid

    async def add_grant(user_id: str, points: int, *, source_ref: str, expires_at=None):
        """Write a referral grant directly, so a test can set an arbitrary size or expiry."""
        async with factory() as session:
            entry = PointsLedgerEntry(
                user_id=user_id,
                points=points,
                kind=points_service.KIND_REFERRAL,
                expires_at=expires_at
                or datetime.now(UTC) + timedelta(days=points_service.POINTS_EXPIRY_DAYS),
                source_ref=source_ref,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry.id

    async def add_usage_days(user_id: str, days: int, *, base=None):
        base = base or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with factory() as session:
            for i in range(days):
                session.add(
                    UsageEvent(
                        user_id=user_id,
                        operation="chat_message",
                        units=100,
                        created_at=base + timedelta(days=i),
                    )
                )
            await session.commit()

    async def points_balance(user_id: str) -> int:
        return (await points_service.balance(user_id)).balance

    yield type(
        "World",
        (),
        {
            "factory": factory,
            "make_user": staticmethod(make_user),
            "add_grant": staticmethod(add_grant),
            "add_usage_days": staticmethod(add_usage_days),
            "points_balance": staticmethod(points_balance),
        },
    )

    # Teardown: UsageEvent has no FK to User (a deleted learner's spend still happened), so it is
    # cleared by user id; everything else cascades when the users go.
    async with factory() as session:
        if created_user_ids:
            await session.execute(
                delete(UsageEvent).where(UsageEvent.user_id.in_(created_user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(created_user_ids)))
            await session.commit()


class TestRedemption:
    @pytest.mark.asyncio
    async def test_one_referral_buys_a_5h_pass_and_leaves_nothing(self, world):
        user_id = await world.make_user()
        await world.add_grant(user_id, 100, source_ref="ref-1")

        new_pass = await points_service.redeem(user_id=user_id, product_id="plus_pass_5h")

        assert new_pass.source == "points"
        assert await world.points_balance(user_id) == 0

    @pytest.mark.asyncio
    async def test_two_hundred_and_forty_nine_points_cannot_buy_the_7day_pass(self, world):
        """250 is the price; 249 is one short, and the refusal is a 409 carrying INSUFFICIENT_POINTS
        rather than a silent under-grant."""
        user_id = await world.make_user()
        await world.add_grant(user_id, 249, source_ref="ref-1")

        with pytest.raises(ConflictError) as excinfo:
            await points_service.redeem(user_id=user_id, product_id="plus_pass_7d")
        assert excinfo.value.code == "INSUFFICIENT_POINTS"
        # Nothing was spent.
        assert await world.points_balance(user_id) == 249

    @pytest.mark.asyncio
    async def test_fifo_spends_the_oldest_grant_first_across_three(self, world):
        """Three 100-point grants, a 250-point spend: the two soonest to expire drain fully and the
        third gives up 50, so a steady earner loses points in the order they would have expired.
        """
        now = datetime.now(UTC)
        user_id = await world.make_user()
        await world.add_grant(user_id, 100, source_ref="ref-1", expires_at=now + timedelta(days=10))
        await world.add_grant(user_id, 100, source_ref="ref-2", expires_at=now + timedelta(days=20))
        await world.add_grant(user_id, 100, source_ref="ref-3", expires_at=now + timedelta(days=30))

        await points_service.redeem(user_id=user_id, product_id="plus_pass_7d")

        # 300 earned, 250 spent, 50 left — and it is the last-to-expire grant that still holds it.
        assert await world.points_balance(user_id) == 50
        bal = await points_service.balance(user_id)
        assert bal.next_expiry_at == now + timedelta(days=30)

    @pytest.mark.asyncio
    async def test_a_redeemed_pass_has_no_purchase_behind_it(self, world):
        """Nothing was bought (Decision O), so the pass carries `source='points'` and no purchase, and
        no `PlusPurchase` row is written — the property that keeps points off the revenue ledger."""
        from sqlalchemy import func, select

        from src.domains.billing.db_models import PlusPurchase

        user_id = await world.make_user()
        await world.add_grant(user_id, 100, source_ref="ref-1")

        new_pass = await points_service.redeem(user_id=user_id, product_id="plus_pass_5h")

        assert new_pass.purchase_id is None
        async with world.factory() as session:
            purchases = (
                await session.execute(
                    select(func.count())
                    .select_from(PlusPurchase)
                    .where(PlusPurchase.user_id == user_id)
                )
            ).scalar()
        assert purchases == 0


class TestExpiry:
    @pytest.mark.asyncio
    async def test_a_grant_past_its_date_is_not_spendable_before_the_sweep(self, world):
        """`balance` and `redeem` both exclude expired grants on read, so the nightly sweep cannot be
        the thing that makes expiry true — a stale sweep must never let dead points be spent."""
        now = datetime.now(UTC)
        user_id = await world.make_user()
        await world.add_grant(user_id, 100, source_ref="ref-1", expires_at=now - timedelta(days=1))

        assert await world.points_balance(user_id) == 0
        with pytest.raises(ConflictError):
            await points_service.redeem(user_id=user_id, product_id="plus_pass_5h")

    @pytest.mark.asyncio
    async def test_the_sweep_writes_an_explaining_entry_for_an_expired_grant(self, world):
        now = datetime.now(UTC)
        user_id = await world.make_user()
        await world.add_grant(user_id, 100, source_ref="ref-1", expires_at=now - timedelta(days=1))

        await points_service.expire_due()

        # Balance was already zero; the sweep adds a negative expiry entry so the ledger explains it.
        entries = await points_service.history(user_id)
        assert any(e.kind == points_service.KIND_EXPIRY and e.points == -100 for e in entries)
        assert await world.points_balance(user_id) == 0


class TestOncePerReferral:
    @pytest.mark.asyncio
    async def test_the_same_referral_cannot_grant_twice(self, world):
        """The partial unique index is the idempotency, because the daily job re-evaluates everyone —
        a second grant for the same referred learner is refused by the database, not by the service.
        """
        user_id = await world.make_user()
        first = await points_service.grant(
            user_id=user_id,
            points=100,
            kind=points_service.KIND_REFERRAL,
            source_ref="referred-learner-1",
        )
        second = await points_service.grant(
            user_id=user_id,
            points=100,
            kind=points_service.KIND_REFERRAL,
            source_ref="referred-learner-1",
        )

        assert first is not None
        assert second is None  # the index refused it, and a refused re-grant is success
        assert await world.points_balance(user_id) == 100


class TestQualification:
    @pytest.mark.asyncio
    async def test_seven_distinct_billable_days_qualify_the_referrer(self, world):
        referrer_id = await world.make_user(referral_code="CODE-A")
        referred_id = await world.make_user(referred_by_code="CODE-A")
        await world.add_usage_days(referred_id, 7)

        entry = await points_service.qualify_referral(referred_id)

        assert entry is not None
        assert (
            await world.points_balance(referrer_id) == points_service.POINTS_PER_QUALIFIED_REFERRAL
        )

    @pytest.mark.asyncio
    async def test_six_days_are_not_enough(self, world):
        referrer_id = await world.make_user(referral_code="CODE-B")
        referred_id = await world.make_user(referred_by_code="CODE-B")
        await world.add_usage_days(referred_id, 6)

        entry = await points_service.qualify_referral(referred_id)

        assert entry is None
        assert await world.points_balance(referrer_id) == 0

    @pytest.mark.asyncio
    async def test_logins_without_a_billable_operation_do_not_qualify(self, world):
        """Activity is a charged operation, not an app open: qualification reads `UsageEvent`, which
        exists only for billable ops, so an account with no rows never qualifies however often it
        logs in."""
        referrer_id = await world.make_user(referral_code="CODE-C")
        referred_id = await world.make_user(referred_by_code="CODE-C")
        # No usage events at all — the "seven logins, nothing studied" case.

        entry = await points_service.qualify_referral(referred_id)

        assert entry is None
        assert await world.points_balance(referrer_id) == 0
