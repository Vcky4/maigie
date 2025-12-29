import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.core.websocket import manager  # <--- NEW NAME

logger = logging.getLogger(__name__)
router = APIRouter()

# NOTE: Startup/Shutdown events are removed because main.py handles them!


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user_id: str = Query(...)):
    """
    WebSocket endpoint.
    Connect via: ws://localhost:8000/ws?user_id=123
    """
    connection_id = await manager.connect(websocket, user_id)

    try:
        while True:
            # Listen for messages (mostly for heartbeats)
            data = await websocket.receive_json()

            # If client sends a heartbeat ping/pong, update the timestamp
            if isinstance(data, dict) and data.get("type") == "heartbeat":
                await manager.handle_heartbeat(connection_id)

    except WebSocketDisconnect:
        await manager.disconnect(connection_id, reason="client_disconnect")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(connection_id, reason="error")
