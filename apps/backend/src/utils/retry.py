"""
Retry utilities for resilient service calls.

Implements exponential backoff retry logic with jitter.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import logging
import random
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """
        Initialize retry configuration.

        Args:
            max_attempts: Maximum number of retry attempts
            initial_delay: Initial delay in seconds
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential backoff
            jitter: Whether to add random jitter to delays
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


async def retry_with_backoff(
    func: Callable[..., T],
    config: RetryConfig | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Execute function with exponential backoff retry.

    Args:
        func: Function to execute
        config: Retry configuration
        retryable_exceptions: Exception types that should trigger retry
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Function result

    Raises:
        Last exception if all retries fail
    """
    if config is None:
        config = RetryConfig()

    last_exception: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        except retryable_exceptions as e:
            last_exception = e

            if attempt == config.max_attempts - 1:
                # Last attempt failed
                logger.error(
                    f"Retry failed after {config.max_attempts} attempts: {e}",
                    exc_info=True,
                )
                raise

            # Calculate delay with exponential backoff
            delay = min(
                config.initial_delay * (config.exponential_base**attempt),
                config.max_delay,
            )

            # Add jitter to prevent thundering herd
            if config.jitter:
                jitter_amount = delay * 0.1 * random.random()
                delay += jitter_amount

            logger.warning(
                f"Retry attempt {attempt + 1}/{config.max_attempts} after {delay:.2f}s: {e}"
            )

            await asyncio.sleep(delay)

    # Should never reach here, but type checker needs it
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic failed unexpectedly")
