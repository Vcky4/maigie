"""
Note Taking Skill — create, view, summarize, and tag notes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="note_taking",
    display_name="Note Taking",
    description="Create, view, rewrite, summarize, and tag study notes.",
    category=SkillCategory.ACTION,
    triggers=["note", "notes", "write", "summary", "summarize", "tag", "retake"],
    always_active=True,
    tools=[
        ToolDefinition(
            name="get_user_notes",
            description=(
                "Get the user's notes. Use this when the user asks about their notes, "
                "writings, or documentation they've created."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "number",
                        "description": "Maximum number of notes to return (default: 20)",
                    },
                    "include_archived": {
                        "type": "boolean",
                        "description": "Whether to include archived notes (default: false)",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Optional: Filter notes for a specific topic ID",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Optional: Filter notes for a specific course ID",
                    },
                },
            },
        ),
        ToolDefinition(
            name="create_note",
            description=(
                "Create a note for a topic. Use this when the user asks to add, create, "
                "or write a note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Note title",
                    },
                    "content": {
                        "type": "string",
                        "description": "Note content in markdown format",
                    },
                    "topic_id": {
                        "type": "string",
                        "description": "Topic ID from context (required if available)",
                    },
                    "course_id": {
                        "type": "string",
                        "description": "Course ID from context (optional)",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary (optional)",
                    },
                },
                "required": ["title", "content"],
            },
        ),
        ToolDefinition(
            name="retake_note",
            description=(
                "Rewrite or improve an existing note with better formatting. Use this when "
                "the user asks to retake, rewrite, improve, or regenerate a note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "string",
                        "description": "Note ID from context",
                    },
                },
                "required": ["note_id"],
            },
        ),
        ToolDefinition(
            name="add_summary_to_note",
            description=(
                "Add an AI-generated summary to an existing note. Use this when the user "
                "asks to summarize, add summary, or create a summary for a note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "string",
                        "description": "Note ID from context",
                    },
                },
                "required": ["note_id"],
            },
        ),
        ToolDefinition(
            name="add_tags_to_note",
            description=(
                "Add tags to an existing note. Use this when the user asks to tag, add tags, "
                "or suggest tags for a note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "string",
                        "description": "Note ID from context",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of tags to add (3-8 tags recommended, use PascalCase or camelCase)",
                    },
                },
                "required": ["note_id", "tags"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the note taking skill."""
    from src.services.gemini_tool_handlers import (
        handle_add_summary_to_note,
        handle_add_tags_to_note,
        handle_create_note,
        handle_get_user_notes,
        handle_retake_note,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_user_notes": handle_get_user_notes,
            "create_note": handle_create_note,
            "retake_note": handle_retake_note,
            "add_summary_to_note": handle_add_summary_to_note,
            "add_tags_to_note": handle_add_tags_to_note,
        },
    )
