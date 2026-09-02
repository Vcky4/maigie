"""Unit tests for cost_calculator (no database). Run with: SKIP_DB_FIXTURE=1 pytest tests/test_cost_calculator.py"""

import os

import pytest

# Ensure conftest autouse DB fixture does not require DATABASE_URL for this module.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.domains.billing.services.cost_calculator import calculate_ai_cost
from src.domains.intelligence.reasoning.llm.registry import LlmTask, default_model_for


@pytest.mark.parametrize(
    "model_name,input_t,output_t,expected_min,expected_max",
    [
        # 1M in + 1M out at listed rate should land on exact sum for exact ids.
        #
        # `gemini-3.5-flash` was asserted at 0.50 + 3.00 here, matching a table that was wrong.
        # Published rates are $1.50 / $9.00, so the meter under-stated the cost of the model Plus
        # learners use by 3× and this test agreed with it — Phase 0 Question 3.
        ("gemini-3.5-flash", 1_000_000, 1_000_000, 1.50 + 9.00, 1.50 + 9.00),
        ("gemini-3.1-flash-lite", 1_000_000, 1_000_000, 0.25 + 1.50, 0.25 + 1.50),
        ("gemini-embedding-001", 1_000_000, 0, 0.15, 0.15),
    ],
)
def test_calculate_ai_cost_exact_models(model_name, input_t, output_t, expected_min, expected_max):
    cost = calculate_ai_cost(input_t, output_t, model_name=model_name)
    assert expected_min <= cost <= expected_max


def test_calculate_ai_cost_models_prefix_stripped():
    cost = calculate_ai_cost(1_000_000, 0, model_name="models/gemini-3.5-flash")
    assert abs(cost - 1.50) < 1e-9


def test_calculate_ai_cost_none_model_uses_registry_default():
    expected = calculate_ai_cost(1000, 1000, model_name=default_model_for(LlmTask.CHAT_DEFAULT))
    assert calculate_ai_cost(1000, 1000, model_name=None) == expected


def test_calculate_ai_cost_unknown_non_flash_uses_pro_fallback():
    cost = calculate_ai_cost(1_000_000, 1_000_000, model_name="some-vendor-unknown-model")
    # Pro-tier legacy constants: 1.25 + 5.00
    assert abs(cost - 6.25) < 1e-6


# ---------------------------------------------------------------------------
# Non-Gemini models, and the two tables that describe the same fact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        ("gpt-4o-mini", 0.15 + 0.60),
        ("gpt-4o", 2.50 + 10.00),
        ("claude-sonnet-4-20250514", 3.00 + 15.00),
        ("claude-haiku-3-5", 0.80 + 4.00),
    ],
)
def test_non_gemini_models_are_priced_exactly(model_name, expected):
    """These were reachable and unpriced.

    `router._select_candidates` draws from `FALLBACK_CHAT_DEFAULT`, whose third entry is
    `openai:gpt-4o-mini`, and `adapter_registry` registers both OpenAI models whenever
    `OPENAI_API_KEY` is set. A chat turn that fell through to OpenAI was priced by
    `_pricing_for_model`'s last resort — the legacy Pro tier at $1.25/$5.00 — so `gpt-4o-mini`
    was costed at roughly 8× its real rate.
    """
    cost = calculate_ai_cost(1_000_000, 1_000_000, model_name=model_name)
    assert abs(cost - expected) < 1e-9


def test_the_two_pricing_tables_agree():
    """One fact, two tables, and last time they disagreed it cost the plan every COGS figure.

    `billing.cost_calculator._EXACT_MODEL_PRICING` (USD per 1M) and
    `intelligence.reasoning.llm.cost_tracker.PROVIDER_PRICING` (USD per token, provider-prefixed)
    both price models. `gemini-3.5-flash` was wrong in both at $0.50/$3.00 against a published
    $1.50/$9.00, and `gemini-3.1-flash-lite` being right in both is what made the other look
    plausible.

    Asserted on the overlap rather than on equality of key sets: the tables legitimately differ in
    coverage, since one carries ids for historical attribution that no adapter can produce. What they
    must not do is disagree about a model they both name.
    """
    from src.domains.billing.services.cost_calculator import _EXACT_MODEL_PRICING
    from src.domains.intelligence.reasoning.llm.cost_tracker import PROVIDER_PRICING

    disagreements = []
    for prefixed, (tracker_in, tracker_out) in PROVIDER_PRICING.items():
        model = prefixed.split(":", 1)[1]
        if model not in _EXACT_MODEL_PRICING:
            continue
        calc_in, calc_out = _EXACT_MODEL_PRICING[model]
        # PROVIDER_PRICING is per token; _EXACT_MODEL_PRICING is per million.
        if abs(tracker_in * 1e6 - calc_in) > 1e-9 or abs(tracker_out * 1e6 - calc_out) > 1e-9:
            disagreements.append(
                f"{model}: cost_tracker={tracker_in * 1e6}/{tracker_out * 1e6} "
                f"cost_calculator={calc_in}/{calc_out}"
            )

    assert not disagreements, "pricing tables disagree: " + "; ".join(disagreements)
