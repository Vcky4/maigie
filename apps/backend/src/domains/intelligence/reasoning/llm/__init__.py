"""
Multi-provider LLM adapters — Gemini, OpenAI, Anthropic.

Provides a clean interface for other domains to use LLM capabilities
without knowing about provider specifics.
"""

import logging
from typing import Any

from .gemini_sdk import new_gemini_client, types as gemini_types
from .registry import LlmTask, default_model_for, gemini_api_key
from .errors import LLMError, LLMProviderError, LLMUnavailableError, GeminiError

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
    return (response.text or "").strip()
