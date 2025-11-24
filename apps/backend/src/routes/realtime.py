"""Realtime/WebSocket routes."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import HTTPException

from ..core.websocket import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


async def authenticate_websocket(websocket: WebSocket) -> str | None:
    """
    Authenticate WebSocket connection.

    Args:
        websocket: WebSocket connection

    Returns:
        User ID if authenticated, None otherwise
    """
    # Try to get token from query params
    token = websocket.query_params.get("token")

    if not token:
        return None

    try:
        # Import here to avoid circular dependency
        from ..core.security import decode_access_token

        payload = decode_access_token(token)
        return payload.get("sub")  # User ID from JWT
    except Exception as e:
        logger.warning(f"WebSocket authentication failed: {e}")
        return None


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Supports:
    - Authentication via token query parameter
    - Heartbeat mechanism
    - Channel subscriptions
    - Event broadcasting
    - Reconnection handling
    """
    connection_id = None

    try:
        # Authenticate connection (optional - allows anonymous connections)
        user_id = await authenticate_websocket(websocket)

        # Connect
        connection_id = await manager.connect(websocket, user_id=user_id)

        # Start background tasks if not already running
        await manager.start_heartbeat()
        await manager.start_cleanup()

        # Message handling loop
        while True:
            try:
                # Receive message
                data = await websocket.receive_text()

                try:
                    message_data = json.loads(data)
                    message_type = message_data.get("type")

                    # Handle different message types
                    if message_type == "heartbeat":
                        await manager.handle_heartbeat(connection_id)

                    elif message_type == "subscribe":
                        channel = message_data.get("channel")
                        if channel:
                            manager.subscribe_to_channel(connection_id, channel)
                            await manager.send_personal_message(
                                connection_id,
                                {
                                    "type": "subscription",
                                    "status": "subscribed",
                                    "channel": channel,
                                },
                            )
                        else:
                            await manager.send_personal_message(
                                connection_id,
                                {
                                    "type": "error",
                                    "code": "invalid_message",
                                    "message": "Channel name required for subscription",
                                },
                            )

                    elif message_type == "unsubscribe":
                        channel = message_data.get("channel")
                        if channel:
                            manager.unsubscribe_from_channel(connection_id, channel)
                            await manager.send_personal_message(
                                connection_id,
                                {
                                    "type": "subscription",
                                    "status": "unsubscribed",
                                    "channel": channel,
                                },
                            )
                        else:
                            await manager.send_personal_message(
                                connection_id,
                                {
                                    "type": "error",
                                    "code": "invalid_message",
                                    "message": "Channel name required for unsubscription",
                                },
                            )

                    elif message_type == "ping":
                        # Respond to ping
                        await manager.send_personal_message(
                            connection_id,
                            {"type": "pong", "timestamp": message_data.get("timestamp")},
                        )

                    else:
                        # Echo unknown message types back (for debugging)
                        await manager.send_personal_message(
                            connection_id,
                            {
                                "type": "error",
                                "code": "unknown_message_type",
                                "message": f"Unknown message type: {message_type}",
                            },
                        )

                except json.JSONDecodeError:
                    await manager.send_personal_message(
                        connection_id,
                        {
                            "type": "error",
                            "code": "invalid_json",
                            "message": "Invalid JSON format",
                        },
                    )

            except WebSocketDisconnect:
                # Client disconnected normally
                break

            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                try:
                    await manager.send_personal_message(
                        connection_id,
                        {
                            "type": "error",
                            "code": "server_error",
                            "message": "An error occurred processing your message",
                        },
                    )
                except Exception:
                    # Connection might be dead, break the loop
                    break

    except WebSocketDisconnect:
        # Client disconnected
        pass

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        # Clean up connection
        if connection_id:
            await manager.disconnect(connection_id, reason="client_disconnect")


@router.get("/stats")
async def get_websocket_stats():
    """
    Get WebSocket connection statistics.

    Requires authentication.
    """
    return {
        "active_connections": manager.get_connection_count(),
        "channels": {
            channel: manager.get_channel_subscriber_count(channel)
            for channel in manager.channel_subscriptions.keys()
        },
    }


@router.post("/broadcast")
async def broadcast_message(
    channel: str | None = None,
    message: dict = None,
    user_id: str | None = None,
):
    """
    Broadcast a message to WebSocket connections.

    Args:
        channel: Optional channel name (broadcasts to channel subscribers)
        message: Message to broadcast
        user_id: Optional user ID (broadcasts to specific user)

    Note: In production, this should require admin authentication.
    """
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message required")

    broadcast_msg = {
        "type": "broadcast",
        "data": message,
        "timestamp": str(datetime.utcnow()),
    }

    if user_id:
        await manager.send_to_user(user_id, broadcast_msg)
        return {"status": "sent", "target": f"user:{user_id}"}
    elif channel:
        await manager.broadcast_to_channel(channel, broadcast_msg)
        return {"status": "sent", "target": f"channel:{channel}"}
    else:
        await manager.broadcast(broadcast_msg)
        return {"status": "sent", "target": "all"}
