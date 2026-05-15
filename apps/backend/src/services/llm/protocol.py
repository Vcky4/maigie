"""
Abstract chat+tools surface (Phase B).

The lazy ``llm_service`` proxy implements this protocol (see ``chat_with_tools_provider()`` in
``llm_service``). Call ``get_chat_response_with_tools`` on that proxy or use the typed accessor.
Other providers should implement the same contract behind feature flags.
"""

from __future__ import annotations

from typing import Any, Protocol


class ChatWithToolsProvider(Protocol):
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
        """Stream-capable chat with function calling; returns text, usage, actions, query rows."""
        ...
