"""
Provider-neutral skill and tool type definitions.

These types form the canonical representation of tools/skills in Maigie,
independent of any LLM provider's format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


class SkillCategory(str, Enum):
    """Categories for grouping skills."""

    QUERY = "query"
    ACTION = "action"
    AGENTIC = "agentic"
    STUDY = "study"


@dataclass
class ToolParam:
    """A single parameter for a tool."""

    name: str
    type: str  # "string", "number", "boolean", "integer", "array", "object"
    description: str
    required: bool = False
    enum: list[str] | None = None
    items: dict[str, Any] | None = None  # For array types
    properties: dict[str, Any] | None = None  # For object types


@dataclass
class ToolDefinition:
    """Provider-neutral tool definition.

    This is the canonical format. Provider adapters convert FROM this format
    to their native format (Gemini functionDeclarations, OpenAI functions, etc.)
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    # JSON Schema "properties" object — same format used today in gemini_tools.py
    # but without the Gemini-specific wrapper


@dataclass
class Skill:
    """A skill groups related tools with metadata.

    Skills are the unit of modularity — each skill can be independently
    enabled/disabled, tested, and documented.
    """

    name: str
    display_name: str
    description: str
    category: SkillCategory
    tools: list[ToolDefinition] = field(default_factory=list)
    # Keywords that help route user messages to this skill
    triggers: list[str] = field(default_factory=list)
    # Whether this skill is always loaded or conditionally activated
    always_active: bool = True
    # Whether this skill supports progress callbacks
    supports_progress: bool = False
    # Tool names within this skill that support progress callbacks
    progress_tools: list[str] = field(default_factory=list)


# Type alias for tool handler functions
ToolHandler = Callable[
    [dict[str, Any], str, dict[str, Any] | None],
    Coroutine[Any, Any, dict[str, Any]],
]

ToolHandlerWithProgress = Callable[
    [dict[str, Any], str, dict[str, Any] | None, Any],
    Coroutine[Any, Any, dict[str, Any]],
]
