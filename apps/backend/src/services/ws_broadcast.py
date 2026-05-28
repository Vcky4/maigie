"""
Redis Pub/Sub broadcast layer for WebSocket messages.

Enables multi-instance WebSocket scaling by publishing messages to Redis
and subscribing on all instances. When a message needs to reach a user
who may be connected to a different server instance, it's published to
Redis and all instances attempt local delivery.

Usage:
    from src.services.ws_broadcast import broadcast

    # Publish a message to a user (reaches them on any instance)
    await broadcast.publish_to_user(user_id, {"type": "balance_update", ...})

    # Start the subscriber (called once at app startup)
    await broadcast.start_subscriber()

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.core.cache import cache

logger = logging.getLogger(__name__)

# Redis pub/sub channel for cross-instance WebSocket messages
_CHANNEL = "maigie:ws:broadcast"


class WebSocketBroadcast:
    """Redis-backed broadcast layer for WebSocket messages across instances."""

    def __init__(self):
        self._subscriber_task: asyncio.Task | None = None
        self._pubsub = None

    async def publish_to_user(self, user_id: str, data: dict[str, Any]) -> bool:
        """
        Publish a message to a user via Redis pub/sub.

        First attempts local delivery. If the user isn't connected locally,
        publishes to Redis so other instances can deliver.

        Args:
            user_id: Target user ID.
            data: JSON-serializable message payload.

        Returns:
            True if published successfully, False otherwise.
        """
        from src.services.socket_manager import manager

        # Try local delivery first
        local_connections = manager.active_connections.get(user_id)
        if local_connections:
            try:
                await manager.send_json(data, user_id)
                return True
            except Exception:
                pass

        # Publish to Redis for other instances
        if not cache._connected or not cache.redis:
            return False

        try:
            message = json.dumps({"user_id": user_id, "data": data})
            await cache.redis.publish(_CHANNEL, message.encode("utf-8"))
            return True
        except Exception as e:
            logger.debug("WebSocket broadcast publish failed: %s", e)
            return False

    async def start_subscriber(self) -> None:
        """
        Start the Redis pub/sub subscriber.
        Call once at application startup.
        """
        if not cache._connected or not cache.redis:
            logger.info("Redis not available — WebSocket broadcast disabled (single-instance mode)")
            return

        try:
            self._pubsub = cache.redis.pubsub()
            await self._pubsub.subscribe(_CHANNEL)
            self._subscriber_task = asyncio.create_task(self._listen())
            logger.info("WebSocket broadcast subscriber started")
        except Exception as e:
            logger.warning("Failed to start WebSocket broadcast subscriber: %s", e)

    async def stop_subscriber(self) -> None:
        """Stop the Redis pub/sub subscriber."""
        if self._subscriber_task:
            self._subscriber_task.cancel()
            self._subscriber_task = None
        if self._pubsub:
            await self._pubsub.unsubscribe(_CHANNEL)
            await self._pubsub.aclose()
            self._pubsub = None

    async def _listen(self) -> None:
        """Background task that listens for broadcast messages and delivers locally."""
        from src.services.socket_manager import manager

        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    payload = json.loads(message["data"])
                    user_id = payload.get("user_id")
                    data = payload.get("data")

                    if not user_id or not data:
                        continue

                    # Attempt local delivery
                    if user_id in manager.active_connections:
                        try:
                            await manager.send_json(data, user_id)
                        except Exception:
                            pass
                except (json.JSONDecodeError, KeyError):
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WebSocket broadcast listener error: %s", e)


# Global instance
broadcast = WebSocketBroadcast()
