"""Unit tests for the CircuitBreaker implementation.

Tests cover the full state machine: CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN,
cooldown timing, per-provider-model isolation, and reset behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.llm.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerClosedState:
    """Tests for normal (CLOSED) operation."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED

    def test_should_allow_in_closed_state(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        assert cb.should_allow("openai", "gpt-4o") is True

    def test_failures_below_threshold_stay_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED
        assert cb.should_allow("openai", "gpt-4o") is True

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        cb.record_success("openai", "gpt-4o")
        # After success, we need 3 more failures to open
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED


class TestCircuitBreakerOpenState:
    """Tests for OPEN state behavior."""

    def test_transitions_to_open_at_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN

    def test_should_allow_returns_false_when_open(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")
        assert cb.should_allow("openai", "gpt-4o") is False

    def test_open_state_rejects_multiple_requests(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")
        assert cb.should_allow("openai", "gpt-4o") is False
        assert cb.should_allow("openai", "gpt-4o") is False


class TestCircuitBreakerHalfOpenState:
    """Tests for HALF_OPEN state and recovery transitions."""

    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        # Simulate cooldown elapsed
        with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
            cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
            state = cb.get_state("openai", "gpt-4o")
            assert state == CircuitState.HALF_OPEN

    def test_should_allow_permits_one_probe_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        # Simulate cooldown elapsed
        with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
            cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
            assert cb.should_allow("openai", "gpt-4o") is True
            # Second request should be rejected (only one probe allowed)
            assert cb.should_allow("openai", "gpt-4o") is False

    def test_success_in_half_open_transitions_to_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        # Simulate cooldown elapsed
        with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
            cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
            cb.should_allow("openai", "gpt-4o")  # Trigger transition to HALF_OPEN
            cb.record_success("openai", "gpt-4o")
            assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED

    def test_failure_in_half_open_transitions_back_to_open(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        # Simulate cooldown elapsed → transition to HALF_OPEN, then probe fails → back to OPEN
        with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
            cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
            cb.should_allow("openai", "gpt-4o")  # Trigger transition to HALF_OPEN
            cb.record_failure("openai", "gpt-4o")

        # Should be back in OPEN state — check with a time that's within the new cooldown
        # record_failure set _last_failure_time to 1010.0 (the mocked time), so we check
        # at 1015.0 which is only 5s later (less than 10s cooldown)
        with patch("src.services.llm.circuit_breaker.time.time", return_value=1015.0):
            assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN


class TestCircuitBreakerIsolation:
    """Tests for per-provider-model isolation."""

    def test_different_providers_are_independent(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN
        assert cb.get_state("anthropic", "claude-sonnet") == CircuitState.CLOSED
        assert cb.should_allow("anthropic", "claude-sonnet") is True

    def test_different_models_same_provider_are_independent(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN
        assert cb.get_state("openai", "gpt-4o-mini") == CircuitState.CLOSED
        assert cb.should_allow("openai", "gpt-4o-mini") is True


class TestCircuitBreakerReset:
    """Tests for the reset method."""

    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN

        cb.reset("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED
        assert cb.should_allow("openai", "gpt-4o") is True

    def test_reset_clears_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        cb.reset("openai", "gpt-4o")
        # After reset, need full threshold failures to open again
        cb.record_failure("openai", "gpt-4o")
        cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED


class TestCircuitBreakerLogging:
    """Tests for WARNING-level logging on state transitions."""

    def test_logs_warning_on_open_transition(self, caplog):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        with caplog.at_level("WARNING", logger="src.services.llm.circuit_breaker"):
            for _ in range(3):
                cb.record_failure("openai", "gpt-4o")
        assert "OPEN" in caplog.text or "open" in caplog.text

    def test_logs_warning_on_half_open_transition(self, caplog):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        with caplog.at_level("WARNING", logger="src.services.llm.circuit_breaker"):
            with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
                cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
                cb.get_state("openai", "gpt-4o")
        assert "half_open" in caplog.text

    def test_logs_warning_on_closed_transition(self, caplog):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        for _ in range(3):
            cb.record_failure("openai", "gpt-4o")

        with patch("src.services.llm.circuit_breaker.time.time", return_value=1000.0 + 10.0):
            cb._last_failure_time[cb._key("openai", "gpt-4o")] = 1000.0
            cb.should_allow("openai", "gpt-4o")  # Transition to HALF_OPEN

        with caplog.at_level("WARNING", logger="src.services.llm.circuit_breaker"):
            cb.record_success("openai", "gpt-4o")
        assert "closed" in caplog.text


class TestCircuitBreakerEdgeCases:
    """Edge case tests."""

    def test_threshold_of_one(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=5.0)
        cb.record_failure("gemini", "flash")
        assert cb.get_state("gemini", "flash") == CircuitState.OPEN

    def test_default_threshold_is_five(self):
        cb = CircuitBreaker()
        for _ in range(4):
            cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED
        cb.record_failure("openai", "gpt-4o")
        assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN

    def test_record_success_on_unknown_key_is_safe(self):
        cb = CircuitBreaker()
        # Should not raise
        cb.record_success("unknown", "model")
        assert cb.get_state("unknown", "model") == CircuitState.CLOSED

    def test_reset_on_unknown_key_is_safe(self):
        cb = CircuitBreaker()
        # Should not raise
        cb.reset("unknown", "model")
        assert cb.get_state("unknown", "model") == CircuitState.CLOSED


class TestCircuitBreakerRollingWindow:
    """Tests for rolling window behavior (Requirement 7.7)."""

    def test_failures_outside_window_do_not_count(self):
        """Failures older than the rolling window are evicted and don't trigger OPEN."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0, rolling_window_seconds=5.0)
        # Record 2 failures at time 100.0
        with patch("src.services.llm.circuit_breaker.time.time", return_value=100.0):
            cb.record_failure("openai", "gpt-4o")
            cb.record_failure("openai", "gpt-4o")

        # Record 1 failure at time 106.0 (first 2 are now outside the 5s window)
        with patch("src.services.llm.circuit_breaker.time.time", return_value=106.0):
            cb.record_failure("openai", "gpt-4o")
            # Only 1 failure in window — should still be CLOSED
            assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED

    def test_failures_within_window_trigger_open(self):
        """Failures within the rolling window count toward the threshold."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0, rolling_window_seconds=10.0)
        # Record 3 failures within the window
        with patch("src.services.llm.circuit_breaker.time.time", return_value=100.0):
            cb.record_failure("openai", "gpt-4o")
        with patch("src.services.llm.circuit_breaker.time.time", return_value=105.0):
            cb.record_failure("openai", "gpt-4o")
        with patch("src.services.llm.circuit_breaker.time.time", return_value=109.0):
            cb.record_failure("openai", "gpt-4o")
            # All 3 within 10s window — should be OPEN
            assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN

    def test_mixed_window_boundary(self):
        """Only failures within the window count; older ones are evicted."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0, rolling_window_seconds=5.0)
        # Record failures at different times
        with patch("src.services.llm.circuit_breaker.time.time", return_value=100.0):
            cb.record_failure("openai", "gpt-4o")  # Will expire after 105.0
        with patch("src.services.llm.circuit_breaker.time.time", return_value=101.0):
            cb.record_failure("openai", "gpt-4o")  # Will expire after 106.0
        # At time 106.0: cutoff = 101.0, so failure at 100.0 (< 101.0) is evicted,
        # failure at 101.0 (>= 101.0) stays. Plus new one at 106.0 = 2 in window.
        with patch("src.services.llm.circuit_breaker.time.time", return_value=106.0):
            cb.record_failure("openai", "gpt-4o")
            # 2 failures in window (101.0 and 106.0) — not enough for threshold of 3
            assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED

        # At time 107.0: cutoff = 102.0, so failure at 101.0 (< 102.0) is evicted.
        # Remaining: 106.0, plus new one at 107.0 = 2 in window — still not enough
        with patch("src.services.llm.circuit_breaker.time.time", return_value=107.0):
            cb.record_failure("openai", "gpt-4o")
            assert cb.get_state("openai", "gpt-4o") == CircuitState.CLOSED

        # At time 108.0: cutoff = 103.0. Remaining: 106.0, 107.0, plus new 108.0 = 3 → OPEN
        with patch("src.services.llm.circuit_breaker.time.time", return_value=108.0):
            cb.record_failure("openai", "gpt-4o")
            assert cb.get_state("openai", "gpt-4o") == CircuitState.OPEN

    def test_default_rolling_window_is_60_seconds(self):
        """Default rolling window is 60 seconds per Requirement 7.7."""
        cb = CircuitBreaker()
        assert cb._rolling_window_seconds == 60.0

    def test_default_cooldown_is_30_seconds(self):
        """Default cooldown is 30 seconds per Requirement 7.8."""
        cb = CircuitBreaker()
        assert cb._cooldown_seconds == 30.0


class TestCircuitBreakerFactory:
    """Tests for the create_circuit_breaker factory function."""

    def test_factory_uses_config_settings(self):
        """create_circuit_breaker reads from application settings."""
        with patch("src.config.get_settings") as mock_get_settings:
            mock_settings = mock_get_settings.return_value
            mock_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 10
            mock_settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS = 45.0
            mock_settings.CIRCUIT_BREAKER_ROLLING_WINDOW_SECONDS = 120.0

            from src.services.llm.circuit_breaker import create_circuit_breaker

            cb = create_circuit_breaker()
            assert cb._failure_threshold == 10
            assert cb._cooldown_seconds == 45.0
            assert cb._rolling_window_seconds == 120.0
