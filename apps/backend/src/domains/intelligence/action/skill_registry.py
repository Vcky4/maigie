"""
Skill Registry â€” registers and dispatches AI skills (tools).

Skills are actions that Intelligence can take to modify the learning
environment: create courses, schedule blocks, generate notes, etc.

The registry maps skill names to handler functions. The reasoning layer
invokes skills when the LLM requests tool calls.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Re-export from existing skill system during migration
def get_skill_registry():
    """Get the skill registry (handlers + metadata).

    During migration, delegates to existing src/services/skills/ system.
    """
    from src.domains.intelligence.action.skills.registry import get_registry

    return get_registry()


async def execute_skill(
    *,
    skill_name: str,
    arguments: dict[str, Any],
    user_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Execute a skill by name with given arguments.

    This is called by the reasoning layer when the LLM requests a tool call.
    """
    from src.domains.intelligence.action.skills.handlers import handle_skill_call

    return await handle_skill_call(
        skill_name=skill_name,
        arguments=arguments,
        user_id=user_id,
        session_id=session_id,
    )
