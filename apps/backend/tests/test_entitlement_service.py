"""One resolver, and the precedence rule it encodes.

Four mechanisms used to answer "is this learner Plus" and they disagreed
(MAIGIE_PLUS_COMMERCIAL_PLAN.md §2). Two of those disagreements were live defects: a trialling
learner got Plus capabilities and free-tier LLM models in the same request (drift 11), and a
`STUDY_CIRCLE_*` subscriber was denied every capability while the credit meter granted them millions
of credits (drift 10). Both are asserted here, in the direction revision 4 of the plan settled on.

Most of this exercises `_compose`, which is the pure half. That is deliberate: the precedence rule is
the only product decision in the module, `_compose` is where it lives, and testing it directly means
the pass branch is covered before `PlusPass` exists — Phase 4 fills in one reader and does not
reopen the question of what outranks what.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_entitlement_service.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from src.domains.billing.services import entitlement_service as svc  # noqa: E402

NOW = datetime.now(UTC)
LATER = NOW + timedelta(days=10)
PASS_ENDS = NOW + timedelta(hours=4)
TRIAL_ENDS = NOW + timedelta(days=2, hours=3)

A_PASS = svc.ActivePass(pass_id="pass_1", product_id="plus_pass_5h", expires_at=PASS_ENDS)
A_TRIAL = svc.ActiveTrial(ends_at=TRIAL_ENDS, days_remaining=2)


def compose(**overrides):
    kwargs = {
        "subscription_tier": "FREE",
        "subscription_period_end": None,
        "active_pass": None,
        "active_trial": None,
    }
    kwargs.update(overrides)
    return svc._compose(**kwargs)


# ---------------------------------------------------------------------------
# Precedence: subscription → pass → trial → free
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_subscription_outranks_a_pass(self):
        """A subscriber's pass is not burned by a request they were already entitled to make.

        This is Decision D's reason for the ordering. If a pass outranked a subscription, a learner
        holding both would spend a product they did not need and would be right to ask for it back.
        """
        result = compose(
            subscription_tier="PREMIUM_MONTHLY",
            subscription_period_end=LATER,
            active_pass=A_PASS,
        )
        assert result.tier == "plus"
        assert result.source == "subscription"
        assert result.pass_id is None
        assert result.expires_at == LATER

    def test_subscription_outranks_a_trial(self):
        result = compose(
            subscription_tier="PREMIUM_MONTHLY",
            subscription_period_end=LATER,
            active_trial=A_TRIAL,
        )
        assert result.source == "subscription"
        assert result.is_trial is False
        assert result.trial_days_remaining is None

    def test_pass_outranks_a_trial(self):
        """A learner who paid for a pass gets the pass's identity, not the trial's.

        The visible difference is the copy: a trial says "2 days left, then it ends" and a pass says
        "your 5 hours are running". Resolving to the trial would tell a paying learner their purchase
        expires when their free look does.
        """
        result = compose(active_pass=A_PASS, active_trial=A_TRIAL)
        assert result.tier == "plus"
        assert result.source == "pass"
        assert result.pass_id == "pass_1"
        assert result.expires_at == PASS_ENDS
        assert result.is_trial is False

    def test_trial_is_plus(self):
        result = compose(active_trial=A_TRIAL)
        assert result.tier == "plus"
        assert result.source == "trial"
        assert result.is_trial is True
        assert result.trial_days_remaining == 2
        assert result.expires_at == TRIAL_ENDS

    def test_nothing_is_free(self):
        result = compose()
        assert result.tier == "free"
        assert result.source == "none"
        assert result.expires_at is None
        assert result.pass_id is None
        assert result.is_trial is False


# ---------------------------------------------------------------------------
# The tier map: an explicit frozenset, not a prefix
# ---------------------------------------------------------------------------


class TestTierMap:
    def test_premium_monthly_is_the_only_plus_tier(self):
        assert svc.PLUS_TIERS == frozenset({"PREMIUM_MONTHLY"})

    @pytest.mark.parametrize(
        "tier",
        [
            "STUDY_CIRCLE_MONTHLY",
            "STUDY_CIRCLE_YEARLY",
            "SQUAD_MONTHLY",
            "SQUAD_YEARLY",
            "PREMIUM_YEARLY",
        ],
    )
    def test_retired_tiers_resolve_to_free(self, tier):
        """Drift 10, closed by deleting the tiers rather than admitting them.

        Revision 3 of the plan asserted the opposite: a `LEGACY_PLUS_TIERS` frozenset resolved all
        five to `plus` so that subscribers on withdrawn products kept what they were paying for.
        There are none — the precondition is Phase 2b's first task — so `free` is now the correct
        answer and a `User.tier` holding one of these strings is a data error.

        If this test is ever the thing standing between a paying subscriber and their features, the
        fix is to restore `LEGACY_PLUS_TIERS`, not to argue with the assertion.
        """
        result = compose(subscription_tier=tier, subscription_period_end=LATER)
        assert result.tier == "free"
        assert result.source == "none"
        # Still reported, because display and history need the raw value even when it grants nothing.
        assert result.subscription_tier == tier

    def test_a_prefix_match_would_have_passed_and_is_not_used(self):
        """`startswith("PREMIUM")` was the bug. An unknown PREMIUM-ish string must not be Plus."""
        result = compose(subscription_tier="PREMIUM_LIFETIME_SOMETHING")
        assert result.tier == "free"

    def test_raw_tier_is_always_reported(self):
        assert compose().subscription_tier == "FREE"
        assert compose(subscription_tier=None).subscription_tier == "FREE"
        assert compose(subscription_tier="PREMIUM_MONTHLY").subscription_tier == "PREMIUM_MONTHLY"


# ---------------------------------------------------------------------------
# Window allowances (§6.3)
# ---------------------------------------------------------------------------


class TestWindowAllowance:
    def test_free(self):
        assert compose().window_allowance == 500

    def test_subscription(self):
        assert compose(subscription_tier="PREMIUM_MONTHLY").window_allowance == 4_000

    def test_trial_matches_a_subscription(self):
        """A trialling learner is indistinguishable from a subscriber, allowance included."""
        assert compose(active_trial=A_TRIAL).window_allowance == 4_000

    def test_five_hour_pass_carries_its_own_allowance(self):
        """$0.99 buys full capabilities and a bounded amount of the expensive ones, not a
        subscriber's allowance. §6.1 is explicit that the two are different promises."""
        assert compose(active_pass=A_PASS).window_allowance == 3_000

    def test_seven_day_pass(self):
        seven_day = svc.ActivePass(pass_id="p2", product_id="plus_pass_7d", expires_at=PASS_ENDS)
        assert compose(active_pass=seven_day).window_allowance == 4_000

    def test_unknown_pass_product_falls_to_the_smallest_allowance(self):
        """A pass product added to the store without a row in the allowance map must not grant the
        largest allowance by default. Under-granting is a support ticket; over-granting is COGS."""
        mystery = svc.ActivePass(pass_id="p3", product_id="plus_pass_30d", expires_at=PASS_ENDS)
        assert compose(active_pass=mystery).window_allowance == 3_000


