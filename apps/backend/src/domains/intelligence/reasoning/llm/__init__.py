"""
Multi-provider LLM adapters — Gemini, OpenAI, Anthropic.

Provides a clean interface for other domains to use LLM capabilities
without knowing about provider specifics.
"""

import logging
from typing import Any

from .errors import GeminiError, LLMError, LLMProviderError, LLMUnavailableError
from .gemini_sdk import new_gemini_client
from .gemini_sdk import types as gemini_types
from .registry import LlmTask, default_model_for, gemini_api_key

logger = logging.getLogger(__name__)

__all__ = [
    "generate_content",
    "new_gemini_client",
    "gemini_types",
    "LlmTask",
    "default_model_for",
    "gemini_api_key",
    "LLMError",
    "LLMProviderError",
    "LLMUnavailableError",
    "GeminiError",
]


async def generate_content(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.7) -> str:
    """Generate text content using the default LLM provider.

    This is the primary interface for domains that need simple text generation
    (topic explanations, quizzes, summaries, etc.).

    Raises:
        GeminiError: If Gemini returned no usable text (e.g. safety filter blocked
            the response, MAX_TOKENS hit during thinking phase, or RECITATION).
            The error message includes the ``finish_reason`` so callers can
            decide whether to retry or fall back.
    """
    client = new_gemini_client(gemini_api_key() or None)
    response = await client.aio.models.generate_content(
        model=default_model_for(LlmTask.CHAT_DEFAULT),
        contents=prompt,
        config=gemini_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )

    text = _extract_text(response)
    if not text:
        finish_reason = _extract_finish_reason(response)
        logger.warning(
            "Gemini returned no text (finish_reason=%s). Prompt length=%d, max_tokens=%d.",
            finish_reason,
            len(prompt),
            max_tokens,
        )
        raise GeminiError(
            f"empty response (finish_reason={finish_reason})",
        )
    return text


def _extract_text(response: Any) -> str:
    """Pull text out of a Gemini response, falling back to walking parts if needed.

    ``response.text`` is a convenience accessor that returns None whenever the
    response contains anything other than a single simple text part (thinking
    output, function calls, empty candidates). Walking ``candidates[0].content.parts``
    catches the cases where the SDK's convenience field misses valid text.
    """
    direct = getattr(response, "text", None)
    if direct:
        return direct.strip()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        fragments = [p.text for p in parts if getattr(p, "text", None)]
        joined = "".join(fragments).strip()
        if joined:
            return joined
    return ""


def _extract_finish_reason(response: Any) -> str:
    """Best-effort extraction of the first candidate's finish_reason for logging."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "no_candidates"
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return "unknown"
    # google-genai returns an enum whose name is what we want in logs.
    return getattr(reason, "name", str(reason))
