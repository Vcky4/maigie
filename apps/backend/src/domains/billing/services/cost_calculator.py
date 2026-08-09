"""Cost and revenue attribution for AI API usage.

Restored from the pre-migration ``services/cost_calculator``. The stub it replaces was
``def calculate_ai_cost(*args, **kwargs) -> float: return 0.0``, so every AI call was
recorded as free: cost tracking, revenue attribution and margin reporting all read zero
regardless of usage. That is worse than missing data, because the numbers look valid.

Pricing is USD per million tokens on the Gemini API paid tier, standard. Update
``_EXACT_MODEL_PRICING`` when Google publishes changes; see
https://ai.google.dev/gemini-api/docs/pricing.

Changes from the original: ``LlmTask``/``default_model_for`` moved to
``domains.intelligence.reasoning.llm.registry``, and the default model is resolved on
first use rather than at import so that the billing package does not evaluate the
intelligence registry while modules are still loading.
"""

from __future__ import annotations

from functools import lru_cache

# Legacy Gemini 1.5 (explicit ids only)
GEMINI_15_PRO_INPUT_COST_PER_MILLION = 1.25
GEMINI_15_PRO_OUTPUT_COST_PER_MILLION = 5.00
GEMINI_15_FLASH_INPUT_COST_PER_MILLION = 0.075
GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION = 0.30

# USD per 1M tokens (input, output).
_EXACT_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Current models
    "gemini-3.5-flash": (0.50, 3.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-embedding-001": (0.15, 0.0),
    # Legacy, kept for historical cost attribution
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-1.5-pro": (
        GEMINI_15_PRO_INPUT_COST_PER_MILLION,
        GEMINI_15_PRO_OUTPUT_COST_PER_MILLION,
    ),
    "gemini-1.5-flash": (
        GEMINI_15_FLASH_INPUT_COST_PER_MILLION,
        GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION,
    ),
}

# Premium value attributed per million tokens, used to express what a subscriber's
# usage is worth rather than to bill them; subscribers pay a subscription.
PREMIUM_TOKEN_VALUE_PER_MILLION = 10.0


@lru_cache(maxsize=1)
def _default_model() -> str:
    from src.domains.intelligence.reasoning.llm.registry import LlmTask, default_model_for

    return default_model_for(LlmTask.CHAT_DEFAULT)


def _normalize_model_id(model_name: str | None) -> str:
    if not model_name:
        return ""
    normalized = model_name.strip().lower()
    if normalized.startswith("models/"):
        normalized = normalized.removeprefix("models/")
    return normalized


def _pricing_for_model(model_name: str | None) -> tuple[float, float]:
    """Return ``(input_cost_per_million, output_cost_per_million)`` in USD.

    Unknown ids fall back through family heuristics, then to the legacy Pro tier, which
    is the most expensive entry and so errs towards over-stating cost rather than
    under-stating it.
    """
    normalized = _normalize_model_id(model_name)
    if not normalized:
        normalized = _normalize_model_id(_default_model())

    if normalized in _EXACT_MODEL_PRICING:
        return _EXACT_MODEL_PRICING[normalized]

    if "embedding" in normalized:
        return _EXACT_MODEL_PRICING["gemini-embedding-001"]

    # Preview and variant ids not listed exactly above.
    if "gemini-3" in normalized and "lite" in normalized:
        return _EXACT_MODEL_PRICING["gemini-3.1-flash-lite"]
    if "gemini-3" in normalized and "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-3.5-flash"]
    if "2.5" in normalized and "flash-lite" in normalized:
        return _EXACT_MODEL_PRICING["gemini-2.5-flash-lite"]
    if "2.5" in normalized and "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-2.5-flash"]
    if "2.0" in normalized and "flash-lite" in normalized:
        return _EXACT_MODEL_PRICING["gemini-2.0-flash-lite"]
    if "2.0" in normalized and "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-2.0-flash"]
    if "1.5" in normalized and "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-1.5-flash"]
    if "1.5" in normalized and "pro" in normalized:
        return _EXACT_MODEL_PRICING["gemini-1.5-pro"]
    if "flash-lite" in normalized:
        return _EXACT_MODEL_PRICING["gemini-3.1-flash-lite"]
    if "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-3.5-flash"]

    return (
        GEMINI_15_PRO_INPUT_COST_PER_MILLION,
        GEMINI_15_PRO_OUTPUT_COST_PER_MILLION,
    )


def calculate_ai_cost(
    input_tokens: int,
    output_tokens: int,
    model_name: str | None = None,
) -> float:
    """Cost of one AI API call in USD."""
    input_cost_per_million, output_cost_per_million = _pricing_for_model(model_name)
    input_cost = (input_tokens / 1_000_000) * input_cost_per_million
    output_cost = (output_tokens / 1_000_000) * output_cost_per_million
    return input_cost + output_cost


def calculate_revenue(
    input_tokens: int,
    output_tokens: int,
    user_tier: str,
) -> float:
    """Revenue attributed to one AI API call in USD.

    Free-tier usage earns nothing. Paid tiers are billed by subscription rather than by
    token, so this expresses the value of the tokens consumed, which is what the margin
    reporting compares against cost.
    """
    if user_tier == "FREE":
        return 0.0

    total_tokens = input_tokens + output_tokens
    return (total_tokens / 1_000_000) * PREMIUM_TOKEN_VALUE_PER_MILLION


def calculate_profit_margin(
    cost_usd: float,
    revenue_usd: float,
) -> tuple[float, float]:
    """Return ``(profit_usd, profit_margin_percentage)``.

    Margin is 0.0 when there is no revenue, rather than dividing by zero.
    """
    profit_usd = revenue_usd - cost_usd
    profit_margin = (profit_usd / revenue_usd * 100) if revenue_usd > 0 else 0.0
    return profit_usd, profit_margin
