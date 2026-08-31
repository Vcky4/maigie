"""
Cost tracker for multi-provider LLM usage.

Records per-request costs with provider-specific pricing tables and persists
cost records to the database for aggregation and spend monitoring.

**Ported from `git show "4953972^:apps/backend/src/services/llm/cost_tracker.py"`, with the
persistence layer rewritten.** The original spoke the Prisma client API — `db.llmcostrecord.create`
and `db.query_raw` with `$1` placeholders — and Prisma was removed when the backend moved to
SQLAlchemy. The pricing table and `compute_cost` are unchanged and their tests pass untouched; `record`
and `aggregate` are new implementations over the existing `LlmCostRecord` model.

Two things changed beyond a mechanical translation:

- **The constructor takes a session factory, not a client.** `session_factory=None` resolves through
  `get_session_factory()` at call time rather than at construction, so a `CostTracker` can be built
  before the database is connected — which is what the router does.
- **`aggregate` builds its query through SQLAlchemy rather than string interpolation.** The original
  assembled SQL by concatenating `f'"provider" = ${param_idx}'` and counting placeholders by hand.
  That worked, but a filter added later that forgot to increment `param_idx` would have silently bound
  the wrong value to the wrong column, and cost aggregation is exactly where a quiet wrong answer is
  worst. The typed expression cannot mis-number its own parameters.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from src.domains.intelligence.db_models import LlmCostRecord
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

# USD per token (not per million) for direct multiplication.
# Format: "provider:model" -> (input_rate_per_token, output_rate_per_token)
PROVIDER_PRICING: dict[str, tuple[float, float]] = {
    # Gemini (current)
    "gemini:gemini-3.5-flash": (0.50e-6, 3.00e-6),
    "gemini:gemini-3.1-flash-lite": (0.25e-6, 1.50e-6),
    # Gemini (legacy — kept for historical cost records)
    "gemini:gemini-2.5-flash": (0.30e-6, 2.50e-6),
    "gemini:gemini-2.5-flash-lite": (0.10e-6, 0.40e-6),
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
        session_factory: Any = None,
    ) -> None:
        self._pricing = pricing_table
        self._session_factory = session_factory

    def _sessions(self) -> Any:
        """Resolve the session factory lazily.

        Deferred to call time so a tracker can be constructed before the database is connected. The
        router builds one at import.
        """
        return self._session_factory or get_session_factory()

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
        attempt_id: str | None = None,
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
        now = datetime.now(UTC)

        # `Decimal(str(cost_usd))` rather than `Decimal(cost_usd)`: the column is Numeric(12, 6) and
        # constructing a Decimal from a float carries the float's binary representation error into it,
        # so 0.000015 can persist as 0.0000149999999999999993.
        factory = self._sessions()
        async with factory() as session:
            session.add(
                LlmCostRecord(
                    user_id=user_id,
                    user_tier=user_tier,
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=Decimal(str(cost_usd)),
                    attempt_id=attempt_id,
                    created_at=now,
                )
            )
            await session.commit()

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
        conditions = []
        if provider is not None:
            conditions.append(LlmCostRecord.provider == provider)
        if model is not None:
            conditions.append(LlmCostRecord.model == model)
        if user_id is not None:
            conditions.append(LlmCostRecord.user_id == user_id)
        if start is not None:
            conditions.append(LlmCostRecord.created_at >= start)
        if end is not None:
            conditions.append(LlmCostRecord.created_at <= end)

        stmt = select(
            func.coalesce(func.sum(LlmCostRecord.cost_usd), 0),
            func.coalesce(func.sum(LlmCostRecord.input_tokens), 0),
            func.coalesce(func.sum(LlmCostRecord.output_tokens), 0),
            func.count(),
        ).select_from(LlmCostRecord)
        if conditions:
            stmt = stmt.where(*conditions)

        factory = self._sessions()
        async with factory() as session:
            row = (await session.execute(stmt)).one_or_none()

        if row is None:
            return {
                "total_cost_usd": 0.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "record_count": 0,
            }

        total_cost, total_input, total_output, count = row
        return {
            "total_cost_usd": float(total_cost or 0),
            "total_input_tokens": int(total_input or 0),
            "total_output_tokens": int(total_output or 0),
            "record_count": int(count or 0),
        }
