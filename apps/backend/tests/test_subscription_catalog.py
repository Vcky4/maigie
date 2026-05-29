"""Unit tests for the active plan catalog and deprecated-tier rejection.

Covers Task 4.1 of the Circle Reimagining spec:
- ``get_active_plan_catalog()`` returns exactly the new tier set.
- ``_price_id_to_tier`` and ``_get_plan_code`` reject SQUAD_* / STUDY_CIRCLE_*
  on creation paths with HTTP 410 and the correct error code.
- 7-day Maigie Plus trial is granted on first PLUS purchase only.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_subscription_catalog.py -v
"""

import os

# Ensure conftest autouse DB fixture does not require DATABASE_URL for this module.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.schemas.subscription import (  # noqa: E402
    PlanCatalogProductId,
)
from src.services import (  # noqa: E402
    paystack_subscription_service as paystack_svc,
)
from src.services import subscription_service as stripe_svc  # noqa: E402
from src.utils.exceptions import DeprecatedPlanError  # noqa: E402

# ---------------------------------------------------------------------------
# Plan catalog
# ---------------------------------------------------------------------------


class TestActivePlanCatalog:
    """Property 1: catalog has exactly the new tier set (Requirement 1.10)."""

    def test_catalog_contains_exactly_the_new_tier_set(self):
        catalog = stripe_svc.get_active_plan_catalog()
        product_ids = {entry.productId for entry in catalog.products}
        assert product_ids == {
            PlanCatalogProductId.FREE,
            PlanCatalogProductId.PLUS_MONTHLY,
            PlanCatalogProductId.PLUS_YEARLY,
            PlanCatalogProductId.CIRCLE_PLAN_MONTHLY,
            PlanCatalogProductId.PLUS_SEAT_ADD_ON_MONTHLY,
        }

    def test_catalog_excludes_deprecated_tiers(self):
        catalog = stripe_svc.get_active_plan_catalog()
        names = {entry.displayName.lower() for entry in catalog.products}
        for forbidden in ("squad", "study circle"):
            assert not any(forbidden in name for name in names), (
                f"deprecated tier '{forbidden}' must not appear in active catalog"
            )

    def test_catalog_prices_match_requirement_1_3(self):
        cfg = get_settings()
        catalog = stripe_svc.get_active_plan_catalog()
        by_id = {entry.productId: entry for entry in catalog.products}
        # Free is $0, Plus is $4.99/mo or $39/yr, Circle Plan is $14.99/mo,
        # Plus Seat add-on is $4.99/seat/mo.
        assert by_id[PlanCatalogProductId.FREE].priceCents == 0
        assert (
            by_id[PlanCatalogProductId.PLUS_MONTHLY].priceCents == cfg.PRICE_CENTS_PLUS_MONTHLY
        )
        assert (
            by_id[PlanCatalogProductId.PLUS_YEARLY].priceCents == cfg.PRICE_CENTS_PLUS_YEARLY
        )
        assert (
            by_id[PlanCatalogProductId.CIRCLE_PLAN_MONTHLY].priceCents
            == cfg.PRICE_CENTS_CIRCLE_PLAN_MONTHLY
        )
        assert (
            by_id[PlanCatalogProductId.PLUS_SEAT_ADD_ON_MONTHLY].priceCents
            == cfg.PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY
        )

    def test_plus_tiers_carry_a_trial_period(self):
        catalog = stripe_svc.get_active_plan_catalog()
        by_id = {entry.productId: entry for entry in catalog.products}
        assert by_id[PlanCatalogProductId.PLUS_MONTHLY].trialDays == 7
        assert by_id[PlanCatalogProductId.PLUS_YEARLY].trialDays == 7


# ---------------------------------------------------------------------------
# Deprecated-tier rejection — Stripe surface
# ---------------------------------------------------------------------------


