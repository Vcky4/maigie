"""
Cost calculator for AI API usage.

This module handles:
- Calculating costs for different AI providers (Gemini today; OpenAI / Anthropic later)
- Calculating revenue based on user tier
- Tracking cost vs revenue margins

Pricing for Gemini text is aligned with Google AI Gemini API **Paid tier, Standard**
where applicable. Update `_EXACT_MODEL_PRICING` and heuristics when Google publishes
changes (see https://ai.google.dev/gemini-api/docs/pricing).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from src.services.llm_registry import LlmTask, default_model_for

# Legacy Gemini 1.5 (explicit ids only)
GEMINI_15_PRO_INPUT_COST_PER_MILLION = 1.25
GEMINI_15_PRO_OUTPUT_COST_PER_MILLION = 5.00
GEMINI_15_FLASH_INPUT_COST_PER_MILLION = 0.075
GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION = 0.30

# USD per 1M tokens (input, output) — Paid tier Standard from Gemini API pricing page.
_EXACT_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-embedding-001": (0.15, 0.0),
    "gemini-1.5-pro": (
        GEMINI_15_PRO_INPUT_COST_PER_MILLION,
        GEMINI_15_PRO_OUTPUT_COST_PER_MILLION,
    ),
    "gemini-1.5-flash": (
        GEMINI_15_FLASH_INPUT_COST_PER_MILLION,
        GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION,
    ),
}

# Gemini 3.1 Flash-Lite Standard (text / image / video)
_GEMINI_31_FLASH_LITE = (0.25, 1.50)

DEFAULT_MODEL = default_model_for(LlmTask.CHAT_DEFAULT)


def _normalize_model_id(model_name: str | None) -> str:
    if not model_name:
        return ""
    n = model_name.strip().lower()
    if n.startswith("models/"):
        n = n.removeprefix("models/")
    return n


def _pricing_for_model(model_name: str | None) -> tuple[float, float]:
    """
    Return (input_cost_per_million, output_cost_per_million) in USD.
    Unknown models fall back to sensible Gemini Flash-tier defaults.
    """
    normalized = _normalize_model_id(model_name)
    if not normalized:
        normalized = _normalize_model_id(DEFAULT_MODEL)

    if normalized in _EXACT_MODEL_PRICING:
        return _EXACT_MODEL_PRICING[normalized]

    if "embedding" in normalized:
        return _EXACT_MODEL_PRICING["gemini-embedding-001"]

    # Preview / variant ids not listed exactly above
    if "gemini-3" in normalized and "lite" in normalized:
        return _GEMINI_31_FLASH_LITE
    if "gemini-3" in normalized and "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-3-flash-preview"]
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
        return _EXACT_MODEL_PRICING["gemini-2.5-flash-lite"]
    if "flash" in normalized:
        return _EXACT_MODEL_PRICING["gemini-2.5-flash"]

    # Non-flash / unknown: use Pro-tier legacy as upper-bound-ish default
    return (
        GEMINI_15_PRO_INPUT_COST_PER_MILLION,
        GEMINI_15_PRO_OUTPUT_COST_PER_MILLION,
    )


def calculate_ai_cost(
    input_tokens: int,
    output_tokens: int,
    model_name: str | None = None,
) -> float:
    """
    Calculate the cost of an AI API call in USD.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model_name: Model id (e.g. gemini-2.5-flash, gemini-3-flash-preview)

    Returns:
        Cost in USD
    """
    input_cost_per_million, output_cost_per_million = _pricing_for_model(model_name)

    input_cost = (input_tokens / 1_000_000) * input_cost_per_million
    output_cost = (output_tokens / 1_000_000) * output_cost_per_million

    return input_cost + output_cost


def calculate_revenue(
    input_tokens: int,
    output_tokens: int,
    user_tier: str,
) -> float:
    """
    Calculate the revenue from a user based on their tier.

    Note: For FREE tier, revenue is $0. For premium tiers, we could charge
    per token or include it in subscription. For now, we'll use a simple
    per-token pricing model for premium users.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        user_tier: User's subscription tier (FREE, PREMIUM_MONTHLY, PREMIUM_YEARLY)

    Returns:
        Revenue in USD (0 for FREE tier, calculated for premium tiers)
    """
    if user_tier == "FREE":
        return 0.0

    # For premium tiers, calculate revenue based on token usage
    # This is a simplified model - in reality, premium users pay a subscription
    # and get included tokens. This represents the "value" of the tokens used.
    total_tokens = input_tokens + output_tokens

    # Premium pricing: $0.01 per 1K tokens (or $10 per 1M tokens)
    # This represents the value users get from their subscription
    premium_token_value_per_million = 10.0
    revenue = (total_tokens / 1_000_000) * premium_token_value_per_million

    return revenue


def calculate_profit_margin(
    cost_usd: float,
    revenue_usd: float,
) -> tuple[float, float]:
    """
    Calculate profit margin and percentage.

    Args:
        cost_usd: Cost in USD
        revenue_usd: Revenue in USD

    Returns:
        Tuple of (profit_usd, profit_margin_percentage)
    """
    profit_usd = revenue_usd - cost_usd
    profit_margin = (profit_usd / revenue_usd * 100) if revenue_usd > 0 else 0.0

    return profit_usd, profit_margin
