"""Lightweight tests for gemini_tool_handlers routing (no DB for unknown tools)."""

import os

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")


@pytest.mark.asyncio
async def test_handle_tool_call_unknown_tool_returns_error():
    from src.services.gemini_tool_handlers import handle_tool_call

    out = await handle_tool_call("not_a_real_tool_xyz", {}, "user-id-placeholder")
    assert "error" in out
    assert "Unknown tool" in out["error"]


@pytest.mark.asyncio
async def test_handle_tool_call_empty_name():
    from src.services.gemini_tool_handlers import handle_tool_call

    out = await handle_tool_call("", {}, "user-id-placeholder")
    assert "error" in out
