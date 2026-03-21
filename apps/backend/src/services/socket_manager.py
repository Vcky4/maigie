"""
WebSocket Connection Manager.
Handles active connections and message broadcasting.
"""

import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Maps user_id -> WebSocket connection
        self.active_connections: dict[str, WebSocket] = {}
        # Maps room_id -> set[user_id]
        self.room_members: dict[str, set[str]] = defaultdict(set)
        # Maps user_id -> set[room_id]
        self.user_rooms: dict[str, set[str]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept a new WebSocket connection and store it."""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info(f"User {user_id} connected via WebSocket.")

    def disconnect(self, user_id: str):
        """Remove a user's connection."""
        self.leave_all_rooms(user_id)
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            logger.info(f"User {user_id} disconnected.")

    def join_room(self, user_id: str, room_id: str):
        """Subscribe a connected user to a logical room."""
        if user_id not in self.active_connections or not room_id:
            return
        self.room_members[room_id].add(user_id)
        self.user_rooms[user_id].add(room_id)

    def leave_room(self, user_id: str, room_id: str):
        """Unsubscribe a user from a logical room."""
        if not room_id:
            return
        members = self.room_members.get(room_id)
        if members:
            members.discard(user_id)
            if not members:
                self.room_members.pop(room_id, None)

        rooms = self.user_rooms.get(user_id)
        if rooms:
            rooms.discard(room_id)
            if not rooms:
                self.user_rooms.pop(user_id, None)

    def leave_all_rooms(self, user_id: str):
        """Remove a user from every subscribed room."""
        for room_id in list(self.user_rooms.get(user_id, set())):
            self.leave_room(user_id, room_id)

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

    async def send_room_message(
        self,
        message: str,
        room_id: str,
        *,
        exclude_user_id: str | None = None,
    ):
        """Send a text message to everyone subscribed to a room."""
        for member_user_id in list(self.room_members.get(room_id, set())):
            if exclude_user_id and member_user_id == exclude_user_id:
                continue
            await self.send_personal_message(message, member_user_id)

    async def send_room_json(
        self,
        data: dict,
        room_id: str,
        *,
        exclude_user_id: str | None = None,
    ):
        """Send a JSON payload to everyone subscribed to a room."""
        for member_user_id in list(self.room_members.get(room_id, set())):
            if exclude_user_id and member_user_id == exclude_user_id:
                continue
            await self.send_json(data, member_user_id)


# Global instance
manager = ConnectionManager()
