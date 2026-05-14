"""Multi-provider LLM package (Phase B — adapter boundary).

Imports are lazy to avoid blocking app startup when provider SDKs
(google-genai, openai, anthropic) perform network initialization.
"""

__all__ = [
    "BaseProviderAdapter",
    "ChatToolTurnResult",
    "ChatWithToolsProvider",
    "GeminiChatToolsAdapter",
    "OpenAIChatToolsAdapter",
    "SYSTEM_INSTRUCTION",
    "StreamConsumerDisconnected",
    "TokenUsage",
    "build_personalized_system_instruction",
    "gemini_types",
    "genai",
    "is_websocket_consumer_disconnect",
    "new_gemini_client",
    "run_gemini_chat_with_tools",
]


def __getattr__(name: str):
    """Lazy import to avoid blocking startup with heavy SDK imports."""
    if name == "BaseProviderAdapter":
        from src.services.llm.base_adapter import BaseProviderAdapter

        return BaseProviderAdapter
    if name in ("GeminiChatToolsAdapter", "run_gemini_chat_with_tools"):
        from src.services.llm.gemini_chat_tools import (
            GeminiChatToolsAdapter,
            run_gemini_chat_with_tools,
        )

        return (
            GeminiChatToolsAdapter
            if name == "GeminiChatToolsAdapter"
            else run_gemini_chat_with_tools
        )
    if name in ("genai", "new_gemini_client", "gemini_types"):
        from src.services.llm.gemini_sdk import genai, new_gemini_client, types as gemini_types

        if name == "genai":
            return genai
        if name == "new_gemini_client":
            return new_gemini_client
        return gemini_types
    if name == "OpenAIChatToolsAdapter":
        from src.services.llm.openai_chat_tools import OpenAIChatToolsAdapter

        return OpenAIChatToolsAdapter
    if name in ("SYSTEM_INSTRUCTION", "build_personalized_system_instruction"):
        from src.services.llm.prompts import (
            SYSTEM_INSTRUCTION,
            build_personalized_system_instruction,
        )

        return (
            SYSTEM_INSTRUCTION
            if name == "SYSTEM_INSTRUCTION"
            else build_personalized_system_instruction
        )
    if name == "ChatWithToolsProvider":
        from src.services.llm.protocol import ChatWithToolsProvider

        return ChatWithToolsProvider
    if name in ("StreamConsumerDisconnected", "is_websocket_consumer_disconnect"):
        from src.services.llm.streaming import (
            StreamConsumerDisconnected,
            is_websocket_consumer_disconnect,
        )

        return (
            StreamConsumerDisconnected
            if name == "StreamConsumerDisconnected"
            else is_websocket_consumer_disconnect
        )
    if name in ("ChatToolTurnResult", "TokenUsage"):
        from src.services.llm.types import ChatToolTurnResult, TokenUsage

        return ChatToolTurnResult if name == "ChatToolTurnResult" else TokenUsage
    raise AttributeError(f"module 'src.services.llm' has no attribute {name!r}")
