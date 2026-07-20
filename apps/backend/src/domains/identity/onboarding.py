"""Stub — implementation pending migration from services/onboarding_service."""

from typing import Any


async def get_onboarding_state(user_id: str) -> dict[str, Any] | None:
    """Get the current onboarding state for a user."""
    return None  # TODO: migrate implementation


async def save_onboarding_state(user_id: str, state: dict[str, Any]) -> None:
    """Save the onboarding state for a user."""
    pass  # TODO: migrate implementation


async def ensure_onboarding_initialized(user_id: str) -> dict[str, Any]:
    """Ensure onboarding is initialized for a user."""
    return {}  # TODO: migrate implementation


async def handle_onboarding_message(user_id: str, message: str, **kwargs) -> dict[str, Any]:
    """Handle a message during onboarding flow."""
    return {}  # TODO: migrate implementation
