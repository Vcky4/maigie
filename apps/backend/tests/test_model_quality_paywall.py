"""The model allowlist is the model-quality paywall, and it gated nothing.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.3 argues the free tier is not starved of conversation but of
*voice* and of *model quality*, and sizes the Free window at 500 units on that basis. The second half
was not true: `LLM_TIER_ALLOWLIST_FREE` listed `gemini-3.5-flash`, the same model Plus gets, and
because that model is also first in `FALLBACK_CHAT_DEFAULT` — and `router._select_candidates` keeps
the first allowed pair in chain order — every free chat turn ran on it.

With the corrected rate card that is $0.0174 a turn against $0.0029 on Flash-Lite. So 500 units
bought about 3 chat turns, not the ~16 the plan claims, and the plan's own description of the failure
it was fixing ("free got 250 voice-minutes/day and 3–5 chat turns") described the state it shipped.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_model_quality_paywall.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.domains.billing.services.cost_calculator import calculate_ai_cost  # noqa: E402
from src.domains.intelligence.reasoning.llm.feature_flags import (  # noqa: E402
    FeatureFlagService,
)

PLUS_MODEL = "gemini-3.5-flash"
FREE_MODEL = "gemini-3.1-flash-lite"
FREE_FALLBACK = "gemini-3.5-flash-lite"

_SRC = Path(__file__).resolve().parents[1] / "src"


def _read_src() -> str:
    """Every source file as one string, for the two tests that assert over the whole tree.

    `subprocess.run(["grep", ...])` is what these used, and it cannot run on Windows — no `grep` on
    the PATH, so the test raised `FileNotFoundError` rather than failing, and provided no signal at
    all on a developer machine. `encoding="utf-8"` is not optional either: the default on Windows is
    cp1252 and this tree is full of em-dashes.
    """
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in _SRC.rglob("*.py")
    )


def _service() -> FeatureFlagService:
    """Built from real settings on purpose.

    A service built from literals would assert what this file believes rather than what the
    application is configured to do — and the whole defect was a configured value disagreeing with a
    documented intent. `.env` pinning the old string is the failure mode this catches.
    """
    settings = get_settings()
    return FeatureFlagService(
        enabled_providers=settings.LLM_ENABLED_PROVIDERS,
        tier_allowlists={
            "free": settings.LLM_TIER_ALLOWLIST_FREE,
            "plus": settings.LLM_TIER_ALLOWLIST_PLUS,
        },
    )


class TestTheAllowlistGatesModelQuality:
    def test_free_cannot_use_the_plus_model(self):
        assert _service().is_model_allowed("gemini", PLUS_MODEL, "free", "u1") is False

    def test_free_can_use_both_flash_lite_models(self):
        service = _service()
        assert service.is_model_allowed("gemini", FREE_MODEL, "free", "u1") is True
        assert service.is_model_allowed("gemini", FREE_FALLBACK, "free", "u1") is True

    def test_plus_can_use_all_three(self):
        service = _service()
        for model in (PLUS_MODEL, FREE_MODEL, FREE_FALLBACK):
            assert service.is_model_allowed("gemini", model, "plus", "u1") is True

    def test_the_two_tiers_do_not_name_the_same_model_set(self):
        """If they match, the allowlist is not a paywall — it is a list."""
        settings = get_settings()
        free = {p.strip() for p in settings.LLM_TIER_ALLOWLIST_FREE.split(",") if p.strip()}
        plus = {p.strip() for p in settings.LLM_TIER_ALLOWLIST_PLUS.split(",") if p.strip()}
        assert free != plus
        assert free < plus, "free must be a strict subset of plus"


class TestTheChainOrderIsWhyThisMattered:
    def test_the_plus_model_is_first_in_the_default_chat_chain(self):
        """The allowlist entry alone was not the defect; its position was.

        `_select_candidates` walks `FALLBACK_CHAT_DEFAULT` in order and keeps the first allowed pair,
        so listing the expensive model for Free did not make it a fallback — it made it the default.
        Leaving it first is correct for Plus; the fix is the allowlist, not the chain.
        """
        chain = [p.strip() for p in get_settings().FALLBACK_CHAT_DEFAULT.split(",")]
        assert chain[0] == f"gemini:{PLUS_MODEL}"

    def test_free_has_two_candidates_cheapest_first(self):
        """Decision P. A single-candidate tier fails rather than degrades, and this is the tier
        holding 1 205 of 1 206 accounts.

        Order matters as much as membership: the chain is walked in order, so the cheaper model has to
        come first or the fallback becomes the default — which is precisely how the original defect
        worked, with the Plus model listed for Free and sitting first.
        """
        settings = get_settings()
        allowed = {p.strip() for p in settings.LLM_TIER_ALLOWLIST_FREE.split(",") if p.strip()}
        chain = [p.strip() for p in settings.FALLBACK_CHAT_DEFAULT.split(",")]
        assert [pair for pair in chain if pair in allowed] == [
            f"gemini:{FREE_MODEL}",
            f"gemini:{FREE_FALLBACK}",
        ]

    def test_the_free_fallback_is_registered(self):
        """An unregistered model is not an error, it is an absence — `_select_candidates` skips any
        pair without an adapter. So a second candidate that was never registered would leave Free on
        one model and nothing would say so.
        """
        from src.domains.intelligence.reasoning.llm.adapter_registry import (
            _build_adapter_registry,
        )

        assert f"gemini:{FREE_FALLBACK}" in _build_adapter_registry()

    def test_the_free_fallback_costs_a_third_more_not_six_times_more(self):
        """What makes it a fallback rather than a cost leak.

        `gemini-3.5-flash-lite` is *dearer* per token than the primary — newer, better per unit of
        capability, $0.30/$2.50 against $0.25/$1.50. The comparison that earns it the slot is against
        what Free used to fall back to, which was the Plus model at six times the primary.
        """
        primary = calculate_ai_cost(8000, 600, model_name=FREE_MODEL)
        fallback = calculate_ai_cost(8000, 600, model_name=FREE_FALLBACK)
        plus = calculate_ai_cost(8000, 600, model_name=PLUS_MODEL)
        assert 1.2 < fallback / primary < 1.5
        assert fallback < plus / 4

    def test_no_shut_down_model_is_in_a_fallback_chain(self):
        """The trap this fix nearly walked into.

        `gemini-2.5-flash-lite` is the cheapest row in the pricing table at $0.10/$0.40, and an
        earlier draft proposed it as Free's second candidate. The whole Gemini 2.5 family shuts down
        in October 2026 and the 2.0 models already have — facts a price table does not carry, because
        a price table records what things cost, not what you may still call.
        """
        settings = get_settings()
        chains = f"{settings.FALLBACK_CHAT_DEFAULT},{settings.FALLBACK_CHAT_TOOLS}"
        for retired in ("gemini-2.5-", "gemini-2.0-", "gemini-3.1-flash-lite-preview"):
            assert retired not in chains, f"{retired} is shut down or shutting down"


class TestTheCostDifferenceIsWhyItWasWorthFixing:
    def test_a_free_chat_turn_costs_what_flash_lite_costs(self):
        """8k in / 600 out is the reference chat turn in §6.2."""
        assert abs(calculate_ai_cost(8000, 600, model_name=FREE_MODEL) - 0.0029) < 1e-4

    def test_the_plus_model_costs_six_times_as_much(self):
        free = calculate_ai_cost(8000, 600, model_name=FREE_MODEL)
        plus = calculate_ai_cost(8000, 600, model_name=PLUS_MODEL)
        assert 5.5 < plus / free < 6.5


class TestScope:
    def test_only_the_chat_router_consults_the_allowlist(self):
        """`route_request` still has exactly one caller, and that is now a statement about the
        *allowlist* rather than about the paywall.

        This test was written to assert drift 23 was open: the allowlist gated chat, the other 26 call
        sites went through `llm_resilient`, and nothing there asked about entitlement. Drift 23 is
        closed, and it was closed without adding a second `route_request` caller — `llm_resilient`
        chooses a model from `registry` against the entitlement resolver instead of routing through
        the chain. So the two mechanisms remain separate, and this test guards the separation: a
        second caller here would mean generation had been pushed through the chat router, which is a
        design change worth noticing rather than a refactor.

        What covers the paywall itself is `TestTheQualitySplitAtTheChokepoint` below.
        """
        call_sites = [
            f"{path.name}:{number}"
            for path in _SRC.rglob("*.py")
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            )
            # Calls, not the definition, and not prose mentions in docstrings.
            if ".route_request(" in line and "def route_request" not in line
        ]
        assert len(call_sites) == 1, f"expected one caller, found: {call_sites}"
        assert "ask_service.py" in call_sites[0]


# ---------------------------------------------------------------------------
# Drift 23: the other 26 call sites
# ---------------------------------------------------------------------------


class TestTheQualitySplitAtTheChokepoint:
    """`llm_resilient` now picks a model by entitlement, which is what closed drift 23.

    The allowlist above gates chat. It is read by `router` alone and `router` is called from the Ask
    turn alone, so for as long as it was the whole paywall, "Plus gets advanced models" was true of
    one surface out of twenty-seven — a free learner's quizzes, lessons, documents and narrative
    panels all ran `gemini-3.5-flash`.
    """

    @staticmethod
    def _resolve_as(monkeypatch, tier):
        """Stub the resolver, not the tier string.

        `_compose` is the thing that knows a trial and a pass are Plus, so stubbing at
        `entitlement_service.resolve` keeps that intact — a test that patched a `"FREE"` string in
        would pass while a trialling learner got the wrong model, which is drift 11's shape.
        """
        from src.domains.billing.services import entitlement_service

        async def fake_resolve(user_id):
            return entitlement_service._compose(
                subscription_tier="PREMIUM_MONTHLY" if tier == "plus" else "FREE",
                subscription_period_end=None,
                active_pass=None,
                active_trial=None,
            )

        monkeypatch.setattr(entitlement_service, "resolve", fake_resolve)

    @pytest.mark.asyncio
    async def test_plus_gets_the_dear_model_above_the_threshold(self, monkeypatch):
        from src.domains.personal_learning.services import llm_resilient

        self._resolve_as(monkeypatch, "plus")
        assert (
            await llm_resilient.model_for_operation(user_id="u1", operation="lesson_body")
            == PLUS_MODEL
        )

    @pytest.mark.asyncio
    async def test_free_does_not_above_the_threshold(self, monkeypatch):
        """The defect, in one assertion."""
        from src.domains.personal_learning.services import llm_resilient

        self._resolve_as(monkeypatch, "free")
        assert (
            await llm_resilient.model_for_operation(user_id="u1", operation="lesson_body")
            == FREE_MODEL
        )

    @pytest.mark.asyncio
    async def test_below_the_threshold_both_tiers_match(self, monkeypatch):
        """Decision P's actual content. Downgrading a 100-unit operation saves $0.008 and costs a
        visible quality drop on something a learner sees constantly, so below the line there is no
        split at all — and the honest Free-versus-Plus story on those surfaces is the allowance.
        """
        from src.domains.personal_learning.services import llm_resilient

        for tier in ("free", "plus"):
            self._resolve_as(monkeypatch, tier)
            assert (
                await llm_resilient.model_for_operation(user_id="u1", operation="note_summary")
                == FREE_MODEL
            )

    @pytest.mark.asyncio
    async def test_an_unattributed_call_gets_the_cheap_model(self):
        """`user_id=None` is a system-initiated generation with nobody to charge, and the safe
        reading of "nobody in particular" is the cheap model. The opposite default would mean an
        unattributed call silently costs 6× and is billed to no one — which is what every one of
        these call sites was doing before this change.
        """
        from src.domains.personal_learning.services import llm_resilient

        assert (
            await llm_resilient.model_for_operation(user_id=None, operation="lesson_body")
            == FREE_MODEL
        )

    @pytest.mark.asyncio
    async def test_a_failed_resolve_falls_to_the_cheap_model(self, monkeypatch):
        """Note the direction, and that it differs from the gate beside it on purpose.

        `_refuse_if_exhausted` fails **open**, because refusing a paying learner over a database blip
        is worse than one unbilled call. This fails **closed on cost**: the wrong answer here is not
        an outage, it is silently serving the expensive model, so it degrades instead.
        """
        from src.domains.billing.services import entitlement_service
        from src.domains.personal_learning.services import llm_resilient

        async def boom(user_id):
            raise RuntimeError("no database")

        monkeypatch.setattr(entitlement_service, "resolve", boom)
        assert (
            await llm_resilient.model_for_operation(user_id="u1", operation="lesson_body")
            == FREE_MODEL
        )

    def test_the_exempt_operations_are_never_degraded(self):
        """§6.6 exempts onboarding auto-setup and memory extraction from *charging*, and Decision P's
        argument is that exempting them from the meter and then quietly serving them a worse model
        would honour the letter and lose the point.

        A threshold gets this without a second exception list, which is the reason to prefer a
        threshold over an operation-by-operation table — so the property to assert is that the two
        sets do not intersect, not that some rule keeps them apart.
        """
        from src.domains.personal_learning.services.llm_resilient import (
            QUALITY_SPLIT_OPERATIONS,
            UNCHARGED_OPERATIONS,
        )

        assert not (QUALITY_SPLIT_OPERATIONS & UNCHARGED_OPERATIONS)

    def test_the_split_names_the_same_two_models_the_allowlist_does(self):
        """Otherwise "Plus gets the better model" means two different things on two surfaces, and a
        learner comparing a chat answer with a generated lesson would be comparing three models.
        """
        from src.domains.intelligence.reasoning.llm.registry import (
            LlmTask,
            default_model_for,
        )

        assert default_model_for(LlmTask.GENERATION_PREMIUM) == PLUS_MODEL
        assert default_model_for(LlmTask.GENERATION_STANDARD) == FREE_MODEL

    def test_the_standard_model_is_the_cheaper_of_free_two_candidates(self):
        """§6.10's roster says under-500 operations run `gemini-3.5-flash-lite`, and that row is
        wrong: it is the *dearer* of Free's two models ($0.30/$2.50 against $0.25/$1.50) and is
        Free's chat fallback for exactly that reason. Picking it as the standard generation model
        would raise the cost of twenty-odd operations by ~60% on output to no end.
        """
        from src.domains.intelligence.reasoning.llm.registry import (
            LlmTask,
            default_model_for,
        )

        standard = default_model_for(LlmTask.GENERATION_STANDARD)
        assert calculate_ai_cost(8000, 600, model_name=standard) < calculate_ai_cost(
            8000, 600, model_name=FREE_FALLBACK
        )

    def test_every_split_operation_is_emitted_by_a_call_site(self):
        """A member nothing produces is not coverage, it is a comment that looks like code.

        `resource_recommendations` is the one that makes this worth asserting: it is emitted from
        `resource_service`'s grounded search step, which cannot go through `llm_resilient` at all, so
        it reaches the split through the exported `model_for_operation` rather than through the
        wrapper. If that call were removed the label would still be in the set and the most
        expensive operation in the product would quietly stop being gated.
        """
        from src.domains.personal_learning.services.llm_resilient import (
            QUALITY_SPLIT_OPERATIONS,
        )

        emitted = _read_src()
        for operation in sorted(QUALITY_SPLIT_OPERATIONS):
            assert (
                f'operation="{operation}"' in emitted
            ), f"{operation} is in the split but no call site passes it"

    def test_no_per_operation_unit_table_has_come_back(self):
        """The commit before this one deleted `ESTIMATED_OPERATION_UNITS`, and the reason it deleted
        it rather than correcting it was that the numbers in it were estimates wearing the costume of
        measurements — its ancestor priced a voice minute two orders of magnitude low for the life of
        the product.

        Decision P's threshold is denominated in those same units, so implementing it invites the
        table straight back. The split is a set of names instead, and the estimates live in a comment
        where they cannot be arithmetic. This fails if any module-level mapping from an operation name
        to a number reappears in the chokepoint.
        """
        from src.domains.personal_learning.services import llm_resilient

        for name in dir(llm_resilient):
            value = getattr(llm_resilient, name)
            if not isinstance(value, dict) or not value:
                continue
            assert not all(
                isinstance(k, str) and isinstance(v, int | float) for k, v in value.items()
            ), f"llm_resilient.{name} looks like a per-operation cost table"

    @pytest.mark.asyncio
    async def test_the_model_reaches_gemini_and_not_the_other_providers(self, monkeypatch):
        """`model` and `thinking` are Gemini-only, and passing either to `_call_openai` would be a
        `TypeError` presenting as a provider failure — caught by the attempt loop, retried, and
        eventually reported as every provider being unavailable.

        The honest consequence, recorded on `_call_gemini`: the paywall exists on the Gemini path
        alone, so a free learner whose Gemini attempts all fail falls through to
        `OPENAI_DEFAULT_MODEL`, which no allowlist has been consulted about.
        """
        import inspect

        from src.domains.personal_learning.services import llm_resilient

        assert "model" in inspect.signature(llm_resilient._call_gemini).parameters
        for other in (llm_resilient._call_openai, llm_resilient._call_anthropic):
            params = inspect.signature(other).parameters
            assert "model" not in params
            assert "thinking" not in params

    def test_both_gemini_entry_points_accept_a_model_and_default_to_chat(self):
        """`generate_grounded_content` is the one that matters. It is the only generation in the
        product that cannot go through `llm_resilient` — the search tool has no OpenAI or Anthropic
        equivalent — and it is also the most expensive one, so it was the last place that could afford
        to keep serving the Plus model to everybody.
        """
        import inspect

        from src.domains.intelligence.reasoning import llm as llm_module

        for fn in (
            llm_module.generate_content_with_usage,
            llm_module.generate_grounded_content,
        ):
            parameter = inspect.signature(fn).parameters.get("model")
            assert parameter is not None, f"{fn.__name__} takes no model"
            assert parameter.default is None, f"{fn.__name__} should default to CHAT_DEFAULT"


# ---------------------------------------------------------------------------
# Turning a provider off has to turn it off everywhere
# ---------------------------------------------------------------------------


class TestTheGlobalProviderSwitchReachesGeneration:
    """`LLM_ENABLED_PROVIDERS` reached the chat path and nothing else.

    `router.py`'s own docstring states the rule — "turning a provider off must turn it off
    everywhere" — and `llm_resilient` was the half of *everywhere* that was missing. It hardcoded
    `["gemini", "openai", "anthropic"]` for fallbacks and checked a learner's stored preference
    against `SUPPORTED_PROVIDERS`, so disabling OpenAI in production would have left it serving all
    27 generation surfaces.

    This matters beyond tidiness because the quality split only exists on the Gemini path: a
    fallback to OpenAI gets a model no allowlist was consulted about, and possibly a dearer one than
    the Plus model the split was avoiding.
    """

    @staticmethod
    def _enable(monkeypatch, value):
        from src.config import get_settings
        from src.domains.personal_learning.services import llm_resilient

        monkeypatch.setattr(get_settings(), "LLM_ENABLED_PROVIDERS", value, raising=False)
        return llm_resilient

    def test_a_disabled_provider_is_not_a_fallback(self, monkeypatch):
        llm_resilient = self._enable(monkeypatch, "gemini")
        assert llm_resilient._get_fallback_providers("gemini") == []

    def test_an_enabled_provider_still_is(self, monkeypatch):
        llm_resilient = self._enable(monkeypatch, "gemini,openai")
        assert llm_resilient._get_fallback_providers("gemini") == ["openai"]

    def test_the_order_is_the_supported_order_not_the_configured_order(self, monkeypatch):
        """So that the fallback sequence is a property of the code rather than of how someone typed
        an environment variable. Gemini first because it is the only provider the quality split and
        the `thinking` bound reach.
        """
        llm_resilient = self._enable(monkeypatch, "anthropic,openai,gemini")
        assert llm_resilient.enabled_providers() == ("gemini", "openai", "anthropic")

    @pytest.mark.asyncio
    async def test_a_learners_preference_cannot_re_enable_a_disabled_provider(self, monkeypatch):
        """The worse half of the defect. A stored `preferred_llm_provider` of `"openai"` was validated
        against `SUPPORTED_PROVIDERS`, so it became the learner's **primary** provider for every
        generation — a disabled provider reached first rather than last.
        """
        llm_resilient = self._enable(monkeypatch, "gemini")

        class _Profile:
            preferred_llm_provider = "openai"

        async def fake_get_profile(user_id):
            return _Profile()

        from src.domains.personal_learning import repository

        monkeypatch.setattr(
            repository.personal_learning_repo, "get_profile_by_user", fake_get_profile
        )
        assert await llm_resilient._resolve_provider("u1") == "gemini"

    @pytest.mark.asyncio
    async def test_a_disabled_default_is_not_returned(self, monkeypatch):
        """`_DEFAULT_PROVIDER` is a constant and is not guaranteed to be enabled. Returning it anyway
        would put the whole product on the one provider the operator switched off.
        """
        llm_resilient = self._enable(monkeypatch, "anthropic")
        assert await llm_resilient._resolve_provider(None) == "anthropic"

    def test_disabling_everything_is_read_as_a_mistake_not_an_instruction(self, monkeypatch):
        """A configuration naming no callable provider takes every AI surface in the product down at
        once, so it is far more likely to be a typo than a decision. It degrades to the default and
        logs at `error` — loud without being an outage.
        """
        llm_resilient = self._enable(monkeypatch, "")
        assert llm_resilient.enabled_providers() == (llm_resilient._DEFAULT_PROVIDER,)

        llm_resilient = self._enable(monkeypatch, "not-a-provider")
        assert llm_resilient.enabled_providers() == (llm_resilient._DEFAULT_PROVIDER,)
