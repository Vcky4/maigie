"""
Skill Registry — central registry for all Maigie skills.

Provides:
- Registration of skills and their tool handlers
- Tool definition retrieval in provider-neutral format
- Tool execution dispatch
- Category-based and trigger-based filtering
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for all Maigie skills and their tools."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._handlers: dict[str, Any] = {}  # tool_name -> handler function
        self._tool_to_skill: dict[str, str] = {}  # tool_name -> skill_name
        self._initialized = False

    def register_skill(self, skill: Skill, handlers: dict[str, Any] | None = None) -> None:
        """Register a skill and its tool handlers.

        Args:
            skill: The skill definition with tools
            handlers: Dict mapping tool_name -> async handler function
        """
        if skill.name in self._skills:
            logger.warning(f"Skill '{skill.name}' already registered, overwriting.")

        self._skills[skill.name] = skill

        for tool in skill.tools:
            self._tool_to_skill[tool.name] = skill.name

        if handlers:
            for tool_name, handler in handlers.items():
                self._handlers[tool_name] = handler

    def register_handler(self, tool_name: str, handler: Any) -> None:
        """Register a handler for a specific tool."""
        self._handlers[tool_name] = handler

    # ─── Retrieval ────────────────────────────────────────────────────────

    def get_all_skills(self) -> list[Skill]:
        """Get all registered skills."""
        self._ensure_initialized()
        return list(self._skills.values())

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        self._ensure_initialized()
        return self._skills.get(name)

    def get_skills_by_category(self, category: SkillCategory | str) -> list[Skill]:
        """Get all skills in a category."""
        self._ensure_initialized()
        if isinstance(category, str):
            category = SkillCategory(category)
        return [s for s in self._skills.values() if s.category == category]

    def get_all_tool_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions across all active skills."""
        self._ensure_initialized()
        tools = []
        for skill in self._skills.values():
            if skill.always_active:
                tools.extend(skill.tools)
        return tools

    def get_tools_by_category(self, category: SkillCategory | str) -> list[ToolDefinition]:
        """Get tool definitions for a specific category."""
        self._ensure_initialized()
        if isinstance(category, str):
            category = SkillCategory(category)
        tools = []
        for skill in self._skills.values():
            if skill.category == category:
                tools.extend(skill.tools)
        return tools

    def get_tools_by_skill(self, skill_name: str) -> list[ToolDefinition]:
        """Get tool definitions for a specific skill."""
        self._ensure_initialized()
        skill = self._skills.get(skill_name)
        if skill:
            return skill.tools
        return []

    def get_study_tools(self) -> list[ToolDefinition]:
        """Get tools specifically for study/voice mode (minimal set)."""
        self._ensure_initialized()
        tools = []
        for skill in self._skills.values():
            if skill.category == SkillCategory.STUDY:
                tools.extend(skill.tools)
        return tools

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names."""
        self._ensure_initialized()
        return list(self._tool_to_skill.keys())

    def get_skill_for_tool(self, tool_name: str) -> str | None:
        """Get the skill name that owns a tool."""
        self._ensure_initialized()
        return self._tool_to_skill.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        self._ensure_initialized()
        return tool_name in self._tool_to_skill

    # ─── Execution ────────────────────────────────────────────────────────

    async def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        user_id: str,
        context: dict[str, Any] | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments from the LLM
            user_id: Current user ID
            context: WebSocket context (courseId, topicId, etc.)
            progress_callback: Optional progress callback for long-running tools

        Returns:
            Tool execution result dict
        """
        self._ensure_initialized()

        handler = self._handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}

        # Enrich args with context IDs
        if context:
            _enrich_args_from_context(args, context)

        try:
            # Check if this tool supports progress callbacks
            skill_name = self._tool_to_skill.get(tool_name)
            skill = self._skills.get(skill_name) if skill_name else None
            supports_progress = (
                skill is not None
                and tool_name in skill.progress_tools
                and progress_callback is not None
            )

            if supports_progress:
                result = await handler(args, user_id, context, progress_callback=progress_callback)
            else:
                result = await handler(args, user_id, context)
            return result
        except Exception as e:
            logger.error(f"Tool execution error for {tool_name}: {e}", exc_info=True)
            return {
                "error": str(e),
                "error_type": type(e).__name__,
            }

    # ─── Legacy Bridge ────────────────────────────────────────────────────

    def get_all_tools_legacy_format(self) -> list[dict[str, Any]]:
        """Return tools in the function_declarations dict format.

        Returns [{"function_declarations": [...]}] for adapter consumption.
        """
        self._ensure_initialized()
        declarations = []
        for tool_def in self.get_all_tool_definitions():
            declarations.append(_to_legacy_declaration(tool_def))
        return [{"function_declarations": declarations}]

    def get_study_tools_legacy_format(self) -> list[dict[str, Any]]:
        """Return study tools in function_declarations format for voice mode."""
        self._ensure_initialized()
        declarations = []
        for tool_def in self.get_study_tools():
            declarations.append(_to_legacy_declaration(tool_def))
        return [{"function_declarations": declarations}]

    # ─── Trigger Matching ─────────────────────────────────────────────────

    def match_skills_by_triggers(self, message: str) -> list[Skill]:
        """Find skills whose triggers match the user message.

        Useful for selective tool loading to reduce token usage.
        """
        self._ensure_initialized()
        message_lower = message.lower()
        matched = []
        for skill in self._skills.values():
            for trigger in skill.triggers:
                if trigger in message_lower:
                    matched.append(skill)
                    break
        return matched

    # ─── Initialization ───────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """Lazy-load all skills on first access."""
        if not self._initialized:
            self._load_all_skills()
            self._initialized = True

    def _load_all_skills(self) -> None:
        """Import and register all skill modules."""
        from src.services.skills.skill_courses import register as register_courses
        from src.services.skills.skill_goals import register as register_goals
        from src.services.skills.skill_memory import register as register_memory
        from src.services.skills.skill_notes import register as register_notes
        from src.services.skills.skill_planning import register as register_planning
        from src.services.skills.skill_resources import register as register_resources
        from src.services.skills.skill_scheduling import register as register_scheduling
        from src.services.skills.skill_study_mode import register as register_study_mode

        register_courses(self)
        register_notes(self)
        register_goals(self)
        register_scheduling(self)
        register_resources(self)
        register_memory(self)
        register_planning(self)
        register_study_mode(self)

        logger.info(
            f"Skill registry initialized: {len(self._skills)} skills, "
            f"{len(self._tool_to_skill)} tools"
        )


# ─── Module-level singleton ──────────────────────────────────────────────────

skill_registry = SkillRegistry()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _enrich_args_from_context(args: dict[str, Any], context: dict[str, Any]) -> None:
    """Merge context IDs into tool args."""
    _CONTEXT_MAPPINGS = [
        ("courseId", "course_id"),
        ("topicId", "topic_id"),
        ("noteId", "note_id"),
        ("reviewItemId", "review_item_id"),
        ("circleId", "circle_id"),
        ("scheduleId", "schedule_id"),
    ]
    for context_key, arg_key in _CONTEXT_MAPPINGS:
        if context_key in context and arg_key not in args:
            args[arg_key] = context[context_key]


def _to_legacy_declaration(tool_def: ToolDefinition) -> dict[str, Any]:
    """Convert a ToolDefinition to the legacy Gemini dict format."""
    decl: dict[str, Any] = {
        "name": tool_def.name,
        "description": tool_def.description,
    }
    if tool_def.parameters:
        decl["parameters"] = tool_def.parameters
    else:
        decl["parameters"] = {"type": "object", "properties": {}}
    return decl
