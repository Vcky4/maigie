"""Stub — implementation pending migration from services/usage_tracking_service."""

from typing import Any

PERSONAL_USAGE_SCOPE = "personal"


def build_circle_usage_scope(circle_id: str) -> str:
    """Build a usage scope string for a circle/space."""
    return f"circle:{circle_id}"


async def emit_ai_usage(scope: str, **kwargs) -> None:
    """Emit AI usage event for tracking."""
    pass  # TODO: migrate implementation
