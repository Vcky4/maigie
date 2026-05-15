"""
Course Management Skill — create, view, update, and delete courses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="course_management",
    display_name="Course Management",
    description="Create, view, update, and manage learning courses with modules and topics.",
    category=SkillCategory.ACTION,
    triggers=[
        "course",
        "courses",
        "learning",
        "studying",
        "outline",
        "module",
        "topic",
        "syllabus",
        "curriculum",
    ],
    always_active=True,
    supports_progress=True,
    progress_tools=["create_course"],
    tools=[
        ToolDefinition(
            name="get_user_courses",
            description=(
                "Get the user's courses with progress information. Use this when the user "
                "asks about their courses, what they're learning, or wants to see their course list."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "number",
                        "description": "Maximum number of courses to return (default: 20)",
                    },
                    "include_archived": {
                        "type": "boolean",
                        "description": "Whether to include archived courses (default: false)",
                    },
                },
            },
        ),
        ToolDefinition(
            name="create_course",
            description=(
                "Create a new learning course with modules and topics. IMPORTANT: Before using "
                "this tool, ALWAYS first call get_user_courses to check if the user already has "
                "a course on this topic. Only create a new course if no similar course exists. "
                "When creating, always provide a structured course outline with modules and topics."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Course title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief course description",
                    },
                    "difficulty": {
                        "type": "string",
                        "description": "Difficulty level",
                        "enum": ["BEGINNER", "INTERMEDIATE", "ADVANCED"],
                    },
                    "modules": {
                        "type": "array",
                        "description": (
                            "Array of course modules with topics. REQUIRED: Always provide modules "
                            "when creating a course. Structure the course into logical learning "
                            "modules (typically 4-6 modules), each with 3-6 topics."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Module title",
                                },
                                "topics": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Array of topic titles (3-6 topics per module)",
                                },
                            },
                            "required": ["title", "topics"],
                        },
                    },
                    "circle_id": {
                        "type": "string",
                        "description": (
                            "Circle ID from context — include this when operating within a "
                            "circle chat to scope the course to the circle (optional)"
                        ),
                    },
                },
                "required": ["title", "modules"],
            },
        ),
        ToolDefinition(
            name="update_course_outline",
            description=(
                "Populate or replace the modules and topics for an EXISTING course based on "
                "an outline the user provides. Use this when the user says things like "
                "'outline for ...', 'update outline for ...', 'here is my outline', or when "
                "they paste/upload a course outline or syllabus. IMPORTANT: If the outline is "
                "just a flat list of topics, group them into logical modules (4-6 modules). "
                "Always call get_user_courses first to find the matching course_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "The ID of the existing course to update",
                    },
                    "modules": {
                        "type": "array",
                        "description": "Array of modules, each with a title and a list of topic titles.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Module title",
                                },
                                "topics": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Array of topic titles",
                                },
                            },
                            "required": ["title", "topics"],
                        },
                    },
                },
                "required": ["course_id", "modules"],
            },
        ),
        ToolDefinition(
            name="delete_course",
            description=(
                "Delete a course permanently. Use when the user asks to remove, delete, or "
                "get rid of a course. This will also delete linked goals and schedule blocks; "
                "notes will be kept."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "The ID of the course to delete (required)",
                    },
                },
                "required": ["course_id"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the course management skill."""
    from src.services.skills.handlers import (
        handle_create_course,
        handle_delete_course,
        handle_get_user_courses,
        handle_update_course_outline,
    )

    registry.register_skill(
        SKILL,
        handlers={
            "get_user_courses": handle_get_user_courses,
            "create_course": handle_create_course,
            "update_course_outline": handle_update_course_outline,
            "delete_course": handle_delete_course,
        },
    )
