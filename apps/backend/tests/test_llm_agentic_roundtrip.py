"""Agentic chat wiring: service delegates to ``run_gemini_chat_with_tools`` (no live API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _stub_gemini_init(self) -> None:
    self.safety_settings = []


@pytest.mark.asyncio
async def test_gemini_service_get_chat_response_with_tools_delegates() -> None:
    from src.services.llm_service import GeminiService

    with (
        patch.object(GeminiService, "__init__", _stub_gemini_init),
        patch(
            "src.services.llm_service.run_gemini_chat_with_tools",
            new_callable=AsyncMock,
        ) as run,
    ):
        run.return_value = (
            "assistant text",
            {"input_tokens": 3, "output_tokens": 4, "model_name": "stub"},
            [{"type": "create_note"}],
            [{"tool_name": "get_user_courses"}],
        )
        svc = GeminiService()
        text, usage, actions, queries = await svc.get_chat_response_with_tools(
            history=[{"role": "user", "parts": ["hi"]}],
            user_message="hello",
            context={"pageContext": "test"},
            user_id="user-1",
            user_name="Pat",
        )
        assert text == "assistant text"
        assert usage["input_tokens"] == 3
        assert len(actions) == 1
        assert len(queries) == 1
        run.assert_awaited_once()
        kwargs = run.await_args.kwargs
        assert kwargs["user_id"] == "user-1"
        assert kwargs["user_name"] == "Pat"
        assert kwargs["safety_settings"] == []
