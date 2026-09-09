"""Simple Redis-based rate limiter for API endpoints.

Uses a sliding window counter pattern with Redis INCR + EXPIRE.
Degrades gracefully (allows requests) when Redis is unavailable.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request, status

from src.core.cache import cache

logger = logging.getLogger(__name__)


async def check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Check if a request is within the rate limit.

    Uses Redis INCR with TTL for a simple fixed-window counter.
    Degrades gracefully — if Redis is unavailable, requests are allowed.

    Args:
        key: Unique rate limit key (e.g. "rl:models:pref:{user_id}")
        max_requests: Maximum requests allowed in the window.
        window_seconds: Time window in seconds.

    Returns:
        Tuple of (allowed: bool, remaining: int).
        If Redis is down, returns (True, max_requests).
    """
    if not cache._connected or not cache.redis:
        return True, max_requests

    try:
        full_key = cache.make_key(["rl", key])
        current = await cache.increment(full_key, 1)

        if current is None:
            # Redis error — degrade gracefully
            return True, max_requests

        if current == 1:
            # First request in this window — set expiry
            await cache.expire(full_key, window_seconds)

        remaining = max(0, max_requests - current)
        allowed = current <= max_requests
        return allowed, remaining

    except Exception as e:
        logger.warning("Rate limit check failed: %s", e)
        return True, max_requests


async def check_rate_limit_strict(
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """Like `check_rate_limit`, but **fails closed** when Redis is unavailable.

    `check_rate_limit` degrades open, which is the right call for a logged-in convenience
    endpoint: a Redis outage should not stop a paying learner from working, and the caller is
    already identified and metered elsewhere.

    That reasoning inverts for an **unauthenticated** endpoint that spends money. There is no
    account to charge, no usage window to exhaust, and no name in the logs — the rate limit *is*
    the whole control. Degrading open there means a Redis blip converts into an open tap on the
    LLM budget, and the first signal is the bill.

    So this variant refuses when it cannot count. A visitor sees "try again shortly" during an
    outage, which is a worse minute for them and a bounded one for us. Use it only where the cost
    of allowing an uncounted request exceeds the cost of refusing a legitimate one.

    Returns:
        Tuple of (allowed, remaining). If Redis is down, returns (False, 0).
    """
    if not cache._connected or not cache.redis:
        logger.warning("Strict rate limit refusing %s — cache unavailable", key)
        return False, 0

    try:
        full_key = cache.make_key(["rl", key])
        current = await cache.increment(full_key, 1)

        if current is None:
            logger.warning(
                "Strict rate limit refusing %s — increment returned nothing", key
            )
            return False, 0

        if current == 1:
            await cache.expire(full_key, window_seconds)

        remaining = max(0, max_requests - current)
        return current <= max_requests, remaining

    except Exception as e:
        logger.warning("Strict rate limit check failed for %s, refusing: %s", key, e)
        return False, 0


async def enforce_rate_limit(
    user_id: str,
    endpoint: str,
    max_requests: int = 10,
    window_seconds: int = 60,
) -> None:
    """Enforce a rate limit, raising HTTP 429 if exceeded.

    Args:
        user_id: The user's unique identifier.
        endpoint: Short endpoint identifier (e.g. "models_pref").
        max_requests: Maximum requests allowed in the window.
        window_seconds: Time window in seconds.

    Raises:
        HTTPException: 429 Too Many Requests if limit exceeded.
    """
    key = f"{endpoint}:{user_id}"
    allowed, remaining = await check_rate_limit(key, max_requests, window_seconds)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. Maximum {max_requests} requests "
                f"per {window_seconds} seconds. Please try again later."
            ),
            headers={"Retry-After": str(window_seconds)},
        )
