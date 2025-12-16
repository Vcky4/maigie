import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from src.core.websocket import manager  # Assumes you renamed socket.py to websocket.py

logger = logging.getLogger(__name__)
router = APIRouter()

# NOTE: Startup/Shutdown events are removed here because 
# src/main.py -> lifespan() already handles manager.start_heartbeat()

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    user_id: str = Query(...)
):
    """
    WebSocket endpoint for real-time updates.
    Connect via: ws://localhost:8000/ws?user_id=123
    """
    # 1. Connect using your manager (returns a connection_id)
    connection_id = await manager.connect(websocket, user_id)
    
    try:
        while True:
            # 2. Listen for messages (required to keep connection open)
            data = await websocket.receive_json()
            
            # 3. Handle Heartbeat responses from client
            if isinstance(data, dict) and data.get("type") == "heartbeat":
                await manager.handle_heartbeat(connection_id)
                
    except WebSocketDisconnect:
        await manager.disconnect(connection_id, reason="client_disconnect")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(connection_id, reason="error")