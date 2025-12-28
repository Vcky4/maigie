"""
Realtime/WebSocket routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.core.websocket import manager  # Ensure src/core/socket.py was renamed to websocket.py

logger = logging.getLogger(__name__)

# Note: No prefix needed for WS usually, or use "/ws" if you prefer
router = APIRouter(tags=["Realtime"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user_id: str = Query(...)):
    """
    WebSocket endpoint for real-time updates.
    Connect via: ws://localhost:8000/ws?user_id=YOUR_USER_ID
    """
    # 1. Accept the connection and register user
    connection_id = await manager.connect(websocket, user_id)

    try:
        while True:
            # 2. Listen for messages (required to keep connection open)
            data = await websocket.receive_json()

            # 3. Handle Heartbeat responses from client (if client sends them)
            if isinstance(data, dict) and data.get("type") == "heartbeat":
                await manager.handle_heartbeat(connection_id)

    except WebSocketDisconnect:
        # Handle standard disconnect
        await manager.disconnect(connection_id, reason="client_disconnect")

    except Exception as e:
        # Handle unexpected errors
        logger.error(f"WebSocket error for user {user_id}: {e}")
        await manager.disconnect(connection_id, reason="error")
