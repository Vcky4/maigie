"""Base provider adapter ABC for multi-model LLM support.

All provider adapters (Gemini, OpenAI, Anthropic) extend this class to ensure
they satisfy the :class:`ChatWithToolsProvider` protocol and declare their
supported capabilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProviderAdapter(ABC):
    """Abstract base class for all LLM provider adapters.

    Subclasses must implement the :pymethod:`get_chat_response_with_tools`
    method matching the :class:`ChatWithToolsProvider` protocol, declare their
    :attr:`provider_name` and :attr:`model_id`, and report which capability
    protocols they support via :pymethod:`supported_capabilities`.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the canonical provider identifier (e.g. 'gemini', 'openai', 'anthropic')."""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the model identifier used for this adapter instance."""
        ...

    @abstractmethod
    def supported_capabilities(self) -> set[type]:
        """Return the set of capability protocol classes this adapter implements.

        Example return value::

            {ChatCapability, VisionCapability}
        """
        ...

    @abstractmethod
    async def get_chat_response_with_tools(
        self,
        history: list,
        user_message: str,
        context: dict | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        image_url: str | None = None,
        progress_callback: Any = None,
        stream_callback: Any = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """Stream-capable chat with function calling.

        Returns a tuple of (response_text, usage_dict, actions, query_rows).

        This signature matches the existing :class:`ChatWithToolsProvider`
        protocol so that all adapters are drop-in replacements for the
        original Gemini implementation.
        """
        ...
