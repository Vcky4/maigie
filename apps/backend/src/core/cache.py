"""
Redis cache utilities for Maigie backend.

This module provides a production-ready Redis caching layer that integrates
seamlessly with the FastAPI application lifecycle. It includes connection
management, error handling, serialization, and common caching utilities.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Usage:
    ```python
    from .core.cache import cache

    # Basic operations
    await cache.set("user:123", {"name": "John"}, expire=3600)
    user = await cache.get("user:123")
    await cache.delete("user:123")

    # Using key prefixing
    key = cache.make_key(["user", "123", "profile"])
    # Returns: "maigie:user:123:profile"

    # Common utilities
    exists = await cache.exists("user:123")
    await cache.expire("user:123", 3600)
    ttl = await cache.ttl("user:123")
    await cache.increment("counter:views", 1)
    ```
"""

import json
import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    RedisError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


class Cache:
    """Redis cache connection manager with comprehensive caching utilities.

    This class provides a high-level interface to Redis with automatic
    serialization, error handling, and graceful degradation when Redis
    is unavailable.

    Attributes:
        key_prefix: Prefix for all cache keys (default: "maigie:")
        redis: Redis async client instance
        _connected: Connection status flag
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize cache connection manager.

        Args:
            settings: Application settings. If None, will fetch from get_settings().
        """
        if settings is None:
            settings = get_settings()

        self.settings = settings
        self.key_prefix = settings.REDIS_KEY_PREFIX
        self.redis: redis.Redis | None = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to Redis cache.

        Creates a Redis connection pool and verifies connectivity with a ping.
        Sets up connection with appropriate timeouts from settings.

        Raises:
            RedisConnectionError: If unable to connect to Redis server.
        """
        try:
            self.redis = redis.from_url(
                self.settings.REDIS_URL,
                socket_timeout=self.settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=self.settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                decode_responses=False,  # We handle encoding ourselves
            )
            # Test connection
            await self.redis.ping()
            self._connected = True
            logger.info("Redis cache connected successfully")
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Failed to connect to Redis: {e}. Cache will operate in degraded mode.")
            self._connected = False
            # Don't raise - allow graceful degradation
        except Exception as e:
            logger.error(f"Unexpected error connecting to Redis: {e}")
            self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from Redis cache.

        Closes the Redis connection pool gracefully.
        """
        if self.redis:
            try:
                await self.redis.aclose()
                logger.info("Redis cache disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting from Redis: {e}")
            finally:
                self.redis = None
                self._connected = False

    def make_key(self, parts: list[str]) -> str:
        """Create a namespaced cache key.

        Combines key parts with the configured prefix and separators.

        Args:
            parts: List of key parts to combine (e.g., ["user", "123", "profile"])

        Returns:
            Fully qualified cache key (e.g., "maigie:user:123:profile")

        Example:
            ```python
            key = cache.make_key(["user", user_id, "profile"])
            await cache.set(key, user_data)
            ```
        """
        key = ":".join(str(part) for part in parts)
        return f"{self.key_prefix}{key}" if self.key_prefix else key

    def _serialize(self, value: Any) -> bytes:
        """Serialize a Python value to bytes for Redis storage.

        Handles JSON-serializable types (dict, list, str, int, float, bool, None)
        and native bytes/bytearray types.

        Args:
            value: Python value to serialize

        Returns:
            Bytes representation suitable for Redis storage
        """
        if isinstance(value, bytes | bytearray):
            return bytes(value)
        if isinstance(value, dict | list | tuple):
            return json.dumps(value).encode("utf-8")
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, int | float | bool) or value is None:
            return json.dumps(value).encode("utf-8")
        # For other types, try JSON serialization
        return json.dumps(value, default=str).encode("utf-8")

    def _deserialize(self, value: bytes | None) -> Any:
        """Deserialize bytes from Redis to Python value.

        Attempts JSON deserialization first, falls back to string decoding.

        Args:
            value: Bytes value from Redis (or None if key doesn't exist)

        Returns:
            Deserialized Python value, or None if value is None
        """
        if value is None:
            return None

        try:
            # Try JSON deserialization first
            decoded = value.decode("utf-8")
            try:
                return json.loads(decoded)
            except (json.JSONDecodeError, ValueError):
                # Not JSON, return as string
                return decoded
        except (UnicodeDecodeError, AttributeError):
            # Not UTF-8, return as bytes
            return value

    async def get(self, key: str) -> Any:
        """Get value from cache.

        Retrieves and deserializes a value from Redis.

        Args:
            key: Cache key to retrieve

        Returns:
            Deserialized value, or None if key doesn't exist or Redis is unavailable

        Example:
            ```python
            user = await cache.get("user:123")
            if user is None:
                # Cache miss - fetch from database
                user = await db.get_user(123)
                await cache.set("user:123", user, expire=3600)
            ```
        """
        if not self._connected or not self.redis:
            return None

        try:
            value = await self.redis.get(key.encode("utf-8"))
            return self._deserialize(value)
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during get: {e}")
            self._connected = False
            return None
        except RedisError as e:
            logger.error(f"Redis error during get: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during cache get: {e}")
            return None

    async def set(self, key: str, value: Any, expire: int | None = None) -> bool:
        """Set value in cache.

        Serializes and stores a value in Redis with optional expiration.

        Args:
            key: Cache key
            value: Value to cache (will be serialized)
            expire: Optional expiration time in seconds

        Returns:
            True if successful, False otherwise (including when Redis is unavailable)

        Example:
            ```python
            await cache.set("user:123", {"name": "John"}, expire=3600)
            ```
        """
        if not self._connected or not self.redis:
            return False

        try:
            serialized = self._serialize(value)
            if expire:
                result = await self.redis.setex(key.encode("utf-8"), expire, serialized)
            else:
                result = await self.redis.set(key.encode("utf-8"), serialized)
            return bool(result)
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during set: {e}")
            self._connected = False
            return False
        except RedisError as e:
            logger.error(f"Redis error during set: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during cache set: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted, False otherwise (including when Redis is unavailable)

        Example:
            ```python
            await cache.delete("user:123")
            ```
        """
        if not self._connected or not self.redis:
            return False

        try:
            result = await self.redis.delete(key.encode("utf-8"))
            return bool(result)
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during delete: {e}")
            self._connected = False
            return False
        except RedisError as e:
            logger.error(f"Redis error during delete: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during cache delete: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key to check

        Returns:
            True if key exists, False otherwise (including when Redis is unavailable)

        Example:
            ```python
            if await cache.exists("user:123"):
                user = await cache.get("user:123")
            ```
        """
        if not self._connected or not self.redis:
            return False

        try:
            result = await self.redis.exists(key.encode("utf-8"))
            return bool(result)
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during exists: {e}")
            self._connected = False
            return False
        except RedisError as e:
            logger.error(f"Redis error during exists: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during cache exists: {e}")
            return False

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration time on an existing key.

        Args:
            key: Cache key
            seconds: Expiration time in seconds

        Returns:
            True if expiration was set, False otherwise

        Example:
            ```python
            await cache.expire("user:123", 3600)  # Expire in 1 hour
            ```
        """
        if not self._connected or not self.redis:
            return False

        try:
            result = await self.redis.expire(key.encode("utf-8"), seconds)
            return bool(result)
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during expire: {e}")
            self._connected = False
            return False
        except RedisError as e:
            logger.error(f"Redis error during expire: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during cache expire: {e}")
            return False

    async def ttl(self, key: str) -> int | None:
        """Get remaining time-to-live for a key.

        Args:
            key: Cache key

        Returns:
            Remaining TTL in seconds, or None if key doesn't exist or has no expiration

        Example:
            ```python
            ttl = await cache.ttl("user:123")
            if ttl and ttl < 300:  # Less than 5 minutes left
                await cache.expire("user:123", 3600)  # Refresh
            ```
        """
        if not self._connected or not self.redis:
            return None

        try:
            result = await self.redis.ttl(key.encode("utf-8"))
            # Redis returns -1 for keys without expiration, -2 for non-existent keys
            if result == -1:
                return None  # No expiration set
            if result == -2:
                return None  # Key doesn't exist
            return result if result > 0 else None
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during ttl: {e}")
            self._connected = False
            return None
        except RedisError as e:
            logger.error(f"Redis error during ttl: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during cache ttl: {e}")
            return None

    async def increment(self, key: str, amount: int = 1) -> int | None:
        """Atomically increment a numeric value in cache.

        Args:
            key: Cache key
            amount: Amount to increment by (default: 1)

        Returns:
            New value after increment, or None if operation failed

        Example:
            ```python
            views = await cache.increment("page:views", 1)
            ```
        """
        if not self._connected or not self.redis:
            return None

        try:
            result = await self.redis.incrby(key.encode("utf-8"), amount)
            return result
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during increment: {e}")
            self._connected = False
            return None
        except RedisError as e:
            logger.error(f"Redis error during increment: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during cache increment: {e}")
            return None

    async def decrement(self, key: str, amount: int = 1) -> int | None:
        """Atomically decrement a numeric value in cache.

        Args:
            key: Cache key
            amount: Amount to decrement by (default: 1)

        Returns:
            New value after decrement, or None if operation failed

        Example:
            ```python
            remaining = await cache.decrement("tokens:remaining", 1)
            ```
        """
        if not self._connected or not self.redis:
            return None

        try:
            result = await self.redis.decrby(key.encode("utf-8"), amount)
            return result
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during decrement: {e}")
            self._connected = False
            return None
        except RedisError as e:
            logger.error(f"Redis error during decrement: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during cache decrement: {e}")
            return None

    async def keys(self, pattern: str) -> list[str]:
        """Get keys matching a pattern.

        Warning: Use sparingly in production as this can be slow on large datasets.
        Consider using SCAN for better performance with large key sets.

        Args:
            pattern: Redis key pattern (e.g., "user:*")

        Returns:
            List of matching keys (without prefix), empty list if Redis unavailable

        Example:
            ```python
            user_keys = await cache.keys("user:*")
            ```
        """
        if not self._connected or not self.redis:
            return []

        try:
            # Encode pattern and search
            encoded_pattern = pattern.encode("utf-8")
            matching_keys = await self.redis.keys(encoded_pattern)
            # Decode and remove prefix if present
            decoded_keys = [key.decode("utf-8") for key in matching_keys]
            if self.key_prefix:
                prefix_len = len(self.key_prefix)
                decoded_keys = [
                    key[prefix_len:] if key.startswith(self.key_prefix) else key
                    for key in decoded_keys
                ]
            return decoded_keys
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during keys: {e}")
            self._connected = False
            return []
        except RedisError as e:
            logger.error(f"Redis error during keys: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error during cache keys: {e}")
            return []

    async def flush(self) -> bool:
        """Flush all keys from cache.

        Warning: This deletes ALL keys in the Redis database. Use only for
        development/testing or when you're certain you want to clear everything.

        Returns:
            True if successful, False otherwise

        Example:
            ```python
            # Only in development!
            if settings.DEBUG:
                await cache.flush()
            ```
        """
        if not self._connected or not self.redis:
            return False

        try:
            await self.redis.flushdb()
            logger.warning("Cache flushed - all keys deleted")
            return True
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis connection error during flush: {e}")
            self._connected = False
            return False
        except RedisError as e:
            logger.error(f"Redis error during flush: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during cache flush: {e}")
            return False

    async def health_check(self) -> dict[str, Any]:
        """Check cache health and connection status.

        Performs a ping to Redis and returns connection status and server info.

        Returns:
            Dictionary with status, type, and optional Redis server info

        Example:
            ```python
            health = await cache.health_check()
            # Returns: {"status": "healthy", "type": "redis", "version": "7.0.0"}
            ```
        """
        if not self.redis:
            return {
                "status": "disconnected",
                "type": "redis",
                "error": "Redis client not initialized",
            }

        try:
            # Test connection with ping
            await self.redis.ping()
            self._connected = True

            # Get Redis server info
            info = await self.redis.info("server")
            version = info.get("redis_version", "unknown")

            return {
                "status": "healthy",
                "type": "redis",
                "version": version,
                "connected": True,
            }
        except (RedisConnectionError, RedisTimeoutError) as e:
            self._connected = False
            return {
                "status": "unhealthy",
                "type": "redis",
                "error": str(e),
                "connected": False,
            }
        except RedisError as e:
            self._connected = False
            return {
                "status": "unhealthy",
                "type": "redis",
                "error": str(e),
                "connected": False,
            }
        except Exception as e:
            logger.error(f"Unexpected error during cache health check: {e}")
            self._connected = False
            return {
                "status": "error",
                "type": "redis",
                "error": str(e),
                "connected": False,
            }


# Global cache instance
cache = Cache()


async def get_cache() -> Cache:
    """Get cache instance for dependency injection.

    Returns:
        Global cache instance

    Example:
        ```python
        from .core.cache import get_cache

        async def my_endpoint(cache: Cache = Depends(get_cache)):
            value = await cache.get("my:key")
        ```
    """
    return cache
