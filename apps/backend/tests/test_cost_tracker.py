"""Unit tests for cost_tracker module (no database). Run with: SKIP_DB_FIXTURE=1 pytest tests/test_cost_tracker.py"""

import os
from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure conftest autouse DB fixture does not require DATABASE_URL for this module.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.domains.intelligence.reasoning.llm.cost_tracker import (
    PROVIDER_PRICING,
    CostRecord,
    CostTracker,
)

# --- Fixtures ---


class FakeSession:
    """Records what was added, and answers `aggregate`'s single query.

    Replaces the Prisma-client double this file used to build. `record` and `aggregate` were rewritten
    onto SQLAlchemy during the port (Prisma was removed with the datastore migration), so the tests for
    those two methods assert against this seam rather than against `db.llmcostrecord.create` and
    `db.query_raw`. **The `compute_cost` and `PROVIDER_PRICING` tests below are unchanged** — they were
    always pure, and they are the part of this file that verifies the port rather than the rewrite.
    """

    def __init__(self, aggregate_row=None):
        self.added = []
        self.committed = False
        self.executed = []
        self._aggregate_row = aggregate_row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def execute(self, statement):
        self.executed.append(statement)
        result = MagicMock()
        result.one_or_none.return_value = self._aggregate_row
        return result


@pytest.fixture
def session():
    """A single fake session, shared by the factory below so a test can inspect it."""
    return FakeSession()


@pytest.fixture
def session_factory(session):
    return lambda: session


@pytest.fixture
def tracker(session_factory):
    """A CostTracker over the default pricing table and a fake session factory."""
    return CostTracker(pricing_table=PROVIDER_PRICING, session_factory=session_factory)


# --- compute_cost tests ---


class TestComputeCost:
    """Tests for CostTracker.compute_cost()."""

    def test_known_model_computes_correctly(self, tracker):
        """Cost for a known model uses the pricing formula."""
        # gemini:gemini-3.5-flash has rates (0.50e-6, 3.00e-6)
        cost = tracker.compute_cost("gemini", "gemini-3.5-flash", 1000, 500)
        expected = round((1000 * 0.50e-6) + (500 * 3.00e-6), 6)
        assert cost == expected

    def test_openai_model_computes_correctly(self, tracker):
        """Cost for OpenAI model uses correct rates."""
        # openai:gpt-4o-mini has rates (0.15e-6, 0.60e-6)
        cost = tracker.compute_cost("openai", "gpt-4o-mini", 2000, 1000)
        expected = round((2000 * 0.15e-6) + (1000 * 0.60e-6), 6)
        assert cost == expected

    def test_anthropic_model_computes_correctly(self, tracker):
        """Cost for Anthropic model uses correct rates."""
        # anthropic:claude-sonnet-4-20250514 has rates (3.00e-6, 15.00e-6)
        cost = tracker.compute_cost("anthropic", "claude-sonnet-4-20250514", 500, 200)
        expected = round((500 * 3.00e-6) + (200 * 15.00e-6), 6)
        assert cost == expected

    def test_zero_tokens_returns_zero(self, tracker):
        """Zero input and output tokens produce zero cost."""
        cost = tracker.compute_cost("gemini", "gemini-3.5-flash", 0, 0)
        assert cost == 0.0

    def test_unknown_model_returns_zero(self, tracker):
        """Unknown provider-model pair returns 0.0."""
        cost = tracker.compute_cost("unknown", "nonexistent-model", 1000, 1000)
        assert cost == 0.0

    def test_unknown_model_logs_warning(self, tracker):
        """Unknown provider-model pair logs a warning."""
        with patch(
            "src.domains.intelligence.reasoning.llm.cost_tracker.logger"
        ) as mock_logger:
            tracker.compute_cost("unknown", "nonexistent-model", 1000, 1000)
            mock_logger.warning.assert_called_once()

    def test_result_rounded_to_6_decimal_places(self, tracker):
        """Cost is rounded to exactly 6 decimal places."""
        cost = tracker.compute_cost("gemini", "gemini-3.5-flash", 1, 1)
        # With very small token counts, verify precision
        cost_str = f"{cost:.10f}"
        # The result should have at most 6 meaningful decimal places
        assert cost == round(cost, 6)

    def test_large_token_counts(self, tracker):
        """Large token counts compute correctly."""
        # 1 million tokens each
        cost = tracker.compute_cost("openai", "gpt-4o", 1_000_000, 1_000_000)
        expected = round((1_000_000 * 2.50e-6) + (1_000_000 * 10.00e-6), 6)
        assert cost == expected


