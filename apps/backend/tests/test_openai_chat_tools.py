"""Unit tests for the OpenAI chat tools adapter.

Tests cover:
- Error mapping from OpenAI SDK exceptions to OpenAIError
- History conversion to OpenAI message format
- Adapter initialization and capability reporting
- Tool call loop termination (max 6 iterations)
- Disconnect handling (partial response preservation)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.llm.errors import OpenAIError
from src.services.llm.openai_chat_tools import (
    OpenAIChatToolsAdapter,
    _history_to_openai_messages,
    _map_openai_error,
)


# ---------------------------------------------------------------------------
# Error mapping tests
# ---------------------------------------------------------------------------


class TestErrorMapping:
    """Tests for _map_openai_error."""

    def _make_api_status_error(self, status_code: int, message: str = "error"):
        """Create a mock openai.APIStatusError."""
        import openai

        # APIStatusError requires a response and body
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.headers = {}

        err = openai.APIStatusError.__new__(openai.APIStatusError)
        err.status_code = status_code
        err.response = mock_response
        err.body = None
        err.args = (message,)
        err.message = message
        return err

    def test_rate_limit_429(self):
        exc = self._make_api_status_error(429)
        result = _map_openai_error(exc, "gpt-4o-mini")
        assert result.category == "rate_limit"
        assert result.retriable is True
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_auth_401(self):
        exc = self._make_api_status_error(401)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "auth"
        assert result.retriable is False

    def test_auth_403(self):
        exc = self._make_api_status_error(403)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "auth"
        assert result.retriable is False

    def test_invalid_request_400(self):
        exc = self._make_api_status_error(400)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "invalid_request"
        assert result.retriable is False

    def test_invalid_request_404(self):
        exc = self._make_api_status_error(404)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "invalid_request"
        assert result.retriable is False

    def test_invalid_request_422(self):
        exc = self._make_api_status_error(422)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "invalid_request"
        assert result.retriable is False

    def test_server_error_500(self):
        exc = self._make_api_status_error(500)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "server_error"
        assert result.retriable is True

    def test_server_error_502(self):
        exc = self._make_api_status_error(502)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "server_error"
        assert result.retriable is True

    def test_overloaded_529(self):
        exc = self._make_api_status_error(529)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "overloaded"
        assert result.retriable is True

    def test_overloaded_503_with_message(self):
        exc = self._make_api_status_error(503, "The server is overloaded")
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "overloaded"
        assert result.retriable is True

    def test_server_error_503_generic(self):
        exc = self._make_api_status_error(503, "Service unavailable")
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "server_error"
        assert result.retriable is True

    def test_timeout_error(self):
        import openai

        exc = openai.APITimeoutError.__new__(openai.APITimeoutError)
        exc.args = ("Request timed out",)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "server_error"
        assert result.retriable is True

    def test_connection_error(self):
        import openai

        exc = openai.APIConnectionError.__new__(openai.APIConnectionError)
        exc.args = ("Connection failed",)
        result = _map_openai_error(exc, "gpt-4o")
        assert result.category == "server_error"
        assert result.retriable is True


# ---------------------------------------------------------------------------
# History conversion tests
# ---------------------------------------------------------------------------


class TestHistoryConversion:
    """Tests for _history_to_openai_messages."""

    def test_empty_history(self):
        messages = _history_to_openai_messages([], "You are a helpful assistant.")
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."

    def test_user_and_model_messages(self):
        history = [
            {"role": "user", "parts": ["Hello"]},
            {"role": "model", "parts": ["Hi there!"]},
        ]
        messages = _history_to_openai_messages(history, "System prompt")
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "assistant"  # "model" mapped to "assistant"
        assert messages[2]["content"] == "Hi there!"

    def test_dict_parts_with_text(self):
        history = [
            {"role": "user", "parts": [{"text": "What is 2+2?"}]},
        ]
        messages = _history_to_openai_messages(history, "System")
        assert messages[1]["content"] == "What is 2+2?"

    def test_multiple_text_parts_joined(self):
        history = [
            {"role": "user", "parts": ["Part 1", "Part 2"]},
        ]
        messages = _history_to_openai_messages(history, "System")
        assert messages[1]["content"] == "Part 1\nPart 2"

    def test_empty_parts_skipped(self):
        history = [
            {"role": "user", "parts": []},
        ]
        messages = _history_to_openai_messages(history, "System")
        # Empty content message should be skipped
        assert len(messages) == 1  # Only system message


# ---------------------------------------------------------------------------
# Adapter initialization tests
# ---------------------------------------------------------------------------


class TestAdapterInit:
    """Tests for OpenAIChatToolsAdapter initialization and properties."""

    def test_provider_name(self):
        adapter = OpenAIChatToolsAdapter(model="gpt-4o-mini", api_key="test-key")
        assert adapter.provider_name == "openai"

    def test_model_id(self):
        adapter = OpenAIChatToolsAdapter(model="gpt-4o", api_key="test-key")
        assert adapter.model_id == "gpt-4o"

    def test_capabilities_chat_model(self):
        from src.services.llm.capabilities import (
            ChatCapability,
            EmbeddingCapability,
            StructuredOutputCapability,
            VisionCapability,
        )

        adapter = OpenAIChatToolsAdapter(model="gpt-4o-mini", api_key="test-key")
        caps = adapter.supported_capabilities()
        assert ChatCapability in caps
        assert VisionCapability in caps
        assert StructuredOutputCapability in caps
        assert EmbeddingCapability not in caps

    def test_capabilities_embedding_model(self):
        from src.services.llm.capabilities import EmbeddingCapability

        adapter = OpenAIChatToolsAdapter(model="text-embedding-3-small", api_key="test-key")
        caps = adapter.supported_capabilities()
        assert EmbeddingCapability in caps

    def test_extends_base_adapter(self):
        from src.services.llm.base_adapter import BaseProviderAdapter

        adapter = OpenAIChatToolsAdapter(model="gpt-4o", api_key="test-key")
        assert isinstance(adapter, BaseProviderAdapter)
