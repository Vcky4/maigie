"""Stub — implementation pending migration from services/ws_event_bus."""

from typing import Any


async def publish_ws_event(
    user_id: str | None = None,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
    **kwargs,
) -> None:
    """Publish an event via WebSocket event bus.

    Args:
        user_id: Target user for the event.
        event_type: Optional event type category.
        payload: Event payload data.
    """
    pass  # TODO: migrate implementation
