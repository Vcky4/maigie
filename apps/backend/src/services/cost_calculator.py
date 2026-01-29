"""
Cost calculator for AI API usage.

This module handles:
- Calculating costs for different AI providers (Gemini, OpenAI, etc.)
- Calculating revenue based on user tier
- Tracking cost vs revenue margins

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Optional

# Gemini pricing (as of 2024)
# Gemini 1.5 Pro pricing per 1M tokens
GEMINI_15_PRO_INPUT_COST_PER_MILLION = 1.25  # $1.25 per 1M input tokens
GEMINI_15_PRO_OUTPUT_COST_PER_MILLION = 5.00  # $5.00 per 1M output tokens

# Gemini 1.5 Flash pricing per 1M tokens
GEMINI_15_FLASH_INPUT_COST_PER_MILLION = 0.075  # $0.075 per 1M input tokens
GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION = 0.30  # $0.30 per 1M output tokens

# Default model (if not specified)
DEFAULT_MODEL = "gemini-1.5-pro"
DEFAULT_INPUT_COST = GEMINI_15_PRO_INPUT_COST_PER_MILLION
DEFAULT_OUTPUT_COST = GEMINI_15_PRO_OUTPUT_COST_PER_MILLION


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
        model_name: Name of the model used (e.g., "gemini-1.5-pro", "gemini-1.5-flash")

    Returns:
        Cost in USD
    """
    if model_name is None:
        model_name = DEFAULT_MODEL

    # Determine pricing based on model
    if "flash" in model_name.lower():
        input_cost_per_million = GEMINI_15_FLASH_INPUT_COST_PER_MILLION
        output_cost_per_million = GEMINI_15_FLASH_OUTPUT_COST_PER_MILLION
    else:
        # Default to Pro pricing
        input_cost_per_million = GEMINI_15_PRO_INPUT_COST_PER_MILLION
        output_cost_per_million = GEMINI_15_PRO_OUTPUT_COST_PER_MILLION

    # Calculate costs
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
