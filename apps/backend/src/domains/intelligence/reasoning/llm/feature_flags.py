"""Stub — implementation pending migration from services/llm/feature_flags."""

from typing import Any

PERSONAL_SCOPE = "personal"


def circle_scope(circle_id: str) -> str:
    """Return a scope string for a circle/space."""
    return f"circle:{circle_id}"


class FeatureFlagService:
    """Stub feature flag service."""

    async def is_enabled(self, flag: str, scope: str = PERSONAL_SCOPE) -> bool:
        return True  # TODO: migrate implementation

    async def get_variant(self, flag: str, scope: str = PERSONAL_SCOPE) -> str | None:
        return None  # TODO: migrate implementation
