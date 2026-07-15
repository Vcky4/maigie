"""
Goal Management Skill — create and track learning goals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="goal_management",
    display_name="Goal Management",
    description="Create and track learning goals and objectives.",
    category=SkillCategory.ACTION,
    triggers=["goal", "goals", "objective", "target", "milestone", "achieve"],
    always_active=True,
    tools=[
        ToolDefinition(
            name="get_user_goals",
            description=(
                "Get the user's learning goals. Use this when the user asks about their "
                "goals, objectives, targets, or what they're working towards."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by goal status: ACTIVE, COMPLETED, or ARCHIVED (default: ACTIVE)",
                        "enum": ["ACTIVE", "COMPLETED", "ARCHIVED"],
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum number of goals to return (default: 20)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Optional: Filter goals for a specific course ID",
                    },
                },
            },
        ),
        ToolDefinition(
            name="create_goal",
            description=(
                "Create a learning goal. Use this when the user asks to set, create, or "
                "establish a goal. IMPORTANT: When creating a goal related to a topic, "
                "ALWAYS first call get_user_courses to find an existing course and use its course_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Goal title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Goal description (optional)",
                    },
                    "target_date": {
                        "type": "string",
                        "description": "Target completion date in ISO format (YYYY-MM-DDTHH:MM:SSZ) (optional)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Course ID from context (optional)",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Topic ID from context (optional)",
                    },
                    "circle_id": {
                        "type": "string",
                        "description": (
                            "Circle ID from context — include this when operating within a "
                            "circle chat to scope the goal to the circle (optional)"
                        ),
                    },
                },
                "required": ["title"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the goal management skill."""
    from src.services.skills.handlers import (
        handle_create_goal,
        handle_get_user_goals,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_user_goals": handle_get_user_goals,
            "create_goal": handle_create_goal,
        },
    )
