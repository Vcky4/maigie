"""Unit tests for the StreamNormalizer class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.services.llm.stream_normalizer import StreamNormalizer
from src.services.llm.types import StreamEvent, ToolCallDelta

# ---------------------------------------------------------------------------
# Mock objects to simulate provider-specific chunk structures
# ---------------------------------------------------------------------------


@dataclass
class MockOpenAIFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class MockOpenAIToolCallDelta:
    index: int = 0
    id: str | None = None
    function: MockOpenAIFunction | None = None


@dataclass
class MockOpenAIDelta:
    content: str | None = None
    tool_calls: list[MockOpenAIToolCallDelta] | None = None


@dataclass
class MockOpenAIChoice:
    delta: MockOpenAIDelta | None = None
    finish_reason: str | None = None
    index: int = 0


@dataclass
class MockOpenAIChunk:
    choices: list[MockOpenAIChoice] | None = None


@dataclass
class MockAnthropicContentBlock:
    type: str = "text"
    id: str | None = None
    name: str | None = None


@dataclass
class MockAnthropicTextDelta:
    type: str = "text_delta"
    text: str | None = None


@dataclass
class MockAnthropicInputJsonDelta:
    type: str = "input_json_delta"
    partial_json: str | None = None


@dataclass
class MockAnthropicMessageDelta:
    type: str = "message_delta"
    stop_reason: str | None = None


@dataclass
class MockAnthropicEvent:
    type: str = ""
    content_block: Any = None
    delta: Any = None


@dataclass
class MockGeminiFunctionCall:
    name: str | None = None
    args: dict[str, Any] | None = None


@dataclass
class MockGeminiPart:
    text: str | None = None
    function_call: MockGeminiFunctionCall | None = None


@dataclass
class MockGeminiContent:
    parts: list[MockGeminiPart] | None = None


@dataclass
class MockGeminiCandidate:
    content: MockGeminiContent | None = None
    finish_reason: Any = None


@dataclass
class MockGeminiChunk:
    candidates: list[MockGeminiCandidate] | None = None
    text: str | None = None


# ---------------------------------------------------------------------------
# Tests for from_openai_chunk
# ---------------------------------------------------------------------------


class TestFromOpenAIChunk:
    def test_text_delta(self):
        chunk = MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content="Hello"))])
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "text_delta"
        assert events[0].text == "Hello"

    def test_empty_text_not_emitted(self):
        chunk = MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content=""))])
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 0

    def test_none_text_not_emitted(self):
        chunk = MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content=None))])
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 0

    def test_tool_call_delta_with_id_and_name(self):
        chunk = MockOpenAIChunk(
            choices=[
                MockOpenAIChoice(
                    delta=MockOpenAIDelta(
                        tool_calls=[
                            MockOpenAIToolCallDelta(
                                index=0,
                                id="call_123",
                                function=MockOpenAIFunction(name="get_weather", arguments=None),
                            )
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call is not None
        assert events[0].tool_call.id == "call_123"
        assert events[0].tool_call.name == "get_weather"
        assert events[0].tool_call.arguments_fragment is None

    def test_tool_call_delta_with_arguments(self):
        chunk = MockOpenAIChunk(
            choices=[
                MockOpenAIChoice(
                    delta=MockOpenAIDelta(
                        tool_calls=[
                            MockOpenAIToolCallDelta(
                                index=0,
                                id=None,
                                function=MockOpenAIFunction(name=None, arguments='{"location":'),
                            )
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call is not None
        assert events[0].tool_call.arguments_fragment == '{"location":'

    def test_tool_call_delta_empty_not_emitted(self):
        """Tool call with no ID, no name, and no arguments should not emit."""
        chunk = MockOpenAIChunk(
            choices=[
                MockOpenAIChoice(
                    delta=MockOpenAIDelta(
                        tool_calls=[
                            MockOpenAIToolCallDelta(
                                index=0,
                                id=None,
                                function=MockOpenAIFunction(name=None, arguments=None),
                            )
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 0

    def test_done_event_on_finish_reason(self):
        chunk = MockOpenAIChunk(
            choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content=None), finish_reason="stop")]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "done"
        assert events[0].done is True

    def test_text_and_finish_in_same_chunk(self):
        chunk = MockOpenAIChunk(
            choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content="end"), finish_reason="stop")]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 2
        assert events[0].type == "text_delta"
        assert events[0].text == "end"
        assert events[1].type == "done"
        assert events[1].done is True

    def test_empty_choices(self):
        chunk = MockOpenAIChunk(choices=[])
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert events == []

    def test_none_choices(self):
        chunk = MockOpenAIChunk(choices=None)
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert events == []

    def test_multiple_tool_calls_in_one_chunk(self):
        chunk = MockOpenAIChunk(
            choices=[
                MockOpenAIChoice(
                    delta=MockOpenAIDelta(
                        tool_calls=[
                            MockOpenAIToolCallDelta(
                                index=0,
                                id="call_1",
                                function=MockOpenAIFunction(name="tool_a"),
                            ),
                            MockOpenAIToolCallDelta(
                                index=1,
                                id="call_2",
                                function=MockOpenAIFunction(name="tool_b"),
                            ),
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        assert len(events) == 2
        assert all(e.type == "tool_call_delta" for e in events)
        assert events[0].tool_call.id == "call_1"
        assert events[1].tool_call.id == "call_2"


# ---------------------------------------------------------------------------
# Tests for from_anthropic_event
# ---------------------------------------------------------------------------


class TestFromAnthropicEvent:
    def test_text_delta(self):
        event = MockAnthropicEvent(
            type="content_block_delta",
            delta=MockAnthropicTextDelta(type="text_delta", text="Hello"),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 1
        assert events[0].type == "text_delta"
        assert events[0].text == "Hello"

    def test_empty_text_not_emitted(self):
        event = MockAnthropicEvent(
            type="content_block_delta",
            delta=MockAnthropicTextDelta(type="text_delta", text=""),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 0

    def test_tool_use_block_start(self):
        event = MockAnthropicEvent(
            type="content_block_start",
            content_block=MockAnthropicContentBlock(
                type="tool_use", id="toolu_123", name="get_weather"
            ),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 1
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call is not None
        assert events[0].tool_call.id == "toolu_123"
        assert events[0].tool_call.name == "get_weather"

    def test_input_json_delta(self):
        event = MockAnthropicEvent(
            type="content_block_delta",
            delta=MockAnthropicInputJsonDelta(type="input_json_delta", partial_json='{"loc'),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 1
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call is not None
        assert events[0].tool_call.arguments_fragment == '{"loc'

    def test_empty_input_json_not_emitted(self):
        event = MockAnthropicEvent(
            type="content_block_delta",
            delta=MockAnthropicInputJsonDelta(type="input_json_delta", partial_json=""),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 0

    def test_message_stop_emits_done(self):
        event = MockAnthropicEvent(type="message_stop")
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 1
        assert events[0].type == "done"
        assert events[0].done is True

    def test_message_delta_with_stop_reason(self):
        event = MockAnthropicEvent(
            type="message_delta",
            delta=MockAnthropicMessageDelta(type="message_delta", stop_reason="end_turn"),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 1
        assert events[0].type == "done"
        assert events[0].done is True

    def test_message_delta_without_stop_reason(self):
        event = MockAnthropicEvent(
            type="message_delta",
            delta=MockAnthropicMessageDelta(type="message_delta", stop_reason=None),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert len(events) == 0

    def test_unrecognized_event_type(self):
        event = MockAnthropicEvent(type="ping")
        events = StreamNormalizer.from_anthropic_event(event)
        assert events == []

    def test_content_block_start_text_type_not_emitted(self):
        """Text content_block_start should not emit tool_call_delta."""
        event = MockAnthropicEvent(
            type="content_block_start",
            content_block=MockAnthropicContentBlock(type="text"),
        )
        events = StreamNormalizer.from_anthropic_event(event)
        assert events == []


# ---------------------------------------------------------------------------
# Tests for from_gemini_chunk
# ---------------------------------------------------------------------------


class TestFromGeminiChunk:
    def test_text_delta(self):
        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(content=MockGeminiContent(parts=[MockGeminiPart(text="Hello")]))
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "text_delta"
        assert events[0].text == "Hello"

    def test_empty_text_not_emitted(self):
        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(content=MockGeminiContent(parts=[MockGeminiPart(text="")]))
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 0

    def test_function_call(self):
        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(
                    content=MockGeminiContent(
                        parts=[
                            MockGeminiPart(
                                function_call=MockGeminiFunctionCall(
                                    name="get_weather",
                                    args={"location": "London"},
                                )
                            )
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "tool_call_delta"
        assert events[0].tool_call is not None
        assert events[0].tool_call.name == "get_weather"
        assert events[0].tool_call.id == "gemini_call_get_weather"
        assert '"location"' in events[0].tool_call.arguments_fragment

    def test_finish_reason_stop(self):
        """Gemini STOP finish_reason should emit done event."""

        @dataclass
        class StopReason:
            name: str = "STOP"

        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(
                    content=MockGeminiContent(parts=[]),
                    finish_reason=StopReason(),
                )
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "done"
        assert events[0].done is True

    def test_finish_reason_numeric_stop(self):
        """Gemini numeric finish_reason 1 (STOP) should emit done event."""

        @dataclass
        class NumericStopReason:
            value: int = 1

        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(
                    content=MockGeminiContent(parts=[]),
                    finish_reason=NumericStopReason(),
                )
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "done"

    def test_no_candidates_with_text_attr(self):
        """Fallback: chunk with .text attribute but no candidates."""
        chunk = MockGeminiChunk(candidates=None, text="Fallback text")
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 1
        assert events[0].type == "text_delta"
        assert events[0].text == "Fallback text"

    def test_no_candidates_no_text(self):
        chunk = MockGeminiChunk(candidates=None, text=None)
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert events == []

    def test_text_and_function_call_in_same_chunk(self):
        chunk = MockGeminiChunk(
            candidates=[
                MockGeminiCandidate(
                    content=MockGeminiContent(
                        parts=[
                            MockGeminiPart(text="Let me check"),
                            MockGeminiPart(
                                function_call=MockGeminiFunctionCall(
                                    name="search", args={"q": "test"}
                                )
                            ),
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_gemini_chunk(chunk)
        assert len(events) == 2
        assert events[0].type == "text_delta"
        assert events[0].text == "Let me check"
        assert events[1].type == "tool_call_delta"
        assert events[1].tool_call.name == "search"


# ---------------------------------------------------------------------------
# Tests for error_event
# ---------------------------------------------------------------------------


class TestErrorEvent:
    def test_creates_error_event(self):
        event = StreamNormalizer.error_event("openai", "rate_limit")
        assert event.type == "error"
        assert event.text == "openai:rate_limit"

    def test_error_event_contains_provider_and_category(self):
        event = StreamNormalizer.error_event("anthropic", "server_error")
        assert "anthropic" in event.text
        assert "server_error" in event.text

    def test_error_event_for_gemini(self):
        event = StreamNormalizer.error_event("gemini", "overloaded")
        assert event.type == "error"
        assert event.text == "gemini:overloaded"


# ---------------------------------------------------------------------------
# Tests for stream sequence invariants
# ---------------------------------------------------------------------------


class TestStreamSequenceInvariants:
    """Tests verifying the stream normalizer produces correct event sequences."""

    def test_text_delta_has_non_empty_text(self):
        """All text_delta events must have non-empty text."""
        chunk = MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content="data"))])
        events = StreamNormalizer.from_openai_chunk(chunk)
        for event in events:
            if event.type == "text_delta":
                assert event.text is not None
                assert len(event.text) > 0

    def test_tool_call_delta_has_meaningful_data(self):
        """tool_call_delta events must have at least one of: ID, name, arguments."""
        chunk = MockOpenAIChunk(
            choices=[
                MockOpenAIChoice(
                    delta=MockOpenAIDelta(
                        tool_calls=[
                            MockOpenAIToolCallDelta(
                                index=0,
                                id="call_abc",
                                function=MockOpenAIFunction(name="fn", arguments='{"x":1}'),
                            )
                        ]
                    )
                )
            ]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        for event in events:
            if event.type == "tool_call_delta":
                tc = event.tool_call
                assert tc is not None
                assert tc.id or tc.name or tc.arguments_fragment

    def test_done_event_has_done_flag(self):
        """done events must have done=True."""
        chunk = MockOpenAIChunk(
            choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content=None), finish_reason="stop")]
        )
        events = StreamNormalizer.from_openai_chunk(chunk)
        done_events = [e for e in events if e.type == "done"]
        assert len(done_events) == 1
        assert done_events[0].done is True

    def test_simulated_full_openai_stream(self):
        """Simulate a full OpenAI stream and verify exactly one done event at end."""
        chunks = [
            MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content="Hello"))]),
            MockOpenAIChunk(choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content=" world"))]),
            MockOpenAIChunk(
                choices=[
                    MockOpenAIChoice(delta=MockOpenAIDelta(content=None), finish_reason="stop")
                ]
            ),
        ]

        all_events: list[StreamEvent] = []
        for chunk in chunks:
            all_events.extend(StreamNormalizer.from_openai_chunk(chunk))

        # Verify text events
        text_events = [e for e in all_events if e.type == "text_delta"]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello"
        assert text_events[1].text == " world"

        # Verify exactly one done event at the end
        done_events = [e for e in all_events if e.type == "done"]
        assert len(done_events) == 1
        assert all_events[-1].type == "done"

    def test_simulated_full_anthropic_stream(self):
        """Simulate a full Anthropic stream and verify exactly one done event at end."""
        events_raw = [
            MockAnthropicEvent(
                type="content_block_delta",
                delta=MockAnthropicTextDelta(type="text_delta", text="Hi"),
            ),
            MockAnthropicEvent(
                type="content_block_delta",
                delta=MockAnthropicTextDelta(type="text_delta", text=" there"),
            ),
            MockAnthropicEvent(type="message_stop"),
        ]

        all_events: list[StreamEvent] = []
        for raw_event in events_raw:
            all_events.extend(StreamNormalizer.from_anthropic_event(raw_event))

        text_events = [e for e in all_events if e.type == "text_delta"]
        assert len(text_events) == 2

        done_events = [e for e in all_events if e.type == "done"]
        assert len(done_events) == 1
        assert all_events[-1].type == "done"

    def test_error_event_terminates_sequence(self):
        """After an error event, no further events should be emitted."""
        # Simulate: text → error (no done after error)
        text_chunk = MockOpenAIChunk(
            choices=[MockOpenAIChoice(delta=MockOpenAIDelta(content="partial"))]
        )
        text_events = StreamNormalizer.from_openai_chunk(text_chunk)
        error_event = StreamNormalizer.error_event("openai", "server_error")

        all_events = text_events + [error_event]

        # The last event should be the error
        assert all_events[-1].type == "error"
        # No done event should follow an error
        done_events = [e for e in all_events if e.type == "done"]
        assert len(done_events) == 0
