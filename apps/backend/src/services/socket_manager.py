"""
WebSocket Connection Manager.
Handles active connections and message broadcasting.
"""

import logging
from collections import defaultdict
from uuid import uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Maps user_id -> {connection_id -> WebSocket}
        self.active_connections: dict[str, dict[str, WebSocket]] = defaultdict(dict)
        # Maps connection_id -> user_id
        self.connection_users: dict[str, str] = {}
        # Maps room_id -> set[connection_id]
        self.room_members: dict[str, set[str]] = defaultdict(set)
        # Maps connection_id -> set[room_id]
        self.connection_rooms: dict[str, set[str]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept a new WebSocket connection and store it."""
        await websocket.accept()
        connection_id = uuid4().hex
        self.active_connections[user_id][connection_id] = websocket
        self.connection_users[connection_id] = user_id
        logger.info(f"User {user_id} connected via WebSocket ({connection_id}).")
        return connection_id

    def disconnect(self, connection_id: str):
        """Remove a specific websocket connection."""
        user_id = self.connection_users.pop(connection_id, None)
        if not user_id:
            return

        self.leave_all_rooms(connection_id)

        user_connections = self.active_connections.get(user_id)
        if user_connections:
            user_connections.pop(connection_id, None)
            if not user_connections:
                self.active_connections.pop(user_id, None)

        logger.info(f"User {user_id} disconnected websocket ({connection_id}).")

    def join_room(self, connection_id: str, room_id: str):
        """Subscribe a websocket connection to a logical room."""
        if connection_id not in self.connection_users or not room_id:
            return
        self.room_members[room_id].add(connection_id)
        self.connection_rooms[connection_id].add(room_id)

    def leave_room(self, connection_id: str, room_id: str):
        """Unsubscribe a websocket connection from a logical room."""
        if not room_id:
            return
        members = self.room_members.get(room_id)
        if members:
            members.discard(connection_id)
            if not members:
                self.room_members.pop(room_id, None)

        rooms = self.connection_rooms.get(connection_id)
        if rooms:
            rooms.discard(room_id)
            if not rooms:
                self.connection_rooms.pop(connection_id, None)

    def leave_all_rooms(self, connection_id: str):
        """Remove a websocket connection from every subscribed room."""
        for room_id in list(self.connection_rooms.get(connection_id, set())):
            self.leave_room(connection_id, room_id)

    async def send_connection_message(self, message: str, connection_id: str):
        """Send a text message to a specific websocket connection."""
        user_id = self.connection_users.get(connection_id)
        if not user_id:
            return
        websocket = self.active_connections.get(user_id, {}).get(connection_id)
        if not websocket:
            return
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.debug("send_connection_message failed (%s): %s", connection_id, e)
            self.disconnect(connection_id)
            raise

    async def send_connection_json(self, data: dict, connection_id: str):
        """Send JSON to a specific websocket connection."""
        user_id = self.connection_users.get(connection_id)
        if not user_id:
            return
        websocket = self.active_connections.get(user_id, {}).get(connection_id)
        if not websocket:
            return
        try:
            await websocket.send_json(data)
        except Exception as e:
            logger.debug("send_connection_json failed (%s): %s", connection_id, e)
            self.disconnect(connection_id)
            raise

    async def send_personal_message(self, message: str, user_id: str):
        """Send a text message to all active connections of a user."""
        conns = list(self.active_connections.get(user_id, {}).items())
        last_error: Exception | None = None
        for connection_id, websocket in conns:
            try:
                await websocket.send_text(message)
                last_error = None
                break
            except Exception as e:
                logger.debug("send_personal_message failed (%s): %s", connection_id, e)
                self.disconnect(connection_id)
                last_error = e
        if last_error is not None:
            raise last_error

    async def send_json(self, data: dict, user_id: str):
        """Send a JSON object to all active connections of a user."""
        conns = list(self.active_connections.get(user_id, {}).items())
        last_error: Exception | None = None
        for connection_id, websocket in conns:
            try:
                await websocket.send_json(data)
                last_error = None
                break
            except Exception as e:
                logger.debug("send_json failed (%s): %s", connection_id, e)
                self.disconnect(connection_id)
                last_error = e
        if last_error is not None:
            raise last_error

    async def send_room_message(
        self,
        message: str,
        room_id: str,
        *,
        exclude_connection_id: str | None = None,
    ):
        """Send a text message to everyone subscribed to a room."""
        for member_connection_id in list(self.room_members.get(room_id, set())):
            if exclude_connection_id and member_connection_id == exclude_connection_id:
                continue
            await self.send_connection_message(message, member_connection_id)

    async def send_room_json(
        self,
        data: dict,
        room_id: str,
        *,
        exclude_connection_id: str | None = None,
    ):
        """Send a JSON payload to everyone subscribed to a room."""
        for member_connection_id in list(self.room_members.get(room_id, set())):
            if exclude_connection_id and member_connection_id == exclude_connection_id:
                continue
            await self.send_connection_json(data, member_connection_id)


# Global instance
manager = ConnectionManager()
