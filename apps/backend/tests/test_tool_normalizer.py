"""Unit tests for the ToolNormalizer class."""

import json

import pytest

from src.services.llm.tool_normalizer import ToolNormalizer, _relaxed_json_parse
from src.services.llm.types import ToolCallRequest, ToolDefinition


@pytest.fixture
def normalizer() -> ToolNormalizer:
    return ToolNormalizer()


@pytest.fixture
def sample_tool() -> ToolDefinition:
    return ToolDefinition(
        name="get_weather",
        description="Get the current weather for a location",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"},
                "units": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature units",
                },
            },
        },
        required=["location"],
    )


# ---------------------------------------------------------------------------
# Tests for to_gemini
# ---------------------------------------------------------------------------


class TestToGemini:
    def test_basic_conversion(self, normalizer: ToolNormalizer, sample_tool: ToolDefinition):
        result = normalizer.to_gemini([sample_tool])
        assert len(result) == 1
        decl = result[0]
        assert decl["name"] == "get_weather"
        assert decl["description"] == "Get the current weather for a location"
        assert decl["parameters"]["type"] == "object"
        assert "location" in decl["parameters"]["properties"]
        assert decl["parameters"]["required"] == ["location"]

    def test_empty_tools(self, normalizer: ToolNormalizer):
        assert normalizer.to_gemini([]) == []

    def test_tool_without_parameters(self, normalizer: ToolNormalizer):
        tool = ToolDefinition(name="ping", description="Ping the server", parameters={})
        result = normalizer.to_gemini([tool])
        assert result[0]["name"] == "ping"
        assert result[0]["parameters"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Tests for to_openai
# ---------------------------------------------------------------------------


class TestToOpenAI:
    def test_basic_conversion(self, normalizer: ToolNormalizer, sample_tool: ToolDefinition):
        result = normalizer.to_openai([sample_tool])
        assert len(result) == 1
        wrapper = result[0]
        assert wrapper["type"] == "function"
        func = wrapper["function"]
        assert func["name"] == "get_weather"
        assert func["description"] == "Get the current weather for a location"
        assert func["parameters"]["type"] == "object"
        assert "location" in func["parameters"]["properties"]
        assert func["parameters"]["required"] == ["location"]

    def test_empty_tools(self, normalizer: ToolNormalizer):
        assert normalizer.to_openai([]) == []


# ---------------------------------------------------------------------------
# Tests for to_anthropic
# ---------------------------------------------------------------------------


class TestToAnthropic:
    def test_basic_conversion(self, normalizer: ToolNormalizer, sample_tool: ToolDefinition):
        result = normalizer.to_anthropic([sample_tool])
        assert len(result) == 1
        tool_def = result[0]
        assert tool_def["name"] == "get_weather"
        assert tool_def["description"] == "Get the current weather for a location"
        assert tool_def["input_schema"]["type"] == "object"
        assert "location" in tool_def["input_schema"]["properties"]
        assert tool_def["input_schema"]["required"] == ["location"]

    def test_empty_parameters_still_has_input_schema(self, normalizer: ToolNormalizer):
        tool = ToolDefinition(name="ping", description="Ping", parameters={})
        result = normalizer.to_anthropic([tool])
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Tests for normalize_tool_calls_gemini
# ---------------------------------------------------------------------------


class TestNormalizeToolCallsGemini:
    def test_dict_format(self, normalizer: ToolNormalizer):
        calls = [{"name": "get_weather", "args": {"location": "London"}}]
        result = normalizer.normalize_tool_calls_gemini(calls)
        assert len(result) == 1
        assert result[0].name == "get_weather"
        assert result[0].arguments == {"location": "London"}
        assert result[0].id  # UUID generated

    def test_object_format(self, normalizer: ToolNormalizer):
        class FakeFC:
            name = "search"
            args = {"query": "hello"}

        result = normalizer.normalize_tool_calls_gemini([FakeFC()])
        assert result[0].name == "search"
        assert result[0].arguments == {"query": "hello"}

    def test_skips_calls_without_name(self, normalizer: ToolNormalizer):
        calls = [{"args": {"x": 1}}]
        result = normalizer.normalize_tool_calls_gemini(calls)
        assert result == []

    def test_empty_list(self, normalizer: ToolNormalizer):
        assert normalizer.normalize_tool_calls_gemini([]) == []


# ---------------------------------------------------------------------------
# Tests for normalize_tool_calls_openai
# ---------------------------------------------------------------------------


class TestNormalizeToolCallsOpenAI:
    def test_dict_format(self, normalizer: ToolNormalizer):
        calls = [
            {
                "id": "call_abc123",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "Paris"}',
                },
            }
        ]
        result = normalizer.normalize_tool_calls_openai(calls)
        assert len(result) == 1
        assert result[0].id == "call_abc123"
        assert result[0].name == "get_weather"
        assert result[0].arguments == {"location": "Paris"}

    def test_malformed_json_with_trailing_comma(self, normalizer: ToolNormalizer):
        calls = [
            {
                "id": "call_1",
                "function": {
                    "name": "search",
                    "arguments": '{"query": "test",}',
                },
            }
        ]
        result = normalizer.normalize_tool_calls_openai(calls)
        assert result[0].arguments == {"query": "test"}

    def test_completely_invalid_json_raises(self, normalizer: ToolNormalizer):
        calls = [
            {
                "id": "call_1",
                "function": {
                    "name": "search",
                    "arguments": "not json at all {{{{",
                },
            }
        ]
        with pytest.raises(ValueError, match="Malformed tool call arguments"):
            normalizer.normalize_tool_calls_openai(calls)

    def test_skips_calls_without_name(self, normalizer: ToolNormalizer):
        calls = [{"id": "call_1", "function": {"name": "", "arguments": "{}"}}]
        result = normalizer.normalize_tool_calls_openai(calls)
        assert result == []


