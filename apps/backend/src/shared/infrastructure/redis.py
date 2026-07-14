"""
Redis client for caching and pub/sub.

Provides a production-ready Cache class with graceful degradation
when Redis is unavailable. Used across all domains for caching,
rate limiting, and real-time event forwarding.
"""

import json
import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


class Cache:
    """Redis cache with automatic serialization and graceful degradation."""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            settings = get_settings()
        self.settings = settings
        self.key_prefix = settings.REDIS_KEY_PREFIX
        self.redis: redis.Redis | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Connect to Redis. Degrades gracefully if unavailable."""
        try:
            self.redis = redis.from_url(
                self.settings.REDIS_URL,
                socket_timeout=self.settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=self.settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                decode_responses=False,
            )
            await self.redis.ping()
            self._connected = True
            logger.info("Redis connected")
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"Redis unavailable: {e}. Running in degraded mode.")
            self._connected = False
        except Exception as e:
            logger.error(f"Unexpected Redis error: {e}")
            self._connected = False

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self.redis:
            try:
                await self.redis.aclose()
            except Exception as e:
                logger.error(f"Error disconnecting Redis: {e}")
            finally:
                self.redis = None
                self._connected = False
                logger.info("Redis disconnected")

    def make_key(self, parts: list[str]) -> str:
        """Create a namespaced cache key from parts."""
        key = ":".join(str(p) for p in parts)
        return f"{self.key_prefix}{key}" if self.key_prefix else key

    # --- Core operations ---

    async def get(self, key: str) -> Any:
        """Get and deserialize a cached value."""
        if not self._connected or not self.redis:
            return None
        try:
            value = await self.redis.get(key.encode("utf-8"))
            return self._deserialize(value)
        except (RedisConnectionError, RedisTimeoutError):
            self._connected = False
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    async def set(self, key: str, value: Any, expire: int | None = None) -> bool:
        """Serialize and store a value with optional TTL (seconds)."""
        if not self._connected or not self.redis:
            return False
        try:
            serialized = self._serialize(value)
            if expire:
                await self.redis.setex(key.encode("utf-8"), expire, serialized)
            else:
                await self.redis.set(key.encode("utf-8"), serialized)
            return True
        except (RedisConnectionError, RedisTimeoutError):
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key."""
        if not self._connected or not self.redis:
            return False
        try:
            return bool(await self.redis.delete(key.encode("utf-8")))
        except (RedisConnectionError, RedisTimeoutError):
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if not self._connected or not self.redis:
            return False
        try:
            return bool(await self.redis.exists(key.encode("utf-8")))
        except (RedisConnectionError, RedisTimeoutError):
            self._connected = False
            return False
        except Exception:
            return False

    async def increment(self, key: str, amount: int = 1) -> int | None:
        """Atomically increment a counter."""
        if not self._connected or not self.redis:
            return None
        try:
            return await self.redis.incrby(key.encode("utf-8"), amount)
        except (RedisConnectionError, RedisTimeoutError):
            self._connected = False
            return None
        except Exception:
            return None

    async def expire(self, key: str, seconds: int) -> bool:
        """Set TTL on an existing key."""
        if not self._connected or not self.redis:
            return False
        try:
            return bool(await self.redis.expire(key.encode("utf-8"), seconds))
        except Exception:
            return False

    async def health_check(self) -> dict[str, Any]:
        """Check Redis health."""
        if not self.redis:
            return {"status": "disconnected", "type": "redis"}
        try:
            await self.redis.ping()
            self._connected = True
            info = await self.redis.info("server")
            return {
                "status": "healthy",
                "type": "redis",
                "version": info.get("redis_version", "unknown"),
            }
        except Exception as e:
            self._connected = False
            return {"status": "unhealthy", "type": "redis", "error": str(e)}

    # --- Serialization ---

    def _serialize(self, value: Any) -> bytes:
        if isinstance(value, bytes | bytearray):
            return bytes(value)
        return json.dumps(value, default=str).encode("utf-8")

    def _deserialize(self, value: bytes | None) -> Any:
        if value is None:
            return None
        try:
            decoded = value.decode("utf-8")
            return json.loads(decoded)
        except (json.JSONDecodeError, ValueError):
            return decoded
        except (UnicodeDecodeError, AttributeError):
            return value


# Global instance
cache = Cache()


async def get_cache() -> Cache:
    """Dependency injection helper."""
    return cache
