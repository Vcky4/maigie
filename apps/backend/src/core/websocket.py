"""
WebSocket connection manager and event broadcasting framework.

Copyright (C) 2025 Maigie

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections, user channels, and event broadcasting.

    Features:
    - Connection management per user
    - Event broadcasting to users/channels
    - Heartbeat mechanism
    - Reconnection handling
    """

    def __init__(
        self,
        heartbeat_interval: int = 30,
        heartbeat_timeout: int = 60,
        max_reconnect_attempts: int = 5,
    ):
        """
        Initialize connection manager.

        Args:
            heartbeat_interval: Seconds between heartbeat pings
            heartbeat_timeout: Seconds before considering connection dead
            max_reconnect_attempts: Maximum reconnection attempts before giving up
        """
        # Active connections: {connection_id: WebSocket}
        self.active_connections: dict[str, WebSocket] = {}

        # User connections: {user_id: Set[connection_id]}
        self.user_connections: dict[str, set[str]] = {}

        # Connection metadata: {connection_id: {user_id, last_ping, created_at}}
        self.connection_metadata: dict[str, dict[str, Any]] = {}

        # Channel subscriptions: {channel: Set[connection_id]}
        self.channel_subscriptions: dict[str, set[str]] = {}

        # Heartbeat configuration
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.max_reconnect_attempts = max_reconnect_attempts

        # Background tasks
        self._heartbeat_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket, user_id: str | None = None) -> str:
        """
        Accept a new WebSocket connection.

        Args:
            websocket: WebSocket connection
            user_id: Optional user ID for authenticated connections

        Returns:
            Connection ID
        """
        await websocket.accept()

        connection_id = str(uuid4())
        self.active_connections[connection_id] = websocket
        self.connection_metadata[connection_id] = {
            "user_id": user_id,
            "last_ping": datetime.utcnow(),
            "created_at": datetime.utcnow(),
            "reconnect_count": 0,
        }

        if user_id:
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
            self.user_connections[user_id].add(connection_id)

        logger.info(f"WebSocket connected: {connection_id} (user: {user_id or 'anonymous'})")

        # Send welcome message
        await self.send_personal_message(
            connection_id,
            {
                "type": "connection",
                "status": "connected",
                "connection_id": connection_id,
                "heartbeat_interval": self.heartbeat_interval,
            },
        )

        return connection_id

    async def disconnect(self, connection_id: str, reason: str = "client_disconnect"):
        """
        Disconnect a WebSocket connection.

        Args:
            connection_id: Connection ID to disconnect
            reason: Reason for disconnection
        """
        if connection_id not in self.active_connections:
            return

        websocket = self.active_connections.pop(connection_id)
        metadata = self.connection_metadata.pop(connection_id, {})
        user_id = metadata.get("user_id")

        # Remove from user connections
        if user_id and user_id in self.user_connections:
            self.user_connections[user_id].discard(connection_id)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

        # Remove from all channels
        for channel in list(self.channel_subscriptions.keys()):
            self.channel_subscriptions[channel].discard(connection_id)
            if not self.channel_subscriptions[channel]:
                del self.channel_subscriptions[channel]

        try:
            await websocket.close()
        except Exception as e:
            logger.warning(f"Error closing WebSocket {connection_id}: {e}")

        logger.info(
            f"WebSocket disconnected: {connection_id} (user: {user_id or 'anonymous'}, reason: {reason})"
        )

    async def send_personal_message(self, connection_id: str, message: dict[str, Any]):
        """
        Send a message to a specific connection.

        Args:
            connection_id: Connection ID
            message: Message to send
        """
        if connection_id not in self.active_connections:
            logger.warning(f"Connection {connection_id} not found")
            return

        websocket = self.active_connections[connection_id]
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending message to {connection_id}: {e}")
            await self.disconnect(connection_id, reason="send_error")

    async def send_to_user(self, user_id: str, message: dict[str, Any]):
        """
        Send a message to all connections for a specific user.

        Args:
            user_id: User ID
            message: Message to send
        """
        if user_id not in self.user_connections:
            return

        connection_ids = list(self.user_connections[user_id])
        for connection_id in connection_ids:
            await self.send_personal_message(connection_id, message)

    async def broadcast(self, message: dict[str, Any], exclude: set[str] | None = None):
        """
        Broadcast a message to all active connections.

        Args:
            message: Message to broadcast
            exclude: Optional set of connection IDs to exclude
        """
        exclude = exclude or set()
        connection_ids = [cid for cid in self.active_connections.keys() if cid not in exclude]

        for connection_id in connection_ids:
            await self.send_personal_message(connection_id, message)

    async def broadcast_to_channel(self, channel: str, message: dict[str, Any]):
        """
        Broadcast a message to all connections subscribed to a channel.

        Args:
            channel: Channel name
            message: Message to broadcast
        """
        if channel not in self.channel_subscriptions:
            return

        connection_ids = list(self.channel_subscriptions[channel])
        for connection_id in connection_ids:
            await self.send_personal_message(connection_id, message)

    def subscribe_to_channel(self, connection_id: str, channel: str):
        """
        Subscribe a connection to a channel.

        Args:
            connection_id: Connection ID
            channel: Channel name
        """
        if connection_id not in self.active_connections:
            return

        if channel not in self.channel_subscriptions:
            self.channel_subscriptions[channel] = set()

        self.channel_subscriptions[channel].add(connection_id)
        logger.debug(f"Connection {connection_id} subscribed to channel {channel}")

    def unsubscribe_from_channel(self, connection_id: str, channel: str):
        """
        Unsubscribe a connection from a channel.

        Args:
            connection_id: Connection ID
            channel: Channel name
        """
        if channel in self.channel_subscriptions:
            self.channel_subscriptions[channel].discard(connection_id)
            if not self.channel_subscriptions[channel]:
                del self.channel_subscriptions[channel]

        logger.debug(f"Connection {connection_id} unsubscribed from channel {channel}")

    async def handle_heartbeat(self, connection_id: str):
        """
        Handle heartbeat ping from client.

        Args:
            connection_id: Connection ID
        """
        if connection_id in self.connection_metadata:
            self.connection_metadata[connection_id]["last_ping"] = datetime.utcnow()
            await self.send_personal_message(connection_id, {"type": "heartbeat", "status": "pong"})

    async def start_heartbeat(self):
        """Start the heartbeat task."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self):
        """Stop the heartbeat task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self):
        """Background task to send heartbeat pings and detect dead connections."""
        while True:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                now = datetime.utcnow()
                dead_connections = []

                for connection_id, metadata in list(self.connection_metadata.items()):
                    last_ping = metadata.get("last_ping", metadata.get("created_at"))
                    time_since_ping = (now - last_ping).total_seconds()

                    # Send ping if interval elapsed
                    if time_since_ping >= self.heartbeat_interval:
                        try:
                            await self.send_personal_message(
                                connection_id, {"type": "heartbeat", "status": "ping"}
                            )
                        except Exception:
                            dead_connections.append(connection_id)

                    # Mark as dead if timeout exceeded
                    if time_since_ping > self.heartbeat_timeout:
                        dead_connections.append(connection_id)

                # Clean up dead connections
                for connection_id in dead_connections:
                    await self.disconnect(connection_id, reason="heartbeat_timeout")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")

    async def start_cleanup(self):
        """Start the cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self):
        """Stop the cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self):
        """Background task to periodically clean up stale data."""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes

                # Clean up empty channel subscriptions
                empty_channels = [
                    channel
                    for channel, connections in self.channel_subscriptions.items()
                    if not connections
                ]
                for channel in empty_channels:
                    del self.channel_subscriptions[channel]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    def get_connection_count(self) -> int:
        """Get the number of active connections."""
        return len(self.active_connections)

    def get_user_connection_count(self, user_id: str) -> int:
        """Get the number of connections for a user."""
        return len(self.user_connections.get(user_id, set()))

    def get_channel_subscriber_count(self, channel: str) -> int:
        """Get the number of subscribers to a channel."""
        return len(self.channel_subscriptions.get(channel, set()))


# Global connection manager instance
manager = ConnectionManager()
