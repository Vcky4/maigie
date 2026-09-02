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
