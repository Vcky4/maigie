"""
Provider-neutral shapes for multi-provider LLM support.

Defines dataclasses for messages, tool definitions, completions, streaming,
and generation configuration. These types form the adapter boundary between
product logic and provider-specific SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Legacy TypedDict (preserved for backward compatibility)
# ---------------------------------------------------------------------------


class ChatToolTurnResult(TypedDict, total=False):
    """One row in ``executed_actions`` / query tooling (loose schema for now)."""

    type: str
    data: dict[str, Any]
    result: dict[str, Any]


# ---------------------------------------------------------------------------
# Provider-neutral dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ContentPart:
    """A part of a multimodal message."""

    type: str  # "text", "image"
    text: str | None = None
    image_data: bytes | None = None
    mime_type: str | None = None


@dataclass
class ToolCallRequest:
    """A normalized tool call from any provider."""

    id: str  # Provider-assigned call ID
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """Provider-neutral chat message."""

    role: str  # "user", "assistant", "tool"
    content: str | list[ContentPart] = ""
    tool_call_id: str | None = None  # For tool result messages
    tool_calls: list[ToolCallRequest] | None = None  # For assistant messages with tool calls


@dataclass
class ToolDefinition:
    """Provider-neutral tool/function definition."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema object
    required: list[str] = field(default_factory=list)


@dataclass
class TokenUsage:
    """Token counts from a completion."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolCallDelta:
    """Partial tool call data from a stream chunk."""

    id: str | None = None
    name: str | None = None
    arguments_fragment: str | None = None


@dataclass
class CompletionResult:
    """Provider-neutral completion response."""

    text: str
    token_usage: TokenUsage
    model_id: str
    provider_name: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class StreamEvent:
    """A single event in a normalized stream."""

    type: str  # "text_delta", "tool_call_delta", "done"
    text: str | None = None
    tool_call: ToolCallDelta | None = None
    done: bool = False


@dataclass
class GenerationConfig:
    """Provider-neutral generation parameters."""

    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
