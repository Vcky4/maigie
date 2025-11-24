"""WebSocket message models."""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class WebSocketMessage(BaseModel):
    """Base WebSocket message model."""

    type: str = Field(..., description="Message type")
    data: Optional[Dict[str, Any]] = Field(None, description="Message data")
    timestamp: Optional[str] = Field(None, description="Message timestamp")


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
    channel: Optional[str] = Field(None, description="Channel to broadcast to")
    message: Dict[str, Any] = Field(..., description="Message to broadcast")


class ConnectionMessage(BaseModel):
    """Connection status message."""

    type: Literal["connection"] = "connection"
    status: Literal["connected", "disconnected", "reconnected"] = Field(
        ..., description="Connection status"
    )
    connection_id: Optional[str] = Field(None, description="Connection ID")
    heartbeat_interval: Optional[int] = Field(None, description="Heartbeat interval in seconds")


class ErrorMessage(BaseModel):
    """Error message model."""

    type: Literal["error"] = "error"
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Error details")
