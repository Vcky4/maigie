"""
Scheduling Skill — create and manage study schedules and calendar events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="scheduling",
    display_name="Scheduling",
    description="Create and manage study schedules, calendar events, and time blocks.",
    category=SkillCategory.ACTION,
    triggers=[
        "schedule",
        "calendar",
        "plan",
        "session",
        "block",
        "time",
        "when",
        "tomorrow",
        "today",
        "week",
    ],
    always_active=True,
    tools=[
        ToolDefinition(
            name="get_user_schedule",
            description=(
                "Get the user's schedule blocks (study sessions, calendar events). Use this "
                "when the user asks about their schedule, calendar, upcoming events, or what's planned."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in ISO format (YYYY-MM-DD) or 'today', 'tomorrow', 'this_week' (default: today)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in ISO format or 'today', 'tomorrow', 'next_week', '+30days' (default: +30days)",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum number of schedule blocks to return (default: 50)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Optional: Filter schedule for a specific course ID",
                    },
                },
            },
        ),
        ToolDefinition(
            name="check_schedule_conflicts",
            description=(
                "Check the user's existing schedule for conflicts before proposing or creating "
                "new study blocks. ALWAYS use this tool first before calling create_schedule to "
                "ensure the time slot is truly free."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start_at": {
                        "type": "string",
                        "description": "Start time to check in ISO format (YYYY-MM-DDTHH:MM:SSZ)",
                    },
                    "end_at": {
                        "type": "string",
                        "description": "End time to check in ISO format (YYYY-MM-DDTHH:MM:SSZ)",
                    },
                },
                "required": ["start_at", "end_at"],
            },
        ),
        ToolDefinition(
            name="create_schedule",
            description=(
                "Create one or more schedule blocks (study sessions). Use this when the user "
                "asks to schedule, plan, block out time, or create study sessions. IMPORTANT: "
                "When creating study schedules for a topic, ALWAYS first call get_user_courses "
                "to find an existing course on that topic and use its course_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Schedule block title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Schedule description (optional)",
                    },
                    "start_at": {
                        "type": "string",
                        "description": "Start time in ISO format (YYYY-MM-DDTHH:MM:SSZ)",
                    },
                    "end_at": {
                        "type": "string",
                        "description": "End time in ISO format (YYYY-MM-DDTHH:MM:SSZ)",
                    },
                    "recurring_rule": {
                        "type": "string",
                        "description": "Recurring rule: DAILY, WEEKLY, or RRULE format (optional)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Course ID from context (optional)",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Topic ID from context (optional)",
                    },
                    "goal_id": {
                        "type": "string",
                        "description": "Goal ID from context (optional)",
                    },
                },
                "required": ["title", "start_at", "end_at"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the scheduling skill."""
    from src.services.gemini_tool_handlers import (
        handle_check_schedule_conflicts,
        handle_create_schedule,
        handle_get_user_schedule,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_user_schedule": handle_get_user_schedule,
            "check_schedule_conflicts": handle_check_schedule_conflicts,
            "create_schedule": handle_create_schedule,
        },
    )
