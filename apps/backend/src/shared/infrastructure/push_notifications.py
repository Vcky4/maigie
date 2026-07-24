"""Stub — implementation pending migration from services/push_notification_service."""

from typing import Any


async def send_push_notification(
    user_id: str, title: str, body: str, data: dict[str, Any] | None = None
) -> None:
    """Send a push notification to a user."""
    pass  # TODO: migrate implementation


async def send_push_to_user(
    user_id: str, title: str, body: str, data: dict[str, Any] | None = None
) -> None:
    """Send a push notification to a user (alias)."""
    await send_push_notification(user_id, title, body, data)
