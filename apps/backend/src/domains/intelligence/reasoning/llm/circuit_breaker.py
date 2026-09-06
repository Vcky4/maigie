"""Circuit breaker for per-provider-model resilience.

Implements the standard three-state circuit breaker pattern (CLOSED → OPEN →
HALF_OPEN) to prevent cascading failures when an LLM provider becomes
unhealthy. Each provider-model pair is tracked independently.

State machine:
    CLOSED  – Normal operation. Failures within the rolling window are counted.
              When failures >= threshold within the window → transition to OPEN.
    OPEN    – All requests rejected (should_allow returns False).
              After cooldown_seconds elapse → transition to HALF_OPEN.
    HALF_OPEN – One probe request is allowed.
              If it succeeds → CLOSED (reset failure count).
              If it fails → OPEN (restart cooldown).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Possible states for a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider-model circuit breaker with configurable thresholds.

    Args:
        failure_threshold: Number of failures within the rolling window before
            opening the circuit (default: 5).
        cooldown_seconds: Seconds to wait in OPEN state before transitioning
            to HALF_OPEN (default: 30.0).
        rolling_window_seconds: Time window in seconds for counting failures.
            Only failures within this window count toward the threshold
            (default: 60.0).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        rolling_window_seconds: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._rolling_window_seconds = rolling_window_seconds

        # Internal state keyed by "{provider}:{model}"
        self._states: dict[str, CircuitState] = {}
        self._failure_timestamps: dict[str, deque[float]] = {}
        self._last_failure_time: dict[str, float] = {}
        # Track whether a probe request has been issued in HALF_OPEN state
        self._half_open_probe_sent: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(provider: str, model: str) -> str:
        """Build the internal dictionary key for a provider-model pair."""
        return f"{provider}:{model}"

    def _get_raw_state(self, key: str) -> CircuitState:
        """Return the stored state for a key, defaulting to CLOSED."""
        return self._states.get(key, CircuitState.CLOSED)

    def _transition(self, key: str, new_state: CircuitState) -> None:
        """Transition to a new state and log at WARNING level."""
        old_state = self._get_raw_state(key)
        self._states[key] = new_state
        logger.warning(
            "Circuit breaker state transition: %s %s → %s (failure_count=%d)",
            key,
            old_state,
            new_state,
            self._failures_in_window(key),
        )

    def _failures_in_window(self, key: str) -> int:
        """Return the number of failures within the rolling window for a key."""
        timestamps = self._failure_timestamps.get(key)
        if not timestamps:
            return 0
        now = time.time()
        cutoff = now - self._rolling_window_seconds
        # Evict expired timestamps from the front of the deque
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        return len(timestamps)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_allow(self, provider: str, model: str) -> bool:
        """Determine whether a request to the given provider-model should proceed.

        Returns True if the circuit is CLOSED or if a single probe is allowed
        in HALF_OPEN state. Returns False if the circuit is OPEN (and cooldown
        has not yet elapsed).
        """
        key = self._key(provider, model)
        state = self._get_raw_state(key)

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # Check if cooldown has elapsed → transition to HALF_OPEN
            last_failure = self._last_failure_time.get(key, 0.0)
            elapsed = time.time() - last_failure
            if elapsed >= self._cooldown_seconds:
                self._transition(key, CircuitState.HALF_OPEN)
                self._half_open_probe_sent[key] = False
                # Fall through to HALF_OPEN logic below
            else:
                return False

        # HALF_OPEN: allow exactly one probe request
        if self._get_raw_state(key) == CircuitState.HALF_OPEN:
            if not self._half_open_probe_sent.get(key, False):
                self._half_open_probe_sent[key] = True
                return True
            # Probe already sent, waiting for result — reject additional requests
            return False

        return False  # pragma: no cover — defensive fallback

    def record_success(self, provider: str, model: str) -> None:
        """Record a successful request for the given provider-model pair.

        In HALF_OPEN state this transitions the circuit back to CLOSED.
        In CLOSED state this resets the failure timestamps (consecutive
        failure chain is broken).
        """
        key = self._key(provider, model)
        state = self._get_raw_state(key)

        if state == CircuitState.HALF_OPEN:
            # Probe succeeded — close the circuit
            self._failure_timestamps.pop(key, None)
            self._half_open_probe_sent.pop(key, None)
            self._transition(key, CircuitState.CLOSED)
        else:
            # CLOSED state — reset failure timestamps on any success
            self._failure_timestamps.pop(key, None)

    def record_failure(self, provider: str, model: str) -> None:
        """Record a failed request for the given provider-model pair.

        In CLOSED state, records the failure timestamp and opens the circuit
        if the number of failures within the rolling window reaches the
        threshold. In HALF_OPEN state, transitions back to OPEN and restarts
        the cooldown timer.
        """
        key = self._key(provider, model)
        state = self._get_raw_state(key)
        now = time.time()

        if state == CircuitState.HALF_OPEN:
            # Probe failed — reopen the circuit and restart cooldown
            self._last_failure_time[key] = now
            self._half_open_probe_sent.pop(key, None)
            self._transition(key, CircuitState.OPEN)
            return

        # CLOSED state — record failure timestamp
        if key not in self._failure_timestamps:
            self._failure_timestamps[key] = deque()
        self._failure_timestamps[key].append(now)

        # Evict failures outside the rolling window
        cutoff = now - self._rolling_window_seconds
        while self._failure_timestamps[key] and self._failure_timestamps[key][0] < cutoff:
            self._failure_timestamps[key].popleft()

        # Check if threshold is reached within the window
        if len(self._failure_timestamps[key]) >= self._failure_threshold:
            self._last_failure_time[key] = now
            self._transition(key, CircuitState.OPEN)

    def get_state(self, provider: str, model: str) -> CircuitState:
        """Return the current circuit state for a provider-model pair.

        This also checks for cooldown expiry and may transition from OPEN to
        HALF_OPEN if the cooldown has elapsed.
        """
        key = self._key(provider, model)
        state = self._get_raw_state(key)

        if state == CircuitState.OPEN:
            last_failure = self._last_failure_time.get(key, 0.0)
            elapsed = time.time() - last_failure
            if elapsed >= self._cooldown_seconds:
                self._transition(key, CircuitState.HALF_OPEN)
                self._half_open_probe_sent[key] = False
                return CircuitState.HALF_OPEN

        return state

    def reset(self, provider: str, model: str) -> None:
        """Fully reset the circuit breaker state for a provider-model pair.

        Clears failure timestamps, state, and timers — returning the pair to a
        clean CLOSED state.
        """
        key = self._key(provider, model)
        self._states.pop(key, None)
        self._failure_timestamps.pop(key, None)
        self._last_failure_time.pop(key, None)
        self._half_open_probe_sent.pop(key, None)


def create_circuit_breaker() -> CircuitBreaker:
    """Create a CircuitBreaker instance configured from application settings.

    Reads CIRCUIT_BREAKER_FAILURE_THRESHOLD, CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    and CIRCUIT_BREAKER_ROLLING_WINDOW_SECONDS from config.py settings.
    """
    from src.config import get_settings

    settings = get_settings()
    return CircuitBreaker(
        failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        rolling_window_seconds=settings.CIRCUIT_BREAKER_ROLLING_WINDOW_SECONDS,
    )
