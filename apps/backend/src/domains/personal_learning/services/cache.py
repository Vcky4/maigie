"""
Simple async TTL cache for the Personal Learning domain.

Reduces database load for frequently-read, infrequently-changing data like
behaviour profiles, flashcard stats, and learning profiles.

This is an in-process LRU+TTL cache — no external dependency (Redis).
Suitable for single-instance or low-traffic multi-instance deployments.
For high-traffic multi-instance deployments, swap this for a Redis-backed
implementation with the same interface.

Usage:
    from .cache import cached

    @cached(ttl_seconds=60, max_size=500)
    async def get_behaviour_profile(*, user_id: str) -> dict:
        ...

    # Invalidate a specific entry:
    get_behaviour_profile.invalidate(user_id="abc123")

    # Clear all entries for a function:
    get_behaviour_profile.clear()
"""

import asyncio
import functools
import logging
import time
from collections import OrderedDict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class _TTLCache:
    """Thread-safe (asyncio-safe) LRU cache with per-entry TTL."""

    __slots__ = ("_cache", "_ttl", "_max_size", "_lock")

    def __init__(self, ttl_seconds: float, max_size: int):
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[bool, Any]:
        """Get a cached value. Returns (hit: bool, value: Any)."""
        async with self._lock:
            if key not in self._cache:
                return False, None

            expires_at, value = self._cache[key]
            if time.monotonic() > expires_at:
                # Expired — evict
                del self._cache[key]
                return False, None

            # Move to end (LRU)
            self._cache.move_to_end(key)
            return True, value

    async def set(self, key: str, value: Any) -> None:
        """Store a value with TTL."""
        async with self._lock:
            self._cache[key] = (time.monotonic() + self._ttl, value)
            self._cache.move_to_end(key)

            # Evict oldest if over capacity
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._cache.clear()


def cached(
    ttl_seconds: float = 60,
    max_size: int = 500,
    key_arg: str = "user_id",
) -> Callable:
    """Decorator that adds TTL caching to an async function.

    Args:
        ttl_seconds: How long entries live (default 60s).
        max_size: Maximum cache entries (LRU eviction beyond this).
        key_arg: The keyword argument to use as cache key (default "user_id").

    The decorated function gains two extra methods:
        - .invalidate(**kwargs): Remove the cache entry for given key_arg value.
        - .clear(): Remove all cache entries for this function.
    """

    def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
        cache = _TTLCache(ttl_seconds=ttl_seconds, max_size=max_size)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract cache key from kwargs
            cache_key = kwargs.get(key_arg)
            if cache_key is None:
                # Can't cache without a key — call through
                return await func(*args, **kwargs)

            cache_key = str(cache_key)

            # Check cache
            hit, value = await cache.get(cache_key)
            if hit:
                return value

            # Cache miss — compute
            result = await func(*args, **kwargs)

            # Store in cache
            await cache.set(cache_key, result)
            return result

        async def invalidate(**kwargs: Any) -> None:
            """Invalidate the cache entry for a specific key."""
            key_value = kwargs.get(key_arg)
            if key_value is not None:
                await cache.invalidate(str(key_value))

        async def clear() -> None:
            """Clear all cache entries for this function."""
            await cache.clear()

        wrapper.invalidate = invalidate  # type: ignore[attr-defined]
        wrapper.clear = clear  # type: ignore[attr-defined]
        wrapper.cache = cache  # type: ignore[attr-defined]

        return wrapper

    return decorator
