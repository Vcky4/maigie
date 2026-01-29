"""
WebSocket Connection Manager.
Handles active connections and message broadcasting.
"""

import logging
from typing import Dict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Maps user_id -> WebSocket connection
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept a new WebSocket connection and store it."""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info(f"User {user_id} connected via WebSocket.")

    def disconnect(self, user_id: str):
        """Remove a user's connection."""
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            logger.info(f"User {user_id} disconnected.")

    async def send_personal_message(self, message: str, user_id: str):
        """Send a text message to a specific user."""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            await websocket.send_text(message)

    async def send_json(self, data: dict, user_id: str):
        """Send a JSON object to a specific user."""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            await websocket.send_json(data)

    async def send_stream_chunk(
        self, chunk: str, user_id: str, is_final: bool = False, usage_info: dict = None
    ):
        """
        Send a streaming chunk to a specific user.

        Args:
            chunk: The text chunk to send
            user_id: The user's ID
            is_final: Whether this is the final chunk
            usage_info: Token usage info (only on final chunk)
        """
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            data = {
                "type": "stream",
                "chunk": chunk,
                "isFinal": is_final,
            }
            if is_final and usage_info:
                data["usage"] = usage_info
            await websocket.send_json(data)


# Global instance
manager = ConnectionManager()