# ---------------------------------------------------------------------------
# Trial reads
# ---------------------------------------------------------------------------


class TestActiveTrial:
    def test_no_trial_field_is_no_trial(self):
        assert svc._active_trial(None) is None

    def test_an_elapsed_trial_is_no_trial(self):
        assert svc._active_trial(NOW - timedelta(seconds=1)) is None

    def test_a_running_trial_reports_floored_days(self):
        """Floored, matching `trial_service.get_trial_status`. Two places computing this differently
        is how a learner sees "2 days left" on one screen and "3" on another."""
        trial = svc._active_trial(NOW + timedelta(days=2, hours=23))
        assert trial is not None
        assert trial.days_remaining == 2

    def test_a_trial_in_its_last_hour_is_still_active(self):
        trial = svc._active_trial(NOW + timedelta(minutes=30))
        assert trial is not None
        assert trial.days_remaining == 0


# ---------------------------------------------------------------------------
# Failure behaviour, and the Phase 4 seam
# ---------------------------------------------------------------------------


class TestSeamsAndFailure:
    def test_free_entitlement_is_what_a_failed_read_returns(self):
        assert svc.FREE_ENTITLEMENT.tier == "free"
        assert svc.FREE_ENTITLEMENT.source == "none"
        assert svc.FREE_ENTITLEMENT.window_allowance == 500

    @pytest.mark.asyncio
    async def test_resolve_gates_as_free_when_the_read_fails(self, monkeypatch):
        """A failure to resolve an entitlement must not fail the request. `resolve()` is called on
        nearly every gated request, so raising here would turn a database blip into an outage."""

        def boom():
            raise RuntimeError("no database")

        monkeypatch.setattr("src.shared.database.session.get_session_factory", boom, raising=True)
        result = await svc.resolve("u1")
        assert result == svc.FREE_ENTITLEMENT

    @pytest.mark.asyncio
    async def test_no_pass_exists_before_phase_4(self):
        """`_read_active_pass` is a named seam, not an oversight. `PlusPass` and the cached columns
        on `User` are created by Phase 4's migration; until then there is nothing to read, and the
        precedence tests above already cover what happens when there is."""
        assert await svc._read_active_pass("u1") is None

    def test_resolve_takes_a_user_id_and_nothing_else(self):
        """Decision F, enforced by signature. An optional `space_id` here is how a personal-scope
        resolver quietly becomes the space resolver, and space entitlement is out of scope."""
        import inspect

        assert list(inspect.signature(svc.resolve).parameters) == ["user_id"]


