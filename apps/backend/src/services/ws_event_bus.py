"""
Redis-backed event bus for forwarding worker events to WebSocket clients.

This is used to bridge Celery workers (separate processes) and the chat WebSocket
connections handled by the API server process.

Event format (published to Redis):
{
  "userId": "<user-id>",
  "message": {
    "type": "event",
    "payload": { ... }
  }
}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.utils.dependencies import get_redis_connection_url, initialize_redis_client, redis

logger = logging.getLogger(__name__)

WS_EVENT_CHANNEL = "maigie:ws_events"

# Per-worker-process reusable Redis client for publishing events.
# Created lazily on first use, reused across all tasks in the same worker process.
_worker_redis_client: redis.Redis | None = None


async def _get_worker_redis() -> redis.Redis:
    """Get or create a reusable Redis client for the current worker process.

    Unlike the previous approach (new connection per publish), this reuses
    a single connection across all tasks in the same prefork child process.
    The client is bound to the current event loop.
    """
    global _worker_redis_client

    if _worker_redis_client is not None:
        try:
            await _worker_redis_client.ping()
            return _worker_redis_client
        except Exception:
            # Connection is dead — recreate
            try:
                await _worker_redis_client.close()
            except Exception:
                pass
            _worker_redis_client = None

    _worker_redis_client = redis.from_url(
        get_redis_connection_url(),
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    return _worker_redis_client


async def publish_ws_event(user_id: str, payload: dict[str, Any]) -> None:
    """
    Publish a websocket event (type: event) to Redis.

    API instances will subscribe and forward to connected clients.
    Uses a per-worker-process reusable Redis connection to avoid
    connection churn (previously created/destroyed a connection per call).
    """
    try:
        client = await _get_worker_redis()
        message = {"userId": user_id, "message": {"type": "event", "payload": payload}}
        await client.publish(WS_EVENT_CHANNEL, json.dumps(message))
    except Exception as e:
        logger.warning("publish_ws_event failed: %s", e)


async def ws_event_forwarder(stop_event: asyncio.Event) -> None:
    """
    Subscribe to Redis and forward worker events to connected WebSocket clients.

    This runs inside the API process.
    """
    # Import lazily to avoid pulling WebSocket/server-only dependencies into
    # Celery workers (which only need `publish_ws_event`).
    from src.services.socket_manager import manager as chat_ws_manager

    redis_client = await initialize_redis_client()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(WS_EVENT_CHANNEL)

    logger.info(f"WS event forwarder subscribed to {WS_EVENT_CHANNEL}")
    try:
        while not stop_event.is_set():
            # Timeout so we can check stop_event and exit promptly.
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message:
                continue

            try:
                data = message.get("data")
                if not data:
                    continue

                event = json.loads(data)
                user_id = event.get("userId")
                msg = event.get("message")

                if not user_id or not isinstance(msg, dict):
                    continue

                # Forward only if user is connected to this API instance.
                await chat_ws_manager.send_json(msg, user_id)
            except Exception as e:
                logger.warning(f"Failed to forward WS event: {e}", exc_info=True)
    finally:
        try:
            await pubsub.unsubscribe(WS_EVENT_CHANNEL)
            await pubsub.close()
        except Exception:
            pass

        logger.info("WS event forwarder stopped")
