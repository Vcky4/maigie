"""Realtime/WebSocket routes."""

from fastapi import APIRouter, WebSocket

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    # TODO: Implement WebSocket handling
    await websocket.close()

