"""
Circuit breaker pattern implementation for external service calls.

Implements circuit breaker to prevent cascading failures and provide
graceful degradation when external services are unavailable.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, TypeVar

from ..core.cache import cache

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""

    pass


class CircuitBreaker:
    """
    Circuit breaker for external service calls.

    Prevents cascading failures by:
    1. Tracking failures
    2. Opening circuit after threshold failures
    3. Allowing test requests after timeout
    4. Closing circuit when service recovers
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: int = 60,
        expected_exception: type[Exception] = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Unique name for this circuit breaker
            failure_threshold: Number of failures before opening circuit
            success_threshold: Number of successes to close circuit from half-open
            timeout: Seconds to wait before attempting half-open
            expected_exception: Exception type that counts as failure
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._cache_key_prefix = f"circuit_breaker:{name}"

    async def _get_state(self) -> CircuitState:
        """Get current state from cache."""
        try:
            data = await cache.get(f"{self._cache_key_prefix}:state")
            if data:
                return CircuitState(data.get("state", "closed"))
        except Exception:
            pass
        return CircuitState.CLOSED

    async def _set_state(self, state: CircuitState) -> None:
        """Save state to cache."""
        try:
            await cache.set(
                f"{self._cache_key_prefix}:state",
                {"state": state.value, "timestamp": time.time()},
                expire=3600,
            )
        except Exception:
            pass

    async def _get_failure_count(self) -> int:
        """Get failure count from cache."""
        try:
            data = await cache.get(f"{self._cache_key_prefix}:failures")
            if data:
                return data.get("count", 0)
        except Exception:
            pass
        return 0

    async def _increment_failure(self) -> None:
        """Increment failure count."""
        try:
            count = await self._get_failure_count()
            await cache.set(
                f"{self._cache_key_prefix}:failures",
                {"count": count + 1, "timestamp": time.time()},
                expire=3600,
            )
        except Exception:
            pass

    async def _reset_failure_count(self) -> None:
        """Reset failure count."""
        try:
            await cache.delete(f"{self._cache_key_prefix}:failures")
        except Exception:
            pass

    async def _record_success(self) -> None:
        """Record successful call."""
        try:
            data = await cache.get(f"{self._cache_key_prefix}:successes")
            count = data.get("count", 0) + 1 if data else 1
            await cache.set(
                f"{self._cache_key_prefix}:successes",
                {"count": count, "timestamp": time.time()},
                expire=3600,
            )
        except Exception:
            pass

    async def _get_last_failure_time(self) -> float | None:
        """Get last failure time."""
        try:
            data = await cache.get(f"{self._cache_key_prefix}:failures")
            if data:
                return data.get("timestamp")
        except Exception:
            pass
        return None

    async def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerError: If circuit is open
        """
        state = await self._get_state()

        # Check if circuit should transition from open to half-open
        if state == CircuitState.OPEN:
            last_failure = await self._get_last_failure_time()
            if last_failure and (time.time() - last_failure) >= self.timeout:
                state = CircuitState.HALF_OPEN
                await self._set_state(state)
                logger.info(f"Circuit breaker {self.name} transitioning to HALF_OPEN")
            else:
                raise CircuitBreakerError(
                    f"Circuit breaker {self.name} is OPEN. Service unavailable."
                )

        # Execute function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Success - update state
            if state == CircuitState.HALF_OPEN:
                success_count = await self._get_success_count()
                if success_count + 1 >= self.success_threshold:
                    await self._set_state(CircuitState.CLOSED)
                    await self._reset_failure_count()
                    logger.info(f"Circuit breaker {self.name} CLOSED - service recovered")
                else:
                    await self._record_success()
            else:
                # Reset failure count on success
                await self._reset_failure_count()

            return result

        except self.expected_exception as e:
            # Failure - update state
            await self._increment_failure()
            failure_count = await self._get_failure_count()

            if failure_count >= self.failure_threshold:
                await self._set_state(CircuitState.OPEN)
                logger.warning(f"Circuit breaker {self.name} OPENED after {failure_count} failures")
            elif state == CircuitState.HALF_OPEN:
                await self._set_state(CircuitState.OPEN)
                logger.warning(f"Circuit breaker {self.name} re-OPENED after test failure")

            raise

    async def _get_success_count(self) -> int:
        """Get success count from cache."""
        try:
            data = await cache.get(f"{self._cache_key_prefix}:successes")
            if data:
                return data.get("count", 0)
        except Exception:
            pass
        return 0
