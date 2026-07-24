"""Stub — implementation pending migration from services/action_service."""

from typing import Any


class ActionService:
    """Orchestrates tool/action execution for the AI agent."""

    async def execute(self, action_name: str, args: dict[str, Any], user_id: str) -> Any:
        """Execute a named action."""
        return None  # TODO: migrate implementation


action_service = ActionService()
