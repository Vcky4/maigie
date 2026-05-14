"""
Memory & Personalization Skill — profile, facts, reviews, and communication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="memory_personalization",
    display_name="Memory & Personalization",
    description="Manage user profile, remember facts, handle reviews, and send emails.",
    category=SkillCategory.QUERY,
    triggers=[
        "profile",
        "who am i",
        "about me",
        "remember",
        "fact",
        "review",
        "email",
        "send",
        "streak",
    ],
    always_active=True,
    tools=[
        ToolDefinition(
            name="get_my_profile",
            description=(
                "Get the user's full profile including their name, study statistics, "
                "course summary, active goals, study streak, upcoming schedule, and "
                "remembered facts about them. Use this when the user asks 'who am I?', "
                "'what do you know about me?', anything about their profile, progress, "
                "or when you need personal context to give a better answer."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
        ),
        ToolDefinition(
            name="save_user_fact",
            description=(
                "Save an important fact the user has shared about themselves for future reference. "
                "Use this when the user tells you something personal that would help Maigie support "
                "them better — like their learning style, exam dates, academic background, struggles, "
                "strengths, or personal preferences. Do NOT save trivial facts or things already "
                "tracked elsewhere (like course names or goals)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category of the fact",
                        "enum": [
                            "preference",
                            "personal",
                            "academic",
                            "goal",
                            "struggle",
                            "strength",
                            "other",
                        ],
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The fact to remember, written as a clear statement. "
                            "E.g. 'Prefers visual learning with diagrams', "
                            "'Is preparing for the bar exam in June 2026'"
                        ),
                    },
                },
                "required": ["category", "content"],
            },
        ),
        ToolDefinition(
            name="complete_review",
            description=(
                "Mark the current spaced-repetition review as completed with a quality rating. "
                "Call this ONLY when the user has finished answering all quiz questions in the "
                "review flow. The review_item_id comes from context when the user is in review mode. "
                "You MUST provide a quality rating based on the user's performance."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "review_item_id": {
                        "type": "string",
                        "description": "Review item ID from context (required when in review mode)",
                    },
                    "quality": {
                        "type": "integer",
                        "description": (
                            "Quality of recall, 0-5 scale: "
                            "0=complete blackout, 1=incorrect but recognised, "
                            "2=incorrect but seemed easy, 3=correct with difficulty, "
                            "4=correct with minor hesitation, 5=perfect instant recall."
                        ),
                    },
                    "score_summary": {
                        "type": "string",
                        "description": "Brief summary of user's performance",
                    },
                },
                "required": ["quality"],
            },
        ),
        ToolDefinition(
            name="email_user",
            description=(
                "Send a personalized email to the user. Use this when the user asks to email "
                "them something (e.g., their schedule, a summary, or a direct message)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "content": {
                        "type": "string",
                        "description": "Email body content in Markdown or HTML format",
                    },
                },
                "required": ["subject", "content"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the memory & personalization skill."""
    from src.services.gemini_tool_handlers import (
        handle_complete_review,
        handle_email_user,
        handle_get_my_profile,
        handle_save_user_fact,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_my_profile": handle_get_my_profile,
            "save_user_fact": handle_save_user_fact,
            "complete_review": handle_complete_review,
            "email_user": handle_email_user,
        },
    )
