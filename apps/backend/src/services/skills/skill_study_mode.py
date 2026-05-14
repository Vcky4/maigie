"""
Study Mode Skill — tools for Gemini Live voice study sessions.

This is a minimal skill set optimized for real-time voice interactions
where reliability of function calling is critical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="study_mode",
    display_name="Study Mode",
    description="Interactive study tools for voice sessions — topic navigation and visual aids.",
    category=SkillCategory.STUDY,
    triggers=["next topic", "done", "continue", "move on", "diagram", "visual", "show me"],
    always_active=False,  # Only loaded in study/voice mode
    tools=[
        ToolDefinition(
            name="complete_topic_and_continue",
            description=(
                "Mark the current study topic as completed and navigate the user to the next topic. "
                "Call this tool when the user agrees to move on to the next topic, says they are done "
                "with the current topic, or explicitly asks to continue to the next one."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
        ),
        ToolDefinition(
            name="study_show_visual",
            description=(
                "Show a diagram or written equation in the Study Mode overlay while you keep "
                "talking naturally. Call this when explaining processes, hierarchies, comparisons, "
                "timelines, or math that is clearer visually. Prefer valid Mermaid (flowchart, "
                "sequenceDiagram, mindmap, etc.). Do not read raw Mermaid line-by-line aloud; "
                "summarize the idea in speech instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "mermaid": {
                        "type": "string",
                        "description": (
                            "Mermaid source only — no markdown fences. For flowchart/graph nodes, "
                            'use quoted labels when text has parentheses. '
                            "Example: flowchart LR\\n  A-->B\\n  B-->C"
                        ),
                    },
                    "display_math": {
                        "type": "string",
                        "description": "Optional LaTeX for one display equation (no $$ delimiters).",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional one-line label shown above the visual.",
                    },
                },
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the study mode skill."""
    from src.services.gemini_tool_handlers import (
        handle_complete_topic_and_continue,
        handle_study_show_visual,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "complete_topic_and_continue": handle_complete_topic_and_continue,
            "study_show_visual": handle_study_show_visual,
        },
    )
