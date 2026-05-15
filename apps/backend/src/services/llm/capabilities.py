"""
Capability protocol interfaces for multi-provider LLM support.

Defines focused, runtime-checkable Protocol classes that providers can implement
to declare which LLM capabilities they support. The TASK_CAPABILITY_MAP links
each logical LlmTask to the capability required to fulfill it, enabling the
router to filter providers by capability at selection time.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.services.llm.types import (
    CompletionResult,
    GenerationConfig,
    Message,
    ToolDefinition,
)
from src.services.llm_registry import LlmTask


@runtime_checkable
class ChatCapability(Protocol):
    """Chat completion with optional tool calling and streaming."""

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        stream_callback: Any | None = None,
        config: GenerationConfig | None = None,
    ) -> CompletionResult: ...


@runtime_checkable
class VisionCapability(Protocol):
    """Image understanding with text prompt."""

    async def analyze_image(
        self,
        image_data: bytes | str,  # bytes or URL
        mime_type: str | None,
        prompt: str,
        config: GenerationConfig | None = None,
    ) -> CompletionResult: ...


@runtime_checkable
class StructuredOutputCapability(Protocol):
    """JSON-constrained generation."""

    async def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        config: GenerationConfig | None = None,
    ) -> CompletionResult: ...


@runtime_checkable
class EmbeddingCapability(Protocol):
    """Text embedding generation."""

    async def embed(
        self,
        texts: list[str],  # 1-100 items, each ≤10,000 chars
    ) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Task-to-Capability mapping
# ---------------------------------------------------------------------------

TASK_CAPABILITY_MAP: dict[LlmTask, type] = {
    LlmTask.CHAT_DEFAULT: ChatCapability,
    LlmTask.CHAT_TOOLS_SESSION: ChatCapability,
    LlmTask.CHAT_TOOLS_USAGE_FALLBACK: ChatCapability,
    LlmTask.FACT_EXTRACTION_LITE: ChatCapability,
    LlmTask.MINIMAL_RESPONSE: ChatCapability,
    LlmTask.COURSE_OUTLINE: ChatCapability,
    LlmTask.STRUCTURED_COMPLETION: StructuredOutputCapability,
    LlmTask.MEMORY_JSON: StructuredOutputCapability,
    LlmTask.EMBEDDING: EmbeddingCapability,
    LlmTask.EMAIL_PRIMARY: ChatCapability,
    LlmTask.EMAIL_FALLBACK: ChatCapability,
    LlmTask.VOICE_TRANSCRIPTION: ChatCapability,
}
