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

import pytest  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.domains.billing.services.cost_calculator import calculate_ai_cost  # noqa: E402
from src.domains.intelligence.reasoning.llm.feature_flags import (  # noqa: E402
    FeatureFlagService,
)

PLUS_MODEL = "gemini-3.5-flash"
FREE_MODEL = "gemini-3.1-flash-lite"


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

    def test_free_can_use_flash_lite(self):
        assert _service().is_model_allowed("gemini", FREE_MODEL, "free", "u1") is True

    def test_plus_can_use_both(self):
        service = _service()
        assert service.is_model_allowed("gemini", PLUS_MODEL, "plus", "u1") is True
        assert service.is_model_allowed("gemini", FREE_MODEL, "plus", "u1") is True

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

    def test_free_has_exactly_one_gemini_candidate_in_the_chain(self):
        """Recorded rather than asserted as desirable.

        Narrowing Free to one model also narrows its chat fallback from two Gemini candidates to one,
        for the tier holding almost every user. That is a real trade and the alternative was worse:
        the previous "fallback" was a model six times the price, chosen first. Giving Free a genuine
        second candidate needs a cheap model registered as an adapter, which is not a config change —
        see drift item 23.
        """
        settings = get_settings()
        allowed = {p.strip() for p in settings.LLM_TIER_ALLOWLIST_FREE.split(",") if p.strip()}
        chain = [p.strip() for p in settings.FALLBACK_CHAT_DEFAULT.split(",")]
        assert [pair for pair in chain if pair in allowed] == [f"gemini:{FREE_MODEL}"]


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
        """Drift 23, asserted so it is not mistaken for solved.

        `route_request` is called from exactly one place — the Ask/chat turn. The other 26 LLM call
        sites reach providers through `llm_resilient`, which picks a provider from its own list and
        never asks about entitlement, so model quality is gated on chat and nowhere else. Quiz
        generation, lesson bodies, documents and the narrative panels all run the Plus model for a
        free learner.

        This test fails if a second caller of `route_request` appears, which would mean the scope of
        the gate changed and the plan's claim needs rechecking rather than re-reading.
        """
        import subprocess

        result = subprocess.run(
            ["grep", "-rn", "route_request", "src/"],
            capture_output=True,
            text=True,
            check=False,
        )
        call_sites = [
            line
            for line in result.stdout.splitlines()
            if ".py:" in line
            and "def route_request" not in line
            # Prose mentions in docstrings and comments, not calls.
            and ".route_request(" in line
        ]
        assert len(call_sites) == 1, f"expected one caller, found: {call_sites}"
        assert "ask_service.py" in call_sites[0]
