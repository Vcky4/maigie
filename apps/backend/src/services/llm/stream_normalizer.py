"""Stream normalizer utilities for converting provider-specific chunks to StreamEvent.

Provides static methods to convert OpenAI ChatCompletionChunks, Anthropic streaming
events, and Gemini streaming chunks into the unified StreamEvent format. Used by
each provider adapter during streaming to emit a consistent event sequence regardless
of the underlying provider's native format.

StreamEvent types:
    - "text_delta": Contains non-empty text content from the provider.
    - "tool_call_delta": Contains partial tool call data (ID, name, arguments fragment).
    - "done": Signals successful stream completion (exactly one per stream).
    - "error": Signals a provider error that terminates the stream.
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.llm.types import StreamEvent, ToolCallDelta

logger = logging.getLogger(__name__)


class StreamNormalizer:
    """Utilities for converting provider-specific stream chunks to StreamEvent.

    All methods are static — no instance state is needed. Each conversion method
    returns a list of StreamEvent objects (possibly empty if the chunk contains
    no actionable data).
    """

    @staticmethod
    def from_openai_chunk(chunk: Any) -> list[StreamEvent]:
        """Convert an OpenAI ChatCompletionChunk to a list of StreamEvent objects.

        Handles:
            - Text content deltas → "text_delta" events (non-empty text only)
            - Tool call deltas → "tool_call_delta" events (with ID/name/arguments)
            - Finish reason → "done" event when stream completes successfully

        Args:
            chunk: An OpenAI ChatCompletionChunk object from the streaming API.

        Returns:
            List of StreamEvent objects extracted from this chunk.
        """
        events: list[StreamEvent] = []

        choices = getattr(chunk, "choices", None)
        if not choices:
            return events

        for choice in choices:
            delta = getattr(choice, "delta", None)

            if delta:
                # Handle text content
                content = getattr(delta, "content", None)
                if content:  # Only emit for non-empty text
                    events.append(StreamEvent(type="text_delta", text=content))

                # Handle tool call deltas
                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        tc_id = getattr(tc, "id", None)
                        tc_function = getattr(tc, "function", None)
                        tc_name: str | None = None
                        tc_arguments: str | None = None

                        if tc_function:
                            tc_name = getattr(tc_function, "name", None) or None
                            tc_arguments = getattr(tc_function, "arguments", None) or None

                        # Only emit if there's meaningful tool call data
                        if tc_id or tc_name or tc_arguments:
                            events.append(
                                StreamEvent(
                                    type="tool_call_delta",
                                    tool_call=ToolCallDelta(
                                        id=tc_id,
                                        name=tc_name,
                                        arguments_fragment=tc_arguments,
                                    ),
                                )
                            )

            # Handle stream completion
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason:
                events.append(StreamEvent(type="done", done=True))

        return events

    @staticmethod
    def from_anthropic_event(event: Any) -> list[StreamEvent]:
        """Convert an Anthropic streaming event to a list of StreamEvent objects.

        Anthropic uses a different event structure than OpenAI:
            - "content_block_start" with type "tool_use" → tool_call_delta (ID + name)
            - "content_block_delta" with "text_delta" → text_delta events
            - "content_block_delta" with "input_json_delta" → tool_call_delta (arguments)
            - "message_stop" → done event

        Args:
            event: An Anthropic streaming event object.

        Returns:
            List of StreamEvent objects extracted from this event.
        """
        events: list[StreamEvent] = []

        event_type = getattr(event, "type", None)

        if event_type == "content_block_start":
            # Check if this is a tool_use block starting
            content_block = getattr(event, "content_block", None)
            if content_block:
                block_type = getattr(content_block, "type", None)
                if block_type == "tool_use":
                    block_id = getattr(content_block, "id", None)
                    block_name = getattr(content_block, "name", None)
                    if block_id or block_name:
                        events.append(
                            StreamEvent(
                                type="tool_call_delta",
                                tool_call=ToolCallDelta(
                                    id=block_id,
                                    name=block_name,
                                    arguments_fragment=None,
                                ),
                            )
                        )

        elif event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            if delta:
                delta_type = getattr(delta, "type", None)

                if delta_type == "text_delta":
                    text = getattr(delta, "text", None)
                    if text:  # Only emit for non-empty text
                        events.append(StreamEvent(type="text_delta", text=text))

                elif delta_type == "input_json_delta":
                    partial_json = getattr(delta, "partial_json", None)
                    if partial_json:  # Only emit for non-empty arguments
                        events.append(
                            StreamEvent(
                                type="tool_call_delta",
                                tool_call=ToolCallDelta(
                                    id=None,
                                    name=None,
                                    arguments_fragment=partial_json,
                                ),
                            )
                        )

        elif event_type == "message_stop":
            events.append(StreamEvent(type="done", done=True))

        elif event_type == "message_delta":
            # Check for stop_reason in message_delta (alternative completion signal)
            delta = getattr(event, "delta", None)
            if delta:
                stop_reason = getattr(delta, "stop_reason", None)
                if stop_reason:
                    events.append(StreamEvent(type="done", done=True))

        return events

    @staticmethod
    def from_gemini_chunk(chunk: Any) -> list[StreamEvent]:
        """Convert a Gemini streaming chunk to a list of StreamEvent objects.

        Gemini chunks contain candidates with content parts. Each part can be
        text or a function_call. The chunk may also signal completion via
        finish_reason on the candidate.

        Args:
            chunk: A Gemini GenerateContentResponse chunk from streaming.

        Returns:
            List of StreamEvent objects extracted from this chunk.
        """
        events: list[StreamEvent] = []

        # Gemini chunks have a `candidates` list
        candidates = getattr(chunk, "candidates", None)
        if not candidates:
            # Some Gemini responses use `.text` directly
            text = getattr(chunk, "text", None)
            if text:
                events.append(StreamEvent(type="text_delta", text=text))
            return events

        for candidate in candidates:
            content = getattr(candidate, "content", None)
            finish_reason = getattr(candidate, "finish_reason", None)

            if content:
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    # Handle text parts
                    text = getattr(part, "text", None)
                    if text:  # Only emit for non-empty text
                        events.append(StreamEvent(type="text_delta", text=text))

                    # Handle function call parts
                    function_call = getattr(part, "function_call", None)
                    if function_call:
                        fc_name = getattr(function_call, "name", None)
                        fc_args = getattr(function_call, "args", None)

                        # Convert args to JSON string fragment if present
                        arguments_fragment: str | None = None
                        if fc_args is not None:
                            import json

                            try:
                                if isinstance(fc_args, dict):
                                    arguments_fragment = json.dumps(fc_args)
                                elif isinstance(fc_args, str):
                                    arguments_fragment = fc_args
                                else:
                                    arguments_fragment = str(fc_args)
                            except (TypeError, ValueError):
                                arguments_fragment = str(fc_args)

                        # Generate a synthetic ID for Gemini (it doesn't provide one)
                        # Use the function name as a pseudo-ID since Gemini doesn't
                        # assign call IDs in the same way as OpenAI/Anthropic
                        tc_id = f"gemini_call_{fc_name}" if fc_name else None

                        if fc_name or arguments_fragment:
                            events.append(
                                StreamEvent(
                                    type="tool_call_delta",
                                    tool_call=ToolCallDelta(
                                        id=tc_id,
                                        name=fc_name,
                                        arguments_fragment=arguments_fragment,
                                    ),
                                )
                            )

            # Handle stream completion
            # Gemini finish_reason can be an enum or int; check for "STOP" or equivalent
            if finish_reason is not None:
                # Gemini uses enum values like STOP (1), MAX_TOKENS (2), etc.
                # We treat STOP as successful completion
                fr_value = finish_reason
                if hasattr(finish_reason, "name"):
                    fr_value = finish_reason.name
                elif hasattr(finish_reason, "value"):
                    fr_value = finish_reason.value

                # Convert to string for comparison
                fr_str = str(fr_value).upper()
                if fr_str in ("STOP", "1", "END_TURN"):
                    events.append(StreamEvent(type="done", done=True))

        return events

    @staticmethod
    def error_event(provider: str, category: str) -> StreamEvent:
        """Create an error StreamEvent that terminates the stream.

        Error events signal that the stream was interrupted by a provider error.
        No further events should be emitted after an error event.

        Args:
            provider: The provider name (e.g., "openai", "anthropic", "gemini").
            category: The error category (e.g., "rate_limit", "server_error").

        Returns:
            A StreamEvent with type "error" containing the provider and category.
        """
        return StreamEvent(type="error", text=f"{provider}:{category}")
