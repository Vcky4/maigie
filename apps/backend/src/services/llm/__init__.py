"""Multi-provider LLM package (Phase B — adapter boundary)."""

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.gemini_chat_tools import GeminiChatToolsAdapter, run_gemini_chat_with_tools
from src.services.llm.gemini_sdk import genai, new_gemini_client, types as gemini_types
from src.services.llm.openai_chat_tools import OpenAIChatToolsAdapter
from src.services.llm.prompts import SYSTEM_INSTRUCTION, build_personalized_system_instruction
from src.services.llm.protocol import ChatWithToolsProvider
from src.services.llm.streaming import StreamConsumerDisconnected, is_websocket_consumer_disconnect
from src.services.llm.types import ChatToolTurnResult, TokenUsage

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