# --- record tests ---


class TestRecord:
    """Tests for CostTracker.record()."""

    @pytest.mark.asyncio
    async def test_record_persists_to_database(self, tracker, session):
        """record() writes one LlmCostRecord row and commits it."""
        await tracker.record(
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=100,
            output_tokens=50,
            user_id="user-123",
            user_tier="FREE",
        )

        assert len(session.added) == 1
        row = session.added[0]
        assert row.user_id == "user-123"
        assert row.user_tier == "FREE"
        assert row.provider == "gemini"
        assert row.model == "gemini-3.5-flash"
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert session.committed, "a cost row was added but never committed"

    @pytest.mark.asyncio
    async def test_the_persisted_cost_is_an_exact_decimal(self, tracker, session):
        """`Decimal(str(cost))`, not `Decimal(cost)`.

        The column is Numeric(12, 6). Building a Decimal straight from a float carries the float's
        binary representation error into it, so a cost of 0.000015 can persist as
        0.0000149999999999999993 and every aggregate over it inherits the drift.
        """
        from decimal import Decimal

        await tracker.record(
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=10,
            output_tokens=0,
            user_id="user-1",
            user_tier="FREE",
        )
        stored = session.added[0].cost_usd
        assert isinstance(stored, Decimal)
        assert stored == Decimal(str(round(10 * 0.50e-6, 6)))

    @pytest.mark.asyncio
    async def test_record_returns_cost_record(self, tracker):
        """record() returns a CostRecord dataclass with correct fields."""
        result = await tracker.record(
            provider="openai",
            model="gpt-4o-mini",
            input_tokens=200,
            output_tokens=100,
            user_id="user-456",
            user_tier="PREMIUM_MONTHLY",
        )

        assert isinstance(result, CostRecord)
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"
        assert result.input_tokens == 200
        assert result.output_tokens == 100
        assert result.user_id == "user-456"
        assert result.user_tier == "PREMIUM_MONTHLY"
        assert isinstance(result.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_record_handles_none_input_tokens(self, tracker):
        """Missing input tokens are recorded as zero."""
        result = await tracker.record(
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=None,
            output_tokens=50,
            user_id="user-789",
            user_tier="FREE",
        )

        assert result.input_tokens == 0
        assert result.output_tokens == 50
        # Cost is computed with 0 input tokens but 50 output tokens
        expected_cost = round(50 * 3.00e-6, 6)
        assert result.cost_usd == expected_cost

    @pytest.mark.asyncio
    async def test_record_handles_none_output_tokens(self, tracker):
        """Missing output tokens are recorded as zero."""
        result = await tracker.record(
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=100,
            output_tokens=None,
            user_id="user-789",
            user_tier="FREE",
        )

        assert result.output_tokens == 0

    @pytest.mark.asyncio
    async def test_record_handles_both_tokens_none(self, tracker):
        """Both tokens None results in zero cost."""
        result = await tracker.record(
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=None,
            output_tokens=None,
            user_id="user-789",
            user_tier="FREE",
        )

        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_record_logs_warning_for_missing_tokens(self, tracker):
        """Missing token counts trigger a warning log."""
        with patch(
            "src.domains.intelligence.reasoning.llm.cost_tracker.logger"
        ) as mock_logger:
            await tracker.record(
                provider="gemini",
                model="gemini-3.5-flash",
                input_tokens=None,
                output_tokens=None,
                user_id="user-789",
                user_tier="FREE",
            )
            mock_logger.warning.assert_called()


# --- aggregate tests ---


class TestAggregate:
    """Tests for CostTracker.aggregate()."""

    @pytest.mark.asyncio
    async def test_aggregate_no_filters(self, session_factory, session):
        """With no filters, the totals come straight back from the query."""
        session._aggregate_row = (1.5, 10000, 5000, 3)
        tracker = CostTracker(
            pricing_table=PROVIDER_PRICING, session_factory=session_factory
        )

        result = await tracker.aggregate()

        assert result["total_cost_usd"] == 1.5
        assert result["total_input_tokens"] == 10000
        assert result["total_output_tokens"] == 5000
        assert result["record_count"] == 3

    @pytest.mark.asyncio
    async def test_aggregate_applies_no_where_clause_without_filters(
        self, tracker, session
    ):
        """An unfiltered aggregate must not accidentally constrain itself."""
        session._aggregate_row = (0, 0, 0, 0)
        await tracker.aggregate()
        assert "WHERE" not in str(session.executed[0])

    @pytest.mark.asyncio
    async def test_aggregate_with_provider_filter(self, tracker, session):
        """A provider filter reaches the query as a bound condition on the provider column."""
        session._aggregate_row = (0.5, 3000, 1000, 1)
        await tracker.aggregate(provider="openai")

        rendered = str(
            session.executed[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "provider = 'openai'" in rendered
        # And nothing else was constrained, so an unrelated provider's spend is not excluded twice.
        assert "model" not in rendered.split("WHERE", 1)[1]

    @pytest.mark.asyncio
    async def test_aggregate_with_time_range(self, tracker, session):
        """Both ends of a time range are applied, and the totals are returned."""
        session._aggregate_row = (0.25, 1000, 500, 2)

        result = await tracker.aggregate(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 31, tzinfo=UTC),
        )

        assert result["total_cost_usd"] == 0.25
        assert result["record_count"] == 2
        rendered = str(session.executed[0])
        assert (
            rendered.count('"createdAt"') == 2
        ), "a one-sided range would silently over-count"

    @pytest.mark.asyncio
    async def test_aggregate_filters_are_independent(self, tracker, session):
        """Every filter is applied, not just the last one.

        The pre-port implementation numbered SQL placeholders by hand; a filter added without
        incrementing the counter would have bound the wrong value to the wrong column. Typed
        expressions cannot mis-number themselves, and this pins that they are all present.
        """
        session._aggregate_row = (0, 0, 0, 0)
        await tracker.aggregate(provider="openai", model="gpt-4o", user_id="user-9")

        rendered = str(
            session.executed[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "'openai'" in rendered
        assert "'gpt-4o'" in rendered
        assert "'user-9'" in rendered

    @pytest.mark.asyncio
    async def test_aggregate_empty_results(self, tracker, session):
        """No matching rows reads as zero, not as an error."""
        session._aggregate_row = (0, 0, 0, 0)

        result = await tracker.aggregate(provider="nonexistent")

        assert result["total_cost_usd"] == 0.0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["record_count"] == 0

    @pytest.mark.asyncio
    async def test_aggregate_handles_no_row_at_all(self, tracker, session):
        """`one_or_none()` can return None. That must be zeros, not a TypeError on unpacking."""
        session._aggregate_row = None

        result = await tracker.aggregate()

        assert result == {
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "record_count": 0,
        }


# --- PROVIDER_PRICING table tests ---


class TestProviderPricing:
    """Tests for the PROVIDER_PRICING configuration."""

    def test_all_expected_models_present(self):
        """All expected provider-model pairs are in the pricing table."""
        expected_keys = [
            "gemini:gemini-3.5-flash",
            "gemini:gemini-3.1-flash-lite",
            "openai:gpt-4o-mini",
            "openai:gpt-4o",
            "anthropic:claude-sonnet-4-20250514",
            "anthropic:claude-haiku-3-5",
        ]
        for key in expected_keys:
            assert key in PROVIDER_PRICING, f"Missing pricing entry: {key}"

    def test_all_rates_are_positive(self):
        """All pricing rates are positive numbers."""
        for key, (input_rate, output_rate) in PROVIDER_PRICING.items():
            assert input_rate > 0, f"Non-positive input rate for {key}"
            assert output_rate > 0, f"Non-positive output rate for {key}"

    def test_rates_are_per_token_not_per_million(self):
        """Rates are per-token (very small numbers), not per-million."""
        for key, (input_rate, output_rate) in PROVIDER_PRICING.items():
            # Per-token rates should be less than 0.001 (1/1000 of a cent)
            assert (
                input_rate < 0.001
            ), f"Input rate for {key} seems too large for per-token"
            assert (
                output_rate < 0.001
            ), f"Output rate for {key} seems too large for per-token"


@pytest.mark.asyncio
async def test_ask_cost_record_is_attributed_to_the_durable_attempt(tracker, session):
    await tracker.record(
        provider="gemini",
        model="gemini-3.5-flash",
        input_tokens=120,
        output_tokens=40,
        user_id="user-ask",
        user_tier="FREE",
        attempt_id="attempt-ask",
    )

    row = session.added[0]
    assert row.user_id == "user-ask"
    assert row.attempt_id == "attempt-ask"
