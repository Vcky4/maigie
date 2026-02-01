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


async def publish_ws_event(user_id: str, payload: dict[str, Any]) -> None:
    """
    Publish a websocket event (type: event) to Redis.

    API instances will subscribe and forward to connected clients.
    """
    # IMPORTANT: Celery tasks frequently wrap async logic with `asyncio.run()`,
    # which creates/closes an event loop per task invocation. A cached/global
    # `redis.asyncio` client can become bound to a different loop and will then
    # throw errors like:
    # - "got Future attached to a different loop"
    # - "Event loop is closed"
    #
    # To avoid cross-loop issues in worker processes, use a short-lived client.
    redis_client = redis.from_url(
        get_redis_connection_url(),
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        message = {"userId": user_id, "message": {"type": "event", "payload": payload}}
        await redis_client.publish(WS_EVENT_CHANNEL, json.dumps(message))
    finally:
        try:
            await redis_client.close()
        except Exception:
            pass


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
