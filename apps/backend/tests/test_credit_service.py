"""Unit tests for credit helpers (pure logic, no database)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_credit_limits_known_tiers() -> None:
    from src.services.credit_service import get_credit_limits

    free = await get_credit_limits("FREE")
    assert free["hard_cap"] == 15000
    assert "daily_limit" in free

    prem = await get_credit_limits("PREMIUM_MONTHLY")
    assert prem["hard_cap"] == 300000


def test_apply_token_multiplier() -> None:
    from src.services.credit_service import TOKEN_MULTIPLIER, apply_token_multiplier

    assert TOKEN_MULTIPLIER == 0.2
    assert apply_token_multiplier(0) == 0
    assert apply_token_multiplier(10) == max(1, int(10 * TOKEN_MULTIPLIER))


def test_credit_costs_keys() -> None:
    from src.services.credit_service import CREDIT_COSTS

    assert "chat_message" in CREDIT_COSTS
    assert CREDIT_COSTS["ai_course_generation"] > 0
