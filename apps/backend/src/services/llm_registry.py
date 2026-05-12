"""
Logical LLM tasks and default Gemini model IDs.

Centralizes model strings that were previously scattered across services.
Future OpenAI/Anthropic adapters will map LlmTask (or explicit model picks) to provider-specific IDs.

See: docs/MULTI_PROVIDER_LLM_PLAN.md
"""

from __future__ import annotations

from enum import StrEnum

from src.config import get_settings


class LlmTask(StrEnum):
    """Stable task names for defaults, config overrides, and future routing."""

    CHAT_DEFAULT = "chat_default"
    CHAT_TOOLS_SESSION = "chat_tools_session"
    CHAT_TOOLS_USAGE_FALLBACK = "chat_tools_usage_fallback"
    FACT_EXTRACTION_LITE = "fact_extraction_lite"
    MINIMAL_RESPONSE = "minimal_response"
    COURSE_OUTLINE = "course_outline"
    STRUCTURED_COMPLETION = "structured_completion"
    MEMORY_JSON = "memory_json"
    EMBEDDING = "embedding"
    EMAIL_PRIMARY = "email_primary"
    EMAIL_FALLBACK = "email_fallback"
    VOICE_TRANSCRIPTION = "voice_transcription"


_DEFAULTS: dict[LlmTask, str] = {
    LlmTask.CHAT_DEFAULT: "gemini-2.5-flash",
    LlmTask.CHAT_TOOLS_SESSION: "gemini-2.5-flash",
    LlmTask.CHAT_TOOLS_USAGE_FALLBACK: "gemini-3-flash-preview",
    LlmTask.FACT_EXTRACTION_LITE: "gemini-2.0-flash-lite",
    LlmTask.MINIMAL_RESPONSE: "gemini-2.0-flash-lite",
    LlmTask.COURSE_OUTLINE: "gemini-2.0-flash",
    LlmTask.STRUCTURED_COMPLETION: "gemini-2.0-flash",
    LlmTask.MEMORY_JSON: "gemini-2.0-flash-lite",
    LlmTask.EMBEDDING: "gemini-embedding-001",
    LlmTask.EMAIL_PRIMARY: "gemini-2.5-flash",
    LlmTask.EMAIL_FALLBACK: "gemini-2.0-flash-lite",
    LlmTask.VOICE_TRANSCRIPTION: "gemini-3-flash-preview",
}


def default_model_for(task: LlmTask) -> str:
    """Return the default Gemini model id for a logical task."""
    return _DEFAULTS[task]


def gemini_api_key() -> str:
    """Gemini API key from application settings (trimmed; may be empty in dev)."""
    return (get_settings().GEMINI_API_KEY or "").strip()
