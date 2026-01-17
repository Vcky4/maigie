"""
Rate limiting utilities for API endpoints.

Implements token bucket algorithm using Redis for distributed rate limiting.
Supports per-user and per-IP rate limiting.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import get_settings
from ..core.cache import cache

logger = logging.getLogger(__name__)


class RateLimitError(HTTPException):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, retry_after: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Rate limit exceeded. Please try again later.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


class RateLimiter:
    """
    Token bucket rate limiter using Redis.

    Implements a distributed token bucket algorithm where:
    - Tokens are added at a fixed rate (refill_rate per second)
    - Each request consumes one token
    - If no tokens available, request is rejected
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        key_prefix: str = "rate_limit",
    ):
        """
        Initialize rate limiter.

        Args:
            capacity: Maximum number of tokens in bucket
            refill_rate: Tokens added per second
            key_prefix: Prefix for Redis keys
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.key_prefix = key_prefix

    async def check_rate_limit(self, identifier: str) -> tuple[bool, int]:
        """
        Check if request should be allowed.

        Args:
            identifier: Unique identifier (user_id, IP, etc.)

        Returns:
            Tuple of (allowed: bool, retry_after: int)
        """
        key = f"{self.key_prefix}:{identifier}"
        now = time.time()

        try:
            # Get current state from Redis
            data = await cache.get(key)
            if data is None:
                # First request - initialize bucket
                tokens = self.capacity - 1
                last_refill = now
            else:
                tokens = data.get("tokens", 0)
                last_refill = data.get("last_refill", now)

            # Calculate tokens to add based on time elapsed
            time_elapsed = now - last_refill
            tokens_to_add = time_elapsed * self.refill_rate
            tokens = min(self.capacity, tokens + tokens_to_add)

            # Check if request can be processed
            if tokens >= 1:
                tokens -= 1
                await cache.set(
                    key,
                    {"tokens": tokens, "last_refill": now},
                    expire=3600,  # Expire after 1 hour of inactivity
                )
                return True, 0
            else:
                # Calculate retry after time
                retry_after = int((1 - tokens) / self.refill_rate) + 1
                await cache.set(
                    key,
                    {"tokens": tokens, "last_refill": now},
                    expire=3600,
                )
                return False, retry_after

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}", exc_info=True)
            # On error, allow request (fail open)
            return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware for rate limiting requests.

    Supports per-IP and per-user rate limiting.
    """

    def __init__(
        self,
        app: Any,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        """
        Initialize rate limit middleware.

        Args:
            app: FastAPI application
            requests_per_minute: Max requests per minute per IP
            requests_per_hour: Max requests per hour per IP
        """
        super().__init__(app)
        self.per_minute_limiter = RateLimiter(
            capacity=requests_per_minute,
            refill_rate=requests_per_minute / 60.0,
            key_prefix="rate_limit:minute",
        )
        self.per_hour_limiter = RateLimiter(
            capacity=requests_per_hour,
            refill_rate=requests_per_hour / 3600.0,
            key_prefix="rate_limit:hour",
        )

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Check rate limits before processing request."""
        # Skip rate limiting for health checks and metrics
        if request.url.path in ["/health", "/ready", "/metrics", "/"]:
            return await call_next(request)

        # Get identifier (IP address)
        client_ip = request.client.host if request.client else "unknown"

        # Check per-minute limit
        allowed, retry_after = await self.per_minute_limiter.check_rate_limit(client_ip)
        if not allowed:
            raise RateLimitError(retry_after)

        # Check per-hour limit
        allowed, retry_after = await self.per_hour_limiter.check_rate_limit(client_ip)
        if not allowed:
            raise RateLimitError(retry_after)

        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit-Minute"] = str(self.per_minute_limiter.capacity)
        response.headers["X-RateLimit-Limit-Hour"] = str(self.per_hour_limiter.capacity)

        return response


def get_user_rate_limiter() -> RateLimiter:
    """Get rate limiter for authenticated users."""
    settings = get_settings()
    return RateLimiter(
        capacity=settings.RATE_LIMIT_USER_CAPACITY,
        refill_rate=settings.RATE_LIMIT_USER_REFILL_RATE,
        key_prefix="rate_limit:user",
    )


async def check_user_rate_limit(
    user_id: str,
    limiter: Annotated[RateLimiter, Depends(get_user_rate_limiter)],
) -> None:
    """
    Dependency to check user rate limit.

    Raises RateLimitError if limit exceeded.
    """
    allowed, retry_after = await limiter.check_rate_limit(user_id)
    if not allowed:
        raise RateLimitError(retry_after)
