"""Integration tests for end-to-end LLM routing.

Tests the full request flow through the router with mocked provider adapters,
verifying fallback behavior, cost recording, and tier-based access control.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_end_to_end_routing.py -v
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import ChatCapability, EmbeddingCapability
from src.services.llm.circuit_breaker import CircuitBreaker
from src.services.llm.errors import GeminiError, LLMProviderError, OpenAIError
from src.services.llm.feature_flags import FeatureFlagService
from src.services.llm.router import LLMRouter
from src.services.llm_registry import LlmTask


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class MockChatAdapter(BaseProviderAdapter):
    """Mock chat adapter for testing."""

    def __init__(self, provider: str, model: str, response: str = "Hello!"):
        self._provider = provider
        self._model = model
        self._response = response
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model

    def supported_capabilities(self) -> set[type]:
        return {ChatCapability}

    async def get_chat_response_with_tools(self, **kwargs):
        self.call_count += 1
        return (
            self._response,
            {"input_tokens": 100, "output_tokens": 50},
            [],
            [],
        )


class MockFailingAdapter(BaseProviderAdapter):
    """Mock adapter that always fails with a retriable error."""

    def __init__(self, provider: str, model: str, category: str = "server_error"):
        self._provider = provider
        self._model = model
        self._category = category
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model

    def supported_capabilities(self) -> set[type]:
        return {ChatCapability}

    async def get_chat_response_with_tools(self, **kwargs):
        self.call_count += 1
        raise LLMProviderError(
            provider=self._provider,
            model=self._model,
            status_code=500,
            category=self._category,
            message=f"{self._provider}:{self._model} is down",
            retriable=True,
        )


class MockNonRetriableAdapter(BaseProviderAdapter):
    """Mock adapter that fails with a non-retriable error."""

    def __init__(self, provider: str, model: str):
        self._provider = provider
        self._model = model
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model

    def supported_capabilities(self) -> set[type]:
        return {ChatCapability}

    async def get_chat_response_with_tools(self, **kwargs):
        self.call_count += 1
        raise LLMProviderError(
            provider=self._provider,
            model=self._model,
            status_code=401,
            category="auth",
            message="Invalid API key",
            retriable=False,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def feature_flags():
    """Feature flags with all providers enabled."""
    return FeatureFlagService(
        enabled_providers="gemini,openai,anthropic",
        tier_allowlists={
            "free": "gemini:gemini-2.5-flash,gemini:gemini-2.0-flash-lite",
            "plus": "gemini:gemini-2.5-flash,openai:gpt-4o-mini",
            "circle": "gemini:gemini-2.5-flash,openai:gpt-4o,anthropic:claude-sonnet-4-20250514",
        },
    )


@pytest.fixture
def circuit_breaker():
    """Fresh circuit breaker."""
    return CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)


@pytest.fixture
def mock_cost_tracker():
    """Mock cost tracker that records calls."""
    tracker = MagicMock()
    tracker.record = AsyncMock(return_value=None)
    return tracker


@pytest.fixture
def gemini_adapter():
    return MockChatAdapter("gemini", "gemini-2.5-flash", "Gemini response")


@pytest.fixture
def openai_adapter():
    return MockChatAdapter("openai", "gpt-4o-mini", "OpenAI response")


@pytest.fixture
def anthropic_adapter():
    return MockChatAdapter("anthropic", "claude-sonnet-4-20250514", "Anthropic response")


# ---------------------------------------------------------------------------
# Tests: Successful routing
# ---------------------------------------------------------------------------


class TestSuccessfulRouting:
    """Tests for successful request routing."""

    @pytest.mark.asyncio
    async def test_routes_to_first_candidate(
        self, feature_flags, circuit_breaker, mock_cost_tracker, gemini_adapter
    ):
        """Router selects the first valid candidate from the fallback chain."""
        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={"gemini:gemini-2.5-flash": gemini_adapter},
            fallback_chains={LlmTask.CHAT_DEFAULT: [("gemini", "gemini-2.5-flash")]},
            timeout_seconds=10.0,
        )

        text, usage, actions, queries = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="free",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        assert text == "Gemini response"
        assert usage["input_tokens"] == 100
        assert gemini_adapter.call_count == 1

    @pytest.mark.asyncio
    async def test_records_cost_on_success(
        self, feature_flags, circuit_breaker, mock_cost_tracker, gemini_adapter
    ):
        """Router records cost after successful request."""
        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={"gemini:gemini-2.5-flash": gemini_adapter},
            fallback_chains={LlmTask.CHAT_DEFAULT: [("gemini", "gemini-2.5-flash")]},
            timeout_seconds=10.0,
        )

        await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="free",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        mock_cost_tracker.record.assert_called_once_with(
            provider="gemini",
            model="gemini-2.5-flash",
            input_tokens=100,
            output_tokens=50,
            user_id="user-1",
            user_tier="free",
        )

    @pytest.mark.asyncio
    async def test_respects_model_preference(
        self, feature_flags, circuit_breaker, mock_cost_tracker, gemini_adapter, openai_adapter
    ):
        """Router uses model preference when the preferred pair is valid."""
        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": gemini_adapter,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        text, _, _, _ = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="plus",
            model_preference=("openai", "gpt-4o-mini"),
            history=[],
            user_message="Hello",
        )

        assert text == "OpenAI response"
        assert openai_adapter.call_count == 1
        assert gemini_adapter.call_count == 0


# ---------------------------------------------------------------------------
# Tests: Fallback behavior
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    """Tests for fallback when primary provider fails."""

    @pytest.mark.asyncio
    async def test_falls_back_on_retriable_error(
        self, feature_flags, circuit_breaker, mock_cost_tracker, openai_adapter
    ):
        """Router falls back to next candidate on retriable failure."""
        failing_gemini = MockFailingAdapter("gemini", "gemini-2.5-flash")

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": failing_gemini,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        text, _, _, _ = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="plus",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        assert text == "OpenAI response"
        assert failing_gemini.call_count == 1
        assert openai_adapter.call_count == 1

    @pytest.mark.asyncio
    async def test_non_retriable_error_propagates_immediately(
        self, feature_flags, circuit_breaker, mock_cost_tracker, openai_adapter
    ):
        """Non-retriable errors propagate without trying fallback."""
        auth_failure = MockNonRetriableAdapter("gemini", "gemini-2.5-flash")

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": auth_failure,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            await router.route_request(
                task=LlmTask.CHAT_DEFAULT,
                user_id="user-1",
                user_tier="plus",
                model_preference=None,
                history=[],
                user_message="Hello",
            )

        assert exc_info.value.category == "auth"
        assert auth_failure.call_count == 1
        assert openai_adapter.call_count == 0  # Never reached

    @pytest.mark.asyncio
    async def test_all_providers_exhausted_raises_overloaded(
        self, feature_flags, circuit_breaker, mock_cost_tracker
    ):
        """When all candidates fail, raises overloaded error."""
        failing_gemini = MockFailingAdapter("gemini", "gemini-2.5-flash")
        failing_openai = MockFailingAdapter("openai", "gpt-4o-mini")

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": failing_gemini,
                "openai:gpt-4o-mini": failing_openai,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            await router.route_request(
                task=LlmTask.CHAT_DEFAULT,
                user_id="user-1",
                user_tier="plus",
                model_preference=None,
                history=[],
                user_message="Hello",
            )

        assert exc_info.value.category == "overloaded"
        assert "exhausted" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure_on_retriable(
        self, feature_flags, circuit_breaker, mock_cost_tracker, openai_adapter
    ):
        """Circuit breaker records failure when adapter returns retriable error."""
        failing_gemini = MockFailingAdapter("gemini", "gemini-2.5-flash")

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": failing_gemini,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="plus",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        # Gemini should have 1 failure recorded
        from src.services.llm.circuit_breaker import CircuitState

        # Not enough failures to trip (threshold=3), but failure is recorded
        assert (
            circuit_breaker._failures_in_window(circuit_breaker._key("gemini", "gemini-2.5-flash"))
            == 1
        )


# ---------------------------------------------------------------------------
# Tests: Tier-based access control
# ---------------------------------------------------------------------------


class TestTierAccessControl:
    """Tests for tier-based model filtering."""

    @pytest.mark.asyncio
    async def test_free_tier_cannot_use_openai(
        self, feature_flags, circuit_breaker, mock_cost_tracker, openai_adapter
    ):
        """FREE tier user cannot access OpenAI models."""
        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={"openai:gpt-4o-mini": openai_adapter},
            fallback_chains={LlmTask.CHAT_DEFAULT: [("openai", "gpt-4o-mini")]},
            timeout_seconds=10.0,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            await router.route_request(
                task=LlmTask.CHAT_DEFAULT,
                user_id="user-1",
                user_tier="free",
                model_preference=None,
                history=[],
                user_message="Hello",
            )

        assert exc_info.value.category == "overloaded"
        assert openai_adapter.call_count == 0

    @pytest.mark.asyncio
    async def test_preference_ignored_when_tier_disallows(
        self, feature_flags, circuit_breaker, mock_cost_tracker, gemini_adapter, openai_adapter
    ):
        """Model preference is ignored if the model isn't allowed for the tier."""
        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": gemini_adapter,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        # FREE tier user prefers OpenAI, but it's not allowed
        text, _, _, _ = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="free",
            model_preference=("openai", "gpt-4o-mini"),
            history=[],
            user_message="Hello",
        )

        # Should fall back to Gemini (the only allowed provider for free tier)
        assert text == "Gemini response"
        assert gemini_adapter.call_count == 1
        assert openai_adapter.call_count == 0


