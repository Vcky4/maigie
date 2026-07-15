"""
Reasoning service — LLM orchestration for chat responses.

Routes requests through the multi-provider LLM infrastructure,
applying memory context, RAG, and tool definitions.
This is the core "thinking" engine of Intelligence.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def generate_response(
    *,
    user_id: str,
    session_id: str,
    message: str,
    image_urls: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an AI response for a user message.

    Orchestrates:
    1. Memory retrieval (conversation history, user facts)
    2. RAG context (relevant knowledge)
    3. LLM call (with tool definitions for skills)
    4. Tool execution (if AI requests actions)
    5. Response formatting

    Delegates to the existing chat pipeline during migration.
    """
    # During migration, delegate to existing chat service
    from src.services.chat_session_service import process_chat_message

    result = await process_chat_message(
        user_id=user_id,
        session_id=session_id,
        user_message=message,
        image_urls=image_urls or [],
    )
    return result


async def generate_streaming_response(
    *,
    user_id: str,
    session_id: str,
    message: str,
    image_urls: list[str] | None = None,
):
    """Generate a streaming AI response (for WebSocket).

    Yields chunks as they arrive from the LLM.
    """
    # Streaming is handled by the WebSocket handler in conversation/
    # This service provides the non-streaming path
    raise NotImplementedError("Streaming handled by WebSocket layer")
