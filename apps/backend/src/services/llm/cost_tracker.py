"""
Cost tracker for multi-provider LLM usage.

Records per-request costs with provider-specific pricing tables and persists
cost records to the database for aggregation and spend monitoring.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from prisma import Prisma

logger = logging.getLogger(__name__)

# USD per token (not per million) for direct multiplication.
# Format: "provider:model" -> (input_rate_per_token, output_rate_per_token)
PROVIDER_PRICING: dict[str, tuple[float, float]] = {
    # Gemini
    "gemini:gemini-2.5-flash": (0.30e-6, 2.50e-6),
    "gemini:gemini-2.0-flash": (0.10e-6, 0.40e-6),
    "gemini:gemini-2.0-flash-lite": (0.075e-6, 0.30e-6),
    "gemini:gemini-3-flash-preview": (0.50e-6, 3.00e-6),
    # OpenAI
    "openai:gpt-4o-mini": (0.15e-6, 0.60e-6),
    "openai:gpt-4o": (2.50e-6, 10.00e-6),
    # Anthropic
    "anthropic:claude-sonnet-4-20250514": (3.00e-6, 15.00e-6),
    "anthropic:claude-haiku-3-5": (0.80e-6, 4.00e-6),
}


@dataclass
class CostRecord:
    """A single cost record for an LLM request."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float  # 6 decimal places
    user_id: str
    user_tier: str
    timestamp: datetime


class CostTracker:
    """Records per-request LLM costs with provider-specific pricing.

    Uses a configurable pricing table mapping "provider:model" keys to
    (input_rate, output_rate) tuples in USD per token.
    """

    def __init__(
        self,
        pricing_table: dict[str, tuple[float, float]],
        db: Prisma,
    ) -> None:
        self._pricing = pricing_table
        self._db = db

    def compute_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Compute cost in USD using the pricing table.

        Formula: (input_tokens × input_rate) + (output_tokens × output_rate)
        rounded to 6 decimal places.

        Returns 0.0 and logs a warning for unknown provider-model pairs.
        """
        key = f"{provider}:{model}"
        if key not in self._pricing:
            logger.warning("No pricing entry for %s, recording zero cost", key)
            return 0.0
        input_rate, output_rate = self._pricing[key]
        return round((input_tokens * input_rate) + (output_tokens * output_rate), 6)

    async def record(
        self,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        user_id: str,
        user_tier: str,
    ) -> CostRecord:
        """Record a cost entry to the database.

        If token counts are None (missing from provider response), they are
        recorded as zero and a warning is logged.
        """
        if input_tokens is None or output_tokens is None:
            logger.warning(
                "Missing token counts for %s:%s (input=%s, output=%s), recording as zero",
                provider,
                model,
                input_tokens,
                output_tokens,
            )
            input_tokens = input_tokens or 0
            output_tokens = output_tokens or 0

        cost_usd = self.compute_cost(provider, model, input_tokens, output_tokens)
        now = datetime.now(timezone.utc)

        await self._db.llmcostrecord.create(
            data={
                "userId": user_id,
                "userTier": user_tier,
                "provider": provider,
                "model": model,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "costUsd": Decimal(str(cost_usd)),
                "createdAt": now,
            }
        )

        return CostRecord(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            user_id=user_id,
            user_tier=user_tier,
            timestamp=now,
        )

    async def aggregate(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate costs by the given dimensions.

        Returns a dictionary with:
        - total_cost_usd: sum of all matching cost records
        - total_input_tokens: sum of input tokens
        - total_output_tokens: sum of output tokens
        - record_count: number of matching records
        """
        conditions: list[str] = []
        params: list[Any] = []
        param_idx = 1

        if provider is not None:
            conditions.append(f'"provider" = ${param_idx}')
            params.append(provider)
            param_idx += 1

        if model is not None:
            conditions.append(f'"model" = ${param_idx}')
            params.append(model)
            param_idx += 1

        if user_id is not None:
            conditions.append(f'"userId" = ${param_idx}')
            params.append(user_id)
            param_idx += 1

        if start is not None:
            conditions.append(f'"createdAt" >= ${param_idx}')
            params.append(start)
            param_idx += 1

        if end is not None:
            conditions.append(f'"createdAt" <= ${param_idx}')
            params.append(end)
            param_idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT
                COALESCE(SUM("costUsd"), 0) as total_cost_usd,
                COALESCE(SUM("inputTokens"), 0) as total_input_tokens,
                COALESCE(SUM("outputTokens"), 0) as total_output_tokens,
                COUNT(*) as record_count
            FROM "LlmCostRecord"
            {where_clause}
        """

        results = await self._db.query_raw(query, *params)

        if results and len(results) > 0:
            row = results[0]
            return {
                "total_cost_usd": float(row.get("total_cost_usd", 0)),
                "total_input_tokens": int(row.get("total_input_tokens", 0)),
                "total_output_tokens": int(row.get("total_output_tokens", 0)),
                "record_count": int(row.get("record_count", 0)),
            }

        return {
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "record_count": 0,
        }
