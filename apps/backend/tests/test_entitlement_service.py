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
EARLIER = NOW - timedelta(days=1)
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

    def test_a_lapsed_subscription_grants_nothing(self):
        """All three sources expire lazily on read, not just two of them.

        Until Phase 2a a paid tier returned `plus` whatever `subscription_period_end` said, while
        pass and trial both checked. That is only safe if webhooks are the sole writer of
        `User.tier` and always land — and `handle_paystack_webhook` reaches a Prisma sentinel until
        Phase 2b, so a lost `subscription.disable` meant a tier that never returned to `FREE`.
        """
        result = compose(subscription_tier="PREMIUM_MONTHLY", subscription_period_end=EARLIER)
        assert result.tier == "free"
        assert result.source == "none"

    def test_a_lapsed_subscription_falls_through_to_a_pass(self):
        """Lapsing is not short-circuiting: the next source in precedence still gets its turn.

        A learner whose card failed and who then bought a pass to keep working is the case that
        makes this matter.
        """
        result = compose(
            subscription_tier="PREMIUM_MONTHLY",
            subscription_period_end=EARLIER,
            active_pass=A_PASS,
        )
        assert result.tier == "plus"
        assert result.source == "pass"

    def test_a_missing_period_end_is_not_read_as_lapsed(self):
        """Absent is not expired. Inferring cancellation from a null would revoke access from
        subscribers whose row predates the column being written."""
        result = compose(subscription_tier="PREMIUM_MONTHLY", subscription_period_end=None)
        assert result.tier == "plus"
        assert result.source == "subscription"

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
        """Nobody holds one, so a row that does is a data error rather than a subscriber.

        This assertion has been both ways round, and the history is the point. Revision 4 asserted
        `free` on a remembered product fact; Phase 2a reverted it to `plus`, because the resolver had
        narrowed while every *writer* still produced these five strings, so a renewal would have
        billed a learner and entitled them to nothing. Phase 2b asserts `free` again — this time
        with `scripts/count_legacy_commercial_state.py` run against production showing zero users on
        any of these tiers and zero subscription identifiers of any kind, **and** with the writers
        narrowed in the same change so the two cannot drift apart again.

        The measurement is what makes this safe, not the tidiness. If the count is ever non-zero,
        restore `LEGACY_PLUS_TIERS` *and* the writer mappings together.
        """
        result = compose(subscription_tier=tier, subscription_period_end=LATER)
        assert result.tier == "free"
        assert result.source == "none"
        # Still reported, because display and history need the raw value even when it grants nothing.
        assert result.subscription_tier == tier

    def test_the_active_tier_is_the_only_member(self):
        """One member, and no second set beside it — Phase 2b removed `LEGACY_PLUS_TIERS`."""
        assert svc.PLUS_TIERS == frozenset({"PREMIUM_MONTHLY"})
        assert not hasattr(svc, "LEGACY_PLUS_TIERS")
        assert not hasattr(svc, "ALL_PLUS_TIERS")

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
        largest allowance by default. Under-granting is a support ticket; over-granting is COGS.
        """
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
        nearly every gated request, so raising here would turn a database blip into an outage.
        """

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
        resolver quietly becomes the space resolver, and space entitlement is out of scope.
        """
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
        assert asyncio.run(feature_tier_service.get_effective_tier("u1")) == (
            "plus",
            False,
            None,
        )

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
        trialling learner would reintroduce drift 11 through the door marked optimisation.
        """
        import inspect

        from src.domains.intelligence.reasoning.llm.feature_flags import (
            FeatureFlagService,
        )

        params = inspect.signature(FeatureFlagService.effective_tier_for_request).parameters
        assert "personal_tier" not in params
        # `seat_tier` stays: under space scope a seat tier is the whole answer.
        assert "seat_tier" in params


# ---------------------------------------------------------------------------
# The request-scoped memo
# ---------------------------------------------------------------------------


