"""
WebSocket message models.

Copyright (C) 2024 Maigie Team

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

from typing import Any, Literal

from pydantic import BaseModel, Field


class WebSocketMessage(BaseModel):
    """Base WebSocket message model."""

    type: str = Field(..., description="Message type")
    data: dict[str, Any] | None = Field(None, description="Message data")
    timestamp: str | None = Field(None, description="Message timestamp")


class HeartbeatMessage(BaseModel):
    """Heartbeat message model."""

    type: Literal["heartbeat"] = "heartbeat"
    status: Literal["ping", "pong"] = Field(..., description="Heartbeat status")


class SubscribeMessage(BaseModel):
    """Subscribe to channel message."""

    type: Literal["subscribe"] = "subscribe"
    channel: str = Field(..., description="Channel name to subscribe to")


class UnsubscribeMessage(BaseModel):
    """Unsubscribe from channel message."""

    type: Literal["unsubscribe"] = "unsubscribe"
    channel: str = Field(..., description="Channel name to unsubscribe from")


class BroadcastMessage(BaseModel):
    """Broadcast message model."""

    type: Literal["broadcast"] = "broadcast"
    channel: str | None = Field(None, description="Channel to broadcast to")
    message: dict[str, Any] = Field(..., description="Message to broadcast")


class ConnectionMessage(BaseModel):
    """Connection status message."""

    type: Literal["connection"] = "connection"
    status: Literal["connected", "disconnected", "reconnected"] = Field(
        ..., description="Connection status"
    )
    connection_id: str | None = Field(None, description="Connection ID")
    heartbeat_interval: int | None = Field(None, description="Heartbeat interval in seconds")


class ErrorMessage(BaseModel):
    """Error message model."""

    type: Literal["error"] = "error"
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: dict[str, Any] | None = Field(None, description="Error details")
