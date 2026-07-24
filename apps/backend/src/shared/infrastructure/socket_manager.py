"""Stub — implementation pending migration from services/socket_manager."""

from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """WebSocket connection manager."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str) -> str:
        """Accept a WebSocket connection and return a connection ID."""
        await websocket.accept()
        return f"{user_id}_conn"  # TODO: migrate implementation

    async def disconnect(self, connection_id: str, user_id: str) -> None:
        """Disconnect a WebSocket connection."""
        pass  # TODO: migrate implementation

    async def send_json(self, data: dict[str, Any], user_id: str) -> None:
        """Send JSON data to all connections for a user."""
        pass  # TODO: migrate implementation

    async def send_to_user(self, user_id: str, data: dict[str, Any]) -> None:
        """Send data to a specific user."""
        pass  # TODO: migrate implementation


manager = ConnectionManager()
