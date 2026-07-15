"""
Multi-provider LLM adapters — Gemini, OpenAI, Anthropic.

Provides a clean interface for other domains to use LLM capabilities
without knowing about provider specifics.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def generate_content(prompt: str, *, max_tokens: int = 2048, temperature: float = 0.7) -> str:
    """Generate text content using the default LLM provider.

    This is the primary interface for domains that need simple text generation
    (topic explanations, quizzes, summaries, etc.).

    Args:
        prompt: The prompt to send to the LLM.
        max_tokens: Maximum output tokens.
        temperature: Creativity parameter (0.0 = deterministic, 1.0 = creative).

    Returns:
        Generated text content.
    """
    from src.services.llm.gemini_sdk import new_gemini_client, types as _types
    from src.services.llm_registry import LlmTask, default_model_for, gemini_api_key

    client = new_gemini_client(gemini_api_key() or None)
    response = await client.aio.models.generate_content(
        model=default_model_for(LlmTask.CHAT_DEFAULT),
        contents=prompt,
        config=_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return (response.text or "").strip()


async def generate_with_tools(
    *,
    history: list[dict],
    user_message: str,
    user_id: str,
    context: dict | None = None,
) -> dict[str, Any]:
    """Generate a response with tool/skill calling support.

    Used by the intelligence domain's reasoning layer.
    """
    # This delegates to the full LLM router during migration
    from src.services.llm.adapter_registry import get_llm_router
    from src.services.llm_registry import LlmTask

    router = get_llm_router()
    return await router.route_request(
        task=LlmTask.CHAT_TOOLS_SESSION,
        user_id=user_id,
        user_tier="plus",
        model_preference=None,
        history=history,
        user_message=user_message,
        context=context,
    )
