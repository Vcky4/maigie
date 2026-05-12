"""Unit tests for cost_tracker module (no database). Run with: SKIP_DB_FIXTURE=1 pytest tests/test_cost_tracker.py"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure conftest autouse DB fixture does not require DATABASE_URL for this module.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.llm.cost_tracker import PROVIDER_PRICING, CostRecord, CostTracker


# --- Fixtures ---


@pytest.fixture
def mock_db():
    """Create a mock Prisma client."""
    db = MagicMock()
    db.llmcostrecord = MagicMock()
    db.llmcostrecord.create = AsyncMock(return_value=None)
    db.query_raw = AsyncMock(return_value=[])
    return db


@pytest.fixture
def tracker(mock_db):
    """Create a CostTracker with the default pricing table and mock DB."""
    return CostTracker(pricing_table=PROVIDER_PRICING, db=mock_db)


# --- compute_cost tests ---


class TestComputeCost:
    """Tests for CostTracker.compute_cost()."""

    def test_known_model_computes_correctly(self, tracker):
        """Cost for a known model uses the pricing formula."""
        # gemini:gemini-2.5-flash has rates (0.30e-6, 2.50e-6)
        cost = tracker.compute_cost("gemini", "gemini-2.5-flash", 1000, 500)
        expected = round((1000 * 0.30e-6) + (500 * 2.50e-6), 6)
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
        cost = tracker.compute_cost("gemini", "gemini-2.5-flash", 0, 0)
        assert cost == 0.0

    def test_unknown_model_returns_zero(self, tracker):
        """Unknown provider-model pair returns 0.0."""
        cost = tracker.compute_cost("unknown", "nonexistent-model", 1000, 1000)
        assert cost == 0.0

    def test_unknown_model_logs_warning(self, tracker):
        """Unknown provider-model pair logs a warning."""
        with patch("src.services.llm.cost_tracker.logger") as mock_logger:
            tracker.compute_cost("unknown", "nonexistent-model", 1000, 1000)
            mock_logger.warning.assert_called_once()

    def test_result_rounded_to_6_decimal_places(self, tracker):
        """Cost is rounded to exactly 6 decimal places."""
        cost = tracker.compute_cost("gemini", "gemini-2.5-flash", 1, 1)
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
    async def test_record_persists_to_database(self, tracker, mock_db):
        """record() calls db.llmcostrecord.create with correct data."""
        result = await tracker.record(
            provider="gemini",
            model="gemini-2.5-flash",
            input_tokens=100,
            output_tokens=50,
            user_id="user-123",
            user_tier="FREE",
        )

        mock_db.llmcostrecord.create.assert_called_once()
        call_data = mock_db.llmcostrecord.create.call_args[1]["data"]
        assert call_data["userId"] == "user-123"
        assert call_data["userTier"] == "FREE"
        assert call_data["provider"] == "gemini"
        assert call_data["model"] == "gemini-2.5-flash"
        assert call_data["inputTokens"] == 100
        assert call_data["outputTokens"] == 50

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
            model="gemini-2.5-flash",
            input_tokens=None,
            output_tokens=50,
            user_id="user-789",
            user_tier="FREE",
        )

        assert result.input_tokens == 0
        assert result.output_tokens == 50
        # Cost is computed with 0 input tokens but 50 output tokens
        expected_cost = round(50 * 2.50e-6, 6)
        assert result.cost_usd == expected_cost

    @pytest.mark.asyncio
    async def test_record_handles_none_output_tokens(self, tracker):
        """Missing output tokens are recorded as zero."""
        result = await tracker.record(
            provider="gemini",
            model="gemini-2.5-flash",
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
            model="gemini-2.5-flash",
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
        with patch("src.services.llm.cost_tracker.logger") as mock_logger:
            await tracker.record(
                provider="gemini",
                model="gemini-2.5-flash",
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
    async def test_aggregate_no_filters(self, tracker, mock_db):
        """aggregate() with no filters queries all records."""
        mock_db.query_raw.return_value = [
            {
                "total_cost_usd": 1.5,
                "total_input_tokens": 10000,
                "total_output_tokens": 5000,
                "record_count": 3,
            }
        ]

        result = await tracker.aggregate()

        assert result["total_cost_usd"] == 1.5
        assert result["total_input_tokens"] == 10000
        assert result["total_output_tokens"] == 5000
        assert result["record_count"] == 3

    @pytest.mark.asyncio
    async def test_aggregate_with_provider_filter(self, tracker, mock_db):
        """aggregate() with provider filter includes it in the query."""
        mock_db.query_raw.return_value = [
            {
                "total_cost_usd": 0.5,
                "total_input_tokens": 3000,
                "total_output_tokens": 1000,
                "record_count": 1,
            }
        ]

        result = await tracker.aggregate(provider="openai")

        # Verify the query was called with the provider parameter
        call_args = mock_db.query_raw.call_args
        assert "openai" in call_args[0]

    @pytest.mark.asyncio
    async def test_aggregate_with_time_range(self, tracker, mock_db):
        """aggregate() with start and end filters by time."""
        mock_db.query_raw.return_value = [
            {
                "total_cost_usd": 0.25,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "record_count": 2,
            }
        ]

        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 31, tzinfo=timezone.utc)

        result = await tracker.aggregate(start=start, end=end)

        assert result["total_cost_usd"] == 0.25
        assert result["record_count"] == 2

    @pytest.mark.asyncio
    async def test_aggregate_empty_results(self, tracker, mock_db):
        """aggregate() returns zeros when no records match."""
        mock_db.query_raw.return_value = []

        result = await tracker.aggregate(provider="nonexistent")

        assert result["total_cost_usd"] == 0.0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["record_count"] == 0


# --- PROVIDER_PRICING table tests ---


class TestProviderPricing:
    """Tests for the PROVIDER_PRICING configuration."""

    def test_all_expected_models_present(self):
        """All expected provider-model pairs are in the pricing table."""
        expected_keys = [
            "gemini:gemini-2.5-flash",
            "gemini:gemini-2.0-flash",
            "gemini:gemini-2.0-flash-lite",
            "gemini:gemini-3-flash-preview",
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
            assert input_rate < 0.001, f"Input rate for {key} seems too large for per-token"
            assert output_rate < 0.001, f"Output rate for {key} seems too large for per-token"