class TestRequestScopedMemo:
    """Collapsing four mechanisms into one resolver means one request asks the same question
    several times — `check_capability` and `get_quality_tier` resolve independently, so the ask
    path pays the join at least twice per turn where it used to read a pre-loaded tier.

    The interesting assertions here are the two *negative* ones: no scope means no cache, and a
    write inside a scope is visible after it. Both are the difference between an optimisation and
    a stale-entitlement bug.
    """

    @pytest.mark.asyncio
    async def test_one_read_per_scope(self, monkeypatch):
        calls = []

        async def counting_read(user_id):
            calls.append(user_id)
            return compose(subscription_tier="PREMIUM_MONTHLY")

        monkeypatch.setattr(svc, "_resolve_uncached", counting_read)

        with svc.request_scope():
            first = await svc.resolve("u1")
            second = await svc.resolve("u1")

        assert calls == ["u1"]
        assert first is second

    @pytest.mark.asyncio
    async def test_users_do_not_share_an_entry(self, monkeypatch):
        async def by_user(user_id):
            tier = "PREMIUM_MONTHLY" if user_id == "paid" else "FREE"
            return compose(subscription_tier=tier)

        monkeypatch.setattr(svc, "_resolve_uncached", by_user)

        with svc.request_scope():
            assert (await svc.resolve("paid")).tier == "plus"
            assert (await svc.resolve("free")).tier == "free"

    @pytest.mark.asyncio
    async def test_nothing_is_cached_without_a_scope(self, monkeypatch):
        """`study_voice` relays run for minutes and bill every couple of seconds, and they never
        open a scope. A cache that existed by default would let a pass expire mid-session and go on
        being honoured until the learner hung up.
        """
        calls = []

        async def counting_read(user_id):
            calls.append(user_id)
            return compose()

        monkeypatch.setattr(svc, "_resolve_uncached", counting_read)

        await svc.resolve("u1")
        await svc.resolve("u1")

        assert calls == ["u1", "u1"]

    @pytest.mark.asyncio
    async def test_invalidate_makes_a_write_visible_in_the_same_request(self, monkeypatch):
        """`trial_service.start_trial` checks eligibility through `get_effective_tier` — which
        resolves and caches `free` — and only then writes the trial. Without the `invalidate()`
        call it makes, anything gated later in that request would deny the learner the trial they
        had just been granted.
        """
        state = {"tier": "FREE"}

        async def read_state(user_id):
            return compose(subscription_tier=state["tier"])

        monkeypatch.setattr(svc, "_resolve_uncached", read_state)

        with svc.request_scope():
            assert (await svc.resolve("u1")).tier == "free"
            state["tier"] = "PREMIUM_MONTHLY"
            assert (await svc.resolve("u1")).tier == "free", "cached, as designed"
            svc.invalidate("u1")
            assert (await svc.resolve("u1")).tier == "plus"

    def test_invalidate_outside_a_scope_is_a_no_op(self):
        """So that callers never have to know whether they are in a request."""
        svc.invalidate("nobody")

    @pytest.mark.asyncio
    async def test_the_scope_does_not_outlive_itself(self, monkeypatch):
        calls = []

        async def counting_read(user_id):
            calls.append(user_id)
            return compose()

        monkeypatch.setattr(svc, "_resolve_uncached", counting_read)

        with svc.request_scope():
            await svc.resolve("u1")
        with svc.request_scope():
            await svc.resolve("u1")

        assert calls == ["u1", "u1"]

    def test_the_middleware_is_installed_and_is_pure_asgi(self):
        """`BaseHTTPMiddleware` calls the downstream app from a different task, so a context
        variable set in `dispatch` reaches the endpoint only by the child task's context copy and
        nothing propagates back. A plain ASGI callable runs in the request's own task. Asserting the
        base class is asserting that reasoning has not been undone by a later refactor.
        """
        import inspect

        from starlette.middleware.base import BaseHTTPMiddleware

        from src.shared.middleware import EntitlementScopeMiddleware

        assert not issubclass(EntitlementScopeMiddleware, BaseHTTPMiddleware)
        assert inspect.iscoroutinefunction(EntitlementScopeMiddleware.__call__)

    @pytest.mark.asyncio
    async def test_the_middleware_scopes_http_and_skips_websockets(self, monkeypatch):
        """Websockets are excluded on purpose; see `test_nothing_is_cached_without_a_scope`."""
        from src.shared.middleware import EntitlementScopeMiddleware

        seen = {}

        async def downstream(scope, receive, send):
            seen[scope["type"]] = svc._REQUEST_CACHE.get()

        middleware = EntitlementScopeMiddleware(downstream)
        await middleware({"type": "http"}, None, None)
        await middleware({"type": "websocket"}, None, None)

        assert seen["http"] == {}
        assert seen["websocket"] is None
