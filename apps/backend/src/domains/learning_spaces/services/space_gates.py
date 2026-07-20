"""Stub — implementation pending migration from services/space_gates."""

from enum import StrEnum
from typing import Any


class SpaceFeature(StrEnum):
    """Features that can be gated per space."""
    AI_CHAT = "ai_chat"
    KB_UPLOAD = "kb_upload"
    MEMBER_INVITE = "member_invite"
    GROUP_CREATE = "group_create"


class SpaceGateState(StrEnum):
    """Gate states."""
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UPGRADE = "upgrade"


class SpaceGateError(Exception):
    """Raised when a space gate check fails."""

    def __init__(self, feature: str, state: str, message: str = ""):
        self.feature = feature
        self.state = state
        super().__init__(message or f"Feature '{feature}' is {state}")


async def gate(space_id: str, feature: SpaceFeature, **kwargs) -> SpaceGateState:
    """Check if a feature is allowed for the given space."""
    return SpaceGateState.ALLOWED  # TODO: migrate implementation
