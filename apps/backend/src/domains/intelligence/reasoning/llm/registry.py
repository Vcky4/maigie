"""
Logical LLM tasks and default Gemini model IDs.

Centralizes model strings so they are not scattered across services.
"""

from __future__ import annotations

from enum import StrEnum

from src.config import get_settings


class LlmTask(StrEnum):
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
    # The two ends of the model-quality paywall (Decision P), for the 26 generation call sites that
    # go through `personal_learning.services.llm_resilient` rather than through `router`.
    #
    # These exist so that `llm_resilient` can name a tier's model without holding a model id of its
    # own. `router` gets its models from `FALLBACK_CHAT_*` filtered by `LLM_TIER_ALLOWLIST_*`;
    # `llm_resilient` has no chain and no allowlist, so it needs somewhere to ask, and the answer
    # belongs in the module that already owns every other model string. A second literal
    # `"gemini-3.5-flash"` elsewhere in the tree is how the rate card came to be wrong in two tables
    # at once.
    GENERATION_PREMIUM = "generation_premium"
    GENERATION_STANDARD = "generation_standard"


_DEFAULTS: dict[LlmTask, str] = {
    LlmTask.CHAT_DEFAULT: "gemini-3.5-flash",
    LlmTask.CHAT_TOOLS_SESSION: "gemini-3.5-flash",
    LlmTask.CHAT_TOOLS_USAGE_FALLBACK: "gemini-3.5-flash",
    LlmTask.FACT_EXTRACTION_LITE: "gemini-3.1-flash-lite",
    LlmTask.MINIMAL_RESPONSE: "gemini-3.1-flash-lite",
    LlmTask.COURSE_OUTLINE: "gemini-3.5-flash",
    LlmTask.STRUCTURED_COMPLETION: "gemini-3.5-flash",
    LlmTask.MEMORY_JSON: "gemini-3.1-flash-lite",
    LlmTask.EMBEDDING: "gemini-embedding-001",
    LlmTask.EMAIL_PRIMARY: "gemini-3.5-flash",
    LlmTask.EMAIL_FALLBACK: "gemini-3.1-flash-lite",
    LlmTask.VOICE_TRANSCRIPTION: "gemini-3.5-flash",
    # Decision P. The premium end is the same model Plus chat already runs, and the standard end is
    # the same one Free chat already runs — deliberately the same two models rather than a third
    # pair, so "Plus gets the better model" means one thing across chat and generation.
    #
    # **The standard end is `gemini-3.1-flash-lite`, not `gemini-3.5-flash-lite`**, and §6.10 of
    # MAIGIE_PLUS_COMMERCIAL_PLAN.md says otherwise. §6.10 is wrong on the arithmetic: `3.5-flash-lite`
    # is $0.30/$2.50 against `3.1-flash-lite`'s $0.25/$1.50, so it is the *dearer* of the two. It is
    # Free's chat *fallback* for that reason, and Decision P names `3.1-flash-lite` as the primary.
    # Picking the fallback as the standard model would raise the cost of every below-threshold
    # operation by ~60% on output to no end.
    LlmTask.GENERATION_PREMIUM: "gemini-3.5-flash",
    LlmTask.GENERATION_STANDARD: "gemini-3.1-flash-lite",
}


def default_model_for(task: LlmTask) -> str:
    """Return the default Gemini model id for a logical task."""
    return _DEFAULTS[task]


def gemini_api_key() -> str:
    """Gemini API key from application settings."""
    return (get_settings().GEMINI_API_KEY or "").strip()
