"""
Resource Management Skill — view saved resources and find new ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="resource_management",
    display_name="Resource Management",
    description="View saved resources and find new educational materials.",
    category=SkillCategory.ACTION,
    triggers=[
        "resource",
        "resources",
        "video",
        "article",
        "book",
        "link",
        "find",
        "recommend",
        "search",
    ],
    always_active=True,
    tools=[
        ToolDefinition(
            name="get_user_resources",
            description=(
                "Get the user's saved resources (links, videos, articles they've saved). "
                "Use this when the user asks about their saved resources, bookmarks, or "
                "materials they've collected. DO NOT use this for finding NEW resources — "
                "use recommend_resources action instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "number",
                        "description": "Maximum number of resources to return (default: 20)",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Optional: Filter resources for a specific topic ID",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Optional: Filter resources for a specific course ID",
                    },
                    "resource_type": {
                        "type": "string",
                        "description": "Optional: Filter by resource type",
                        "enum": [
                            "VIDEO",
                            "ARTICLE",
                            "BOOK",
                            "COURSE",
                            "DOCUMENT",
                            "WEBSITE",
                            "PODCAST",
                            "OTHER",
                        ],
                    },
                },
            },
        ),
        ToolDefinition(
            name="recommend_resources",
            description=(
                "Find and recommend NEW educational resources (videos, articles, courses, etc.) "
                "using web search. Use this when the user asks to find, search, recommend, or "
                "suggest NEW resources. DO NOT use this for showing saved resources — use "
                "get_user_resources query instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing what resources the user needs",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Number of resources to recommend (default: 10)",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Topic ID from context (optional)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Course ID from context (optional)",
                    },
                    "circle_id": {
                        "type": "string",
                        "description": (
                            "Circle ID from context — include this when operating within a "
                            "circle chat to scope the resources to the circle (optional)"
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the resource management skill."""
    from src.services.skills.handlers import (
        handle_get_user_resources,
        handle_recommend_resources,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_user_resources": handle_get_user_resources,
            "recommend_resources": handle_recommend_resources,
        },
    )