# ---------------------------------------------------------------------------
# Tests: Circuit breaker integration
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker filtering in the router."""

    @pytest.mark.asyncio
    async def test_open_circuit_skips_provider(
        self, feature_flags, mock_cost_tracker, gemini_adapter, openai_adapter
    ):
        """Provider with open circuit breaker is skipped."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=300.0)

        # Trip the circuit breaker for Gemini
        cb.record_failure("gemini", "gemini-2.5-flash")
        cb.record_failure("gemini", "gemini-2.5-flash")
        # Now Gemini circuit is OPEN

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=cb,
            cost_tracker=mock_cost_tracker,
            adapter_registry={
                "gemini:gemini-2.5-flash": gemini_adapter,
                "openai:gpt-4o-mini": openai_adapter,
            },
            fallback_chains={
                LlmTask.CHAT_DEFAULT: [
                    ("gemini", "gemini-2.5-flash"),
                    ("openai", "gpt-4o-mini"),
                ]
            },
            timeout_seconds=10.0,
        )

        text, _, _, _ = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="plus",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        # Gemini skipped due to open circuit, OpenAI used instead
        assert text == "OpenAI response"
        assert gemini_adapter.call_count == 0
        assert openai_adapter.call_count == 1


# ---------------------------------------------------------------------------
# Tests: Cost tracking failure resilience
# ---------------------------------------------------------------------------


class TestCostTrackingResilience:
    """Tests that cost tracking failures don't break requests."""

    @pytest.mark.asyncio
    async def test_cost_tracker_failure_does_not_break_request(
        self, feature_flags, circuit_breaker, gemini_adapter
    ):
        """Request succeeds even if cost tracking fails."""
        failing_tracker = MagicMock()
        failing_tracker.record = AsyncMock(side_effect=Exception("DB connection lost"))

        router = LLMRouter(
            feature_flags=feature_flags,
            circuit_breaker=circuit_breaker,
            cost_tracker=failing_tracker,
            adapter_registry={"gemini:gemini-2.5-flash": gemini_adapter},
            fallback_chains={LlmTask.CHAT_DEFAULT: [("gemini", "gemini-2.5-flash")]},
            timeout_seconds=10.0,
        )

        # Should not raise despite cost tracker failure
        text, _, _, _ = await router.route_request(
            task=LlmTask.CHAT_DEFAULT,
            user_id="user-1",
            user_tier="free",
            model_preference=None,
            history=[],
            user_message="Hello",
        )

        assert text == "Gemini response"
        failing_tracker.record.assert_called_once()
