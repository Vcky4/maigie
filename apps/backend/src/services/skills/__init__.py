"""
Maigie Skill Registry — provider-agnostic tool/skill system.

Skills are modular, composable capabilities that the AI agent can invoke.
Each skill defines its tools, handlers, and metadata independently of any
specific LLM provider (Gemini, OpenAI, Anthropic, etc.).

Usage:
    from src.services.skills import skill_registry

    # Get all tool definitions (provider-neutral format)
    all_tools = skill_registry.get_all_tool_definitions()

    # Get tools for specific skill categories
    query_tools = skill_registry.get_tools_by_category("query")

    # Execute a tool call
    result = await skill_registry.execute_tool("get_user_courses", args, user_id, context)
"""

from src.services.skills.registry import SkillRegistry, skill_registry

__all__ = ["SkillRegistry", "skill_registry"]