# ---------------------------------------------------------------------------
# The callers that used to disagree
# ---------------------------------------------------------------------------


class TestTheFourthOpinionIsGone:
    def test_require_premium_is_deleted(self):
        """Drift 8. It matched a six-tier tuple, knew nothing about trials, and gated no endpoint."""
        import src.shared.auth as auth
        import src.shared.auth.dependencies as deps

        for name in ("require_premium", "PremiumUser", "PAID_TIERS"):
            assert not hasattr(deps, name), f"{name} is back in shared.auth.dependencies"
            assert not hasattr(auth, name), f"{name} is back in shared.auth"
            assert name not in auth.__all__

    def test_feature_tier_service_reads_the_resolver(self, monkeypatch):
        """`get_effective_tier` keeps its `(tier, is_trial, days)` shape so its ~15 call sites need
        no edit, and a pass holder arrives there looking exactly like a subscriber."""
        import asyncio

        from src.domains.personal_learning.services import feature_tier_service

        async def fake_resolve(user_id):
            return compose(active_pass=A_PASS)

        monkeypatch.setattr(svc, "resolve", fake_resolve)
        assert asyncio.run(feature_tier_service.get_effective_tier("u1")) == ("plus", False, None)

    def test_the_llm_router_sees_a_trial(self, monkeypatch):
        """Drift 11. Before this delegation the model allowlist read `User.tier` alone, so a
        trialling learner got Plus quiz modes and flash-lite in the same request."""
        import asyncio

        from src.domains.intelligence.reasoning.llm.feature_flags import (
            PERSONAL_SCOPE,
            FeatureFlagService,
        )

        async def fake_resolve(user_id):
            return compose(active_trial=A_TRIAL)

        monkeypatch.setattr(svc, "resolve", fake_resolve)
        service = FeatureFlagService(
            enabled_providers="gemini",
            tier_allowlists={"free": "gemini:a", "plus": "gemini:b"},
        )
        resolved = asyncio.run(
            service.effective_tier_for_request(user_id="u1", scope=PERSONAL_SCOPE)
        )
        assert resolved == "plus"

    def test_personal_tier_cannot_be_passed_to_the_router(self):
        """The parameter is removed rather than ignored. A caller passing a pre-loaded `"FREE"` for a
        trialling learner would reintroduce drift 11 through the door marked optimisation."""
        import inspect

        from src.domains.intelligence.reasoning.llm.feature_flags import FeatureFlagService

        params = inspect.signature(FeatureFlagService.effective_tier_for_request).parameters
        assert "personal_tier" not in params
        # `seat_tier` stays: under space scope a seat tier is the whole answer.
        assert "seat_tier" in params
