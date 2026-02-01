"""
Gemini tool definitions for function calling.
Defines all available tools (queries and actions) as Gemini function schemas.
"""

from typing import Any


def get_all_tools() -> list[dict[str, Any]]:
    """Return all tool definitions for Gemini."""
    return [
        {
            "function_declarations": [
                # Query tools
                get_user_courses_tool(),
                get_user_goals_tool(),
                get_user_schedule_tool(),
                get_user_notes_tool(),
                get_user_resources_tool(),
                # Action tools
                create_course_tool(),
                create_note_tool(),
                create_goal_tool(),
                create_schedule_tool(),
                recommend_resources_tool(),
                retake_note_tool(),
                add_summary_to_note_tool(),
                add_tags_to_note_tool(),
            ]
        }
    ]


def get_user_courses_tool() -> dict[str, Any]:
    """Tool definition for querying user courses."""
    return {
        "name": "get_user_courses",
        "description": "Get the user's courses with progress information. Use this when the user asks about their courses, what they're learning, or wants to see their course list.",
        "parameters": {
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
    }


def get_user_goals_tool() -> dict[str, Any]:
    """Tool definition for querying user goals."""
    return {
        "name": "get_user_goals",
        "description": "Get the user's learning goals. Use this when the user asks about their goals, objectives, targets, or what they're working towards.",
        "parameters": {
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
    }


def get_user_schedule_tool() -> dict[str, Any]:
    """Tool definition for querying user schedule."""
    return {
        "name": "get_user_schedule",
        "description": "Get the user's schedule blocks (study sessions, calendar events). Use this when the user asks about their schedule, calendar, upcoming events, or what's planned.",
        "parameters": {
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
    }


def get_user_notes_tool() -> dict[str, Any]:
    """Tool definition for querying user notes."""
    return {
        "name": "get_user_notes",
        "description": "Get the user's notes. Use this when the user asks about their notes, writings, or documentation they've created.",
        "parameters": {
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
    }


def get_user_resources_tool() -> dict[str, Any]:
    """Tool definition for querying user saved resources."""
    return {
        "name": "get_user_resources",
        "description": "Get the user's saved resources (links, videos, articles they've saved). Use this when the user asks about their saved resources, bookmarks, or materials they've collected. DO NOT use this for finding NEW resources - use recommend_resources action instead.",
        "parameters": {
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
                    "description": "Optional: Filter by resource type (VIDEO, ARTICLE, BOOK, COURSE, DOCUMENT, WEBSITE, PODCAST, OTHER)",
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
    }


def create_course_tool() -> dict[str, Any]:
    """Tool definition for creating a course."""
    return {
        "name": "create_course",
        "description": "Create a new learning course with modules and topics. IMPORTANT: Before using this tool, ALWAYS first call get_user_courses to check if the user already has a course on this topic. Only create a new course if no similar course exists. When creating, always provide a structured course outline with modules and topics.",
        "parameters": {
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
                    "description": "Array of course modules with topics. REQUIRED: Always provide modules when creating a course. Structure the course into logical learning modules (typically 4-6 modules), each with 3-6 topics. Think about the learning progression and how to break down the topic.",
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
            },
            "required": ["title", "modules"],
        },
    }


def create_note_tool() -> dict[str, Any]:
    """Tool definition for creating a note."""
    return {
        "name": "create_note",
        "description": "Create a note for a topic. Use this when the user asks to add, create, or write a note.",
        "parameters": {
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
    }


def create_goal_tool() -> dict[str, Any]:
    """Tool definition for creating a goal."""
    return {
        "name": "create_goal",
        "description": "Create a learning goal. Use this when the user asks to set, create, or establish a goal. IMPORTANT: When creating a goal related to a topic, ALWAYS first call get_user_courses to find an existing course and use its course_id.",
        "parameters": {
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
            },
            "required": ["title"],
        },
    }


def create_schedule_tool() -> dict[str, Any]:
    """Tool definition for creating schedule blocks."""
    return {
        "name": "create_schedule",
        "description": "Create one or more schedule blocks (study sessions). Use this when the user asks to schedule, plan, block out time, or create study sessions. IMPORTANT: When creating study schedules for a topic, ALWAYS first call get_user_courses to find an existing course on that topic and use its course_id. For multiple time blocks, call this function multiple times.",
        "parameters": {
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
    }


def recommend_resources_tool() -> dict[str, Any]:
    """Tool definition for recommending resources."""
    return {
        "name": "recommend_resources",
        "description": "Find and recommend NEW educational resources (videos, articles, courses, etc.) using web search. Use this when the user asks to find, search, recommend, or suggest NEW resources. DO NOT use this for showing saved resources - use get_user_resources query instead.",
        "parameters": {
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
            },
            "required": ["query"],
        },
    }


def retake_note_tool() -> dict[str, Any]:
    """Tool definition for retaking/rewriting a note."""
    return {
        "name": "retake_note",
        "description": "Rewrite or improve an existing note with better formatting. Use this when the user asks to retake, rewrite, improve, or regenerate a note.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Note ID from context",
                },
            },
            "required": ["note_id"],
        },
    }


def add_summary_to_note_tool() -> dict[str, Any]:
    """Tool definition for adding summary to a note."""
    return {
        "name": "add_summary_to_note",
        "description": "Add an AI-generated summary to an existing note. Use this when the user asks to summarize, add summary, or create a summary for a note.",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Note ID from context",
                },
            },
            "required": ["note_id"],
        },
    }


def add_tags_to_note_tool() -> dict[str, Any]:
    """Tool definition for adding tags to a note."""
    return {
        "name": "add_tags_to_note",
        "description": "Add tags to an existing note. Use this when the user asks to tag, add tags, or suggest tags for a note.",
        "parameters": {
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
    }
