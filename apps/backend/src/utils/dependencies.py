"""
Core service dependencies for dependency injection.

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
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ..config import get_settings

# Placeholder imports - assuming prisma.Client() and redis.Redis() are available
# These will be properly configured once Prisma and Redis are fully set up
try:
    from prisma import Client as PrismaClient

    PRISMA_AVAILABLE = True
except ImportError:
    # Fallback placeholder if Prisma is not yet installed
    PRISMA_AVAILABLE = False

    class PrismaClient:  # type: ignore
        """Placeholder Prisma client."""

        _is_mock = True  # Flag to identify mock clients

        async def connect(self):
            """Mock connect method."""
            pass

        async def disconnect(self):
            """Mock disconnect method."""
            pass

        async def query_raw(self, query: str):
            """Mock raw query method - raises error to indicate no real connection."""
            raise Exception(
                "Prisma client not installed. Run: poetry add prisma && poetry run prisma generate"
            )


try:
    import redis.asyncio as redis

    RedisClient = redis.Redis
    REDIS_AVAILABLE = True
except ImportError:
    # Fallback placeholder if Redis is not yet installed
    REDIS_AVAILABLE = False

    class MockRedis:
        """Mock Redis client."""

        _is_mock = True  # Flag to identify mock clients

        async def ping(self):
            """Mock ping method - raises error to indicate no real connection."""
            raise Exception("Redis client not installed - using mock implementation")

        async def close(self):
            """Mock close method."""
            pass

    class MockRedisModule:
        """Placeholder Redis module."""

        Redis = MockRedis

        @staticmethod
        def from_url(url: str, **kwargs):
            """Mock from_url method."""
            return MockRedis()

    redis = MockRedisModule()  # type: ignore
    RedisClient = MockRedis  # type: ignore


# Global client instances
_prisma_client: PrismaClient | None = None
_redis_client: RedisClient | None = None  # type: ignore


@asynccontextmanager
async def get_prisma_lifecycle() -> AsyncGenerator[PrismaClient, None]:
    """
    Asynchronous context manager for Prisma client lifecycle.

    Manages connection and disconnection of Prisma client.
    This should be used in the application lifespan context.

    Yields:
        PrismaClient: Connected Prisma client instance
    """
    global _prisma_client

    if _prisma_client is None:
        _prisma_client = PrismaClient()

    await _prisma_client.connect()
    try:
        yield _prisma_client
    finally:
        await _prisma_client.disconnect()
        _prisma_client = None


async def get_db_client() -> AsyncGenerator[PrismaClient, None]:
    """
    FastAPI dependency function for database (Prisma) client.

    Yields the connected Prisma client instance for use in route handlers.
    The client must be initialized in the application lifespan context.

    Yields:
        PrismaClient: Connected Prisma client instance

    Raises:
        RuntimeError: If Prisma client is not initialized
    """
    global _prisma_client

    if _prisma_client is None:
        # Initialize client if not already done
        _prisma_client = PrismaClient()
        await _prisma_client.connect()

    yield _prisma_client


def get_redis_connection_url() -> str:
    """
    Get Redis connection URL from settings.

    Returns:
        str: Redis connection URL
    """
    settings = get_settings()
    return settings.REDIS_URL


async def initialize_redis_client() -> RedisClient:  # type: ignore
    """
    Initialize the global Redis client.

    Returns:
        RedisClient: Connected Redis client instance
    """
    global _redis_client

    if _redis_client is None:
        redis_url = get_redis_connection_url()
        _redis_client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    return _redis_client


async def close_redis_client() -> None:
    """Close the global Redis client connection."""
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None


async def get_redis_client() -> AsyncGenerator[RedisClient, None]:  # type: ignore
    """
    FastAPI dependency function for cache (Redis) client.

    Yields the connected Redis client instance for use in route handlers.
    The client must be initialized in the application lifespan context.

    Yields:
        RedisClient: Connected Redis client instance

    Raises:
        RuntimeError: If Redis client is not initialized
    """
    global _redis_client

    if _redis_client is None:
        _redis_client = await initialize_redis_client()

    yield _redis_client


async def cleanup_db_client() -> None:
    """Clean up database client on shutdown."""
    global _prisma_client

    if _prisma_client is not None:
        await _prisma_client.disconnect()
        _prisma_client = None