# ---------------------------------------------------------------------------
# Tests for normalize_tool_calls_anthropic
# ---------------------------------------------------------------------------


class TestNormalizeToolCallsAnthropic:
    def test_dict_format(self, normalizer: ToolNormalizer):
        blocks = [
            {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "get_weather",
                "input": {"location": "Tokyo"},
            }
        ]
        result = normalizer.normalize_tool_calls_anthropic(blocks)
        assert len(result) == 1
        assert result[0].id == "toolu_abc"
        assert result[0].name == "get_weather"
        assert result[0].arguments == {"location": "Tokyo"}

    def test_filters_non_tool_use_blocks(self, normalizer: ToolNormalizer):
        blocks = [
            {"type": "text", "text": "Hello"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "search",
                "input": {"q": "test"},
            },
        ]
        result = normalizer.normalize_tool_calls_anthropic(blocks)
        assert len(result) == 1
        assert result[0].name == "search"

    def test_empty_input(self, normalizer: ToolNormalizer):
        blocks = [{"type": "tool_use", "id": "toolu_1", "name": "ping", "input": {}}]
        result = normalizer.normalize_tool_calls_anthropic(blocks)
        assert result[0].arguments == {}


# ---------------------------------------------------------------------------
# Tests for to_tool_result_*
# ---------------------------------------------------------------------------


class TestToolResults:
    def test_gemini_result(self, normalizer: ToolNormalizer):
        result = normalizer.to_tool_result_gemini(
            "get_weather", {"temperature": 22, "unit": "celsius"}
        )
        assert result == {
            "function_response": {
                "name": "get_weather",
                "response": {"temperature": 22, "unit": "celsius"},
            }
        }

    def test_openai_result(self, normalizer: ToolNormalizer):
        result = normalizer.to_tool_result_openai(
            "call_abc", {"temperature": 22, "unit": "celsius"}
        )
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_abc"
        assert json.loads(result["content"]) == {
            "temperature": 22,
            "unit": "celsius",
        }

    def test_anthropic_result(self, normalizer: ToolNormalizer):
        result = normalizer.to_tool_result_anthropic(
            "toolu_abc", {"temperature": 22, "unit": "celsius"}
        )
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "toolu_abc"
        assert json.loads(result["content"]) == {
            "temperature": 22,
            "unit": "celsius",
        }


# ---------------------------------------------------------------------------
# Tests for relaxed JSON parsing
# ---------------------------------------------------------------------------


class TestRelaxedJsonParse:
    def test_valid_json(self):
        assert _relaxed_json_parse('{"key": "value"}') == {"key": "value"}

    def test_trailing_comma(self):
        assert _relaxed_json_parse('{"key": "value",}') == {"key": "value"}

    def test_single_quotes(self):
        assert _relaxed_json_parse("{'key': 'value'}") == {"key": "value"}

    def test_unquoted_keys(self):
        assert _relaxed_json_parse('{name: "test", count: 5}') == {
            "name": "test",
            "count": 5,
        }

    def test_empty_string(self):
        assert _relaxed_json_parse("") == {}

    def test_whitespace_only(self):
        assert _relaxed_json_parse("   ") == {}

    def test_completely_invalid(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            _relaxed_json_parse("not json at all {{{{")