class TestStripeRejectsDeprecatedTiers:
    """Property 2: deprecated-tier creation is rejected (Requirements 1.9, 2.1)."""

    @pytest.mark.parametrize(
        ("plan_id", "expected_code"),
        [
            ("squad_monthly", "SQUAD_PLAN_REMOVED"),
            ("squad_yearly", "SQUAD_PLAN_REMOVED"),
            ("study_circle_monthly", "STUDY_CIRCLE_PLAN_REMOVED"),
            ("study_circle_yearly", "STUDY_CIRCLE_PLAN_REMOVED"),
        ],
    )
    def test_assert_plan_id_is_active_rejects_with_410(self, plan_id, expected_code):
        with pytest.raises(DeprecatedPlanError) as excinfo:
            stripe_svc.assert_plan_id_is_active(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code

    @pytest.mark.parametrize(
        ("plan_id", "expected_code"),
        [
            ("squad_monthly", "SQUAD_PLAN_REMOVED"),
            ("study_circle_yearly", "STUDY_CIRCLE_PLAN_REMOVED"),
        ],
    )
    def test_get_price_id_and_trial_days_rejects_deprecated(self, plan_id, expected_code):
        with pytest.raises(DeprecatedPlanError) as excinfo:
            stripe_svc.get_price_id_and_trial_days(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code

    def test_assert_plan_id_is_active_accepts_active_plans(self):
        for plan_id in (
            "maigie_plus_monthly",
            "plus_monthly",
            "plus_yearly",
            "circle_plan_monthly",
            "plus_seat_add_on_monthly",
        ):
            # Should not raise
            stripe_svc.assert_plan_id_is_active(plan_id)

    def test_assert_price_id_blocks_squad_yearly(self):
        cfg = get_settings()
        # Provide a non-empty price id so the check matches even when the
        # default config string is empty.
        cfg.STRIPE_PRICE_ID_SQUAD_YEARLY = "price_squad_yearly_test"
        try:
            with pytest.raises(DeprecatedPlanError) as excinfo:
                stripe_svc._assert_price_id_is_active("price_squad_yearly_test")
            assert excinfo.value.status_code == 410
            assert excinfo.value.code == "SQUAD_PLAN_REMOVED"
        finally:
            cfg.STRIPE_PRICE_ID_SQUAD_YEARLY = ""


# ---------------------------------------------------------------------------
# Deprecated-tier rejection — Paystack surface
# ---------------------------------------------------------------------------


class TestPaystackRejectsDeprecatedTiers:
    @pytest.mark.parametrize(
        ("plan_id", "expected_code"),
        [
            ("squad_monthly", "SQUAD_PLAN_REMOVED"),
            ("study_circle_yearly", "STUDY_CIRCLE_PLAN_REMOVED"),
        ],
    )
    def test_get_plan_code_rejects_deprecated(self, plan_id, expected_code):
        with pytest.raises(DeprecatedPlanError) as excinfo:
            paystack_svc._get_plan_code(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code


# ---------------------------------------------------------------------------
# Trial logic — Requirement 1.12
# ---------------------------------------------------------------------------


def _fake_user(*, tier="FREE", stripe_sub=None, paystack_sub=None):
    """Build a minimal user-shaped object for trial-detection tests."""
    return SimpleNamespace(
        id="user-test",
        tier=tier,
        stripeSubscriptionId=stripe_sub,
        paystackSubscriptionCode=paystack_sub,
    )


class TestPlusTrialOnFirstPurchase:
    """Requirement 1.12: 7-day trial on first PLUS purchase only."""

    def test_first_time_plus_user_gets_trial(self):
        user = _fake_user()
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_monthly", user=user)
        assert trial_days == get_settings().TRIAL_DAYS_MAIGIE_PLUS

    def test_returning_paid_user_does_not_get_trial(self):
        user = _fake_user(tier="PREMIUM_MONTHLY", stripe_sub="sub_existing")
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_yearly", user=user)
        assert trial_days == 0

    def test_user_with_paystack_subscription_does_not_get_trial(self):
        user = _fake_user(paystack_sub="SUB_existing")
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_monthly", user=user)
        assert trial_days == 0

    def test_circle_plan_at_personal_surface_does_not_emit_trial(self):
        # The Circle Plan trial is owned by the Circle billing service
        # (Task 7.1); the personal checkout surface must return 0 here.
        user = _fake_user()
        _, trial_days = stripe_svc.get_price_id_and_trial_days(
            "circle_plan_monthly", user=user
        )
        assert trial_days == 0
