"""
Planning & Insights Skill — study plans, learning insights, and nudges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="planning_insights",
    display_name="Planning & Insights",
    description="Create study plans, analyze learning patterns, and provide proactive suggestions.",
    category=SkillCategory.AGENTIC,
    triggers=[
        "study plan",
        "plan",
        "prepare",
        "exam",
        "insights",
        "patterns",
        "habits",
        "suggestions",
        "nudge",
        "what should i",
    ],
    always_active=True,
    supports_progress=True,
    progress_tools=["create_study_plan"],
    tools=[
        ToolDefinition(
            name="create_study_plan",
            description=(
                "Create a comprehensive multi-step study plan for the user. This will decompose "
                "a study goal into a course (with modules/topics), milestones, goals, and "
                "scheduled study sessions distributed over the specified time period. "
                "Use this when the user asks you to create a study plan, prepare for an exam, "
                "or help them plan their learning over a period of time. "
                "This is a powerful tool that creates MULTIPLE entities (course + goals + schedules) "
                "in one step."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "The study goal or what the user wants to accomplish",
                    },
                    "duration_weeks": {
                        "type": "integer",
                        "description": "Duration of the plan in weeks (default: 4, range: 1-16)",
                    },
                },
                "required": ["goal"],
            },
        ),
        ToolDefinition(
            name="get_learning_insights",
            description=(
                "Retrieve the AI's accumulated knowledge about the user's learning patterns, "
                "strengths, weaknesses, optimal study times, and strategy effectiveness. "
                "Use this when the user asks about their study habits, learning patterns, "
                "what's working, where they're struggling, or when you need behavioral "
                "context to give personalized advice."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
        ),
        ToolDefinition(
            name="get_pending_nudges",
            description=(
                "Retrieve proactive suggestions and reminders that the AI has queued for the user. "
                "These are things like goal deadline reminders, study streak warnings, and review "
                "due notifications. Use this when the user asks 'what should I do?', 'any suggestions?', "
                "or when starting a greeting to check if there are urgent items."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of nudges to return (default: 5)",
                    },
                },
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the planning & insights skill."""
    from src.services.skills.handlers import (
        handle_create_study_plan,
        handle_get_learning_insights,
        handle_get_pending_nudges,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "create_study_plan": handle_create_study_plan,
            "get_learning_insights": handle_get_learning_insights,
            "get_pending_nudges": handle_get_pending_nudges,
        },
    )
