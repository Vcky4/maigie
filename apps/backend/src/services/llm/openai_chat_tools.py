"""OpenAI agentic chat with tools (streaming + manual tool loop).

Implements the ``ChatWithToolsProvider`` protocol using the OpenAI Python SDK
(``openai.AsyncOpenAI``). Follows the same contract as ``GeminiChatToolsAdapter``
so that the router can use either adapter interchangeably.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import openai

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import (
    ChatCapability,
    EmbeddingCapability,
    StructuredOutputCapability,
    VisionCapability,
)
from src.services.llm.errors import OpenAIError
from src.services.llm.prompts import build_personalized_system_instruction
from src.services.llm.streaming import StreamConsumerDisconnected, is_websocket_consumer_disconnect
from src.services.llm.tool_normalizer import ToolNormalizer
from src.services.llm.types import StreamEvent, ToolCallDelta

logger = logging.getLogger(__name__)

# Maximum number of tool-call loop iterations before forced termination.
_MAX_TOOL_ITERATIONS = 6

# Default timeout for OpenAI API calls (seconds).
_DEFAULT_TIMEOUT = 60.0


def _map_openai_error(exc: openai.OpenAIError, model: str) -> OpenAIError:
    """Map an OpenAI SDK exception to our structured ``OpenAIError``.

    Categories:
        - rate_limit: 429
        - auth: 401, 403
        - invalid_request: 400, 404, 422
        - server_error: 500, 502, 503 (non-overloaded), timeout
        - overloaded: 503 with overloaded signal, 529
        - unknown: anything else
    """
    status_code: int | None = None
    category = "unknown"
    retriable = False

    if isinstance(exc, openai.APIStatusError):
        status_code = exc.status_code

        if status_code == 429:
            category = "rate_limit"
            retriable = True
        elif status_code in (401, 403):
            category = "auth"
            retriable = False
        elif status_code in (400, 404, 422):
            category = "invalid_request"
            retriable = False
        elif status_code == 529:
            category = "overloaded"
            retriable = True
        elif status_code == 503:
            # 503 can be overloaded or server_error; check message
            msg_lower = str(exc).lower()
            if "overloaded" in msg_lower or "capacity" in msg_lower:
                category = "overloaded"
            else:
                category = "server_error"
            retriable = True
        elif status_code and status_code >= 500:
            category = "server_error"
            retriable = True
        else:
            category = "unknown"
            retriable = False

    elif isinstance(exc, openai.APITimeoutError):
        category = "server_error"
        retriable = True

    elif isinstance(exc, openai.APIConnectionError):
        category = "server_error"
        retriable = True

    elif isinstance(exc, openai.AuthenticationError):
        status_code = 401
        category = "auth"
        retriable = False

    elif isinstance(exc, openai.RateLimitError):
        status_code = 429
        category = "rate_limit"
        retriable = True

    return OpenAIError(
        model=model,
        status_code=status_code,
        category=category,
        message=str(exc),
        retriable=retriable,
    )


def _history_to_openai_messages(
    history: list,
    system_instruction: str,
) -> list[dict[str, Any]]:
    """Convert Maigie chat history to OpenAI message format.

    The history comes in as a list of dicts with ``role`` and ``parts`` keys
    (Gemini-style format from the frontend/WebSocket layer). We convert to
    OpenAI's ``{"role": ..., "content": ...}`` format.

    The system instruction is prepended as the first message with role "system".
    """
    messages: list[dict[str, Any]] = []

    # System message first
    messages.append({"role": "system", "content": system_instruction})

    for msg in history:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            # Map Gemini roles to OpenAI roles
            if role == "model":
                role = "assistant"

            parts = msg.get("parts", [])
            # Combine text parts into a single content string
            text_parts = []
            for part in parts:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    if "text" in part:
                        text_parts.append(part["text"])
                    # Skip inline_data (images in history) for now;
                    # OpenAI handles images differently via content array

            content = "\n".join(text_parts) if text_parts else ""
            if content:
                messages.append({"role": role, "content": content})

    return messages


class OpenAIChatToolsAdapter(BaseProviderAdapter):
    """OpenAI implementation of :class:`ChatWithToolsProvider`.

    Uses the ``openai.AsyncOpenAI`` client for chat completions with
    function calling and streaming support.
    """

    provider_name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            timeout=timeout,
        )
        self._tool_normalizer = ToolNormalizer()
        self._timeout = timeout

    @property
    def model_id(self) -> str:
        return self._model

    def supported_capabilities(self) -> set[type]:
        caps: set[type] = {ChatCapability, VisionCapability, StructuredOutputCapability}
        if "embedding" in self._model:
            caps.add(EmbeddingCapability)
        return caps

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
        """Stream-capable chat with function calling via OpenAI.

        Returns a tuple of (response_text, usage_dict, actions, query_rows).
        """
        from src.services.chat_tool_arg_enrichment import (
            enrich_tool_args_for_llm,
            merge_successful_tool_result_into_created_ids,
        )
        from src.services.gemini_tool_handlers import handle_tool_call
        from src.services.gemini_tools import get_all_tools
        from src.services.llm_chat_context import (
            build_enhanced_chat_user_message,
            map_gemini_tool_to_action_type,
        )

        request_start = time.perf_counter()
        request_id = f"openai_{int(request_start * 1000)}"
        executed_actions: list[dict[str, Any]] = []
        query_results: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        final_text = ""
        total_llm_time = 0.0
        total_tool_time = 0.0

        try:
            # Build system instruction
            system_instruction = build_personalized_system_instruction(user_name)

            # Build enhanced user message with context
            enhanced_message = build_enhanced_chat_user_message(user_message, context)

            # Convert history to OpenAI format
            messages = _history_to_openai_messages(history, system_instruction)

            # Add the current user message
            if image_url:
                # Multimodal message with image
                user_content: list[dict[str, Any]] = [
                    {"type": "text", "text": enhanced_message},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": enhanced_message})

            # Get tool definitions and convert to OpenAI format
            raw_tools = get_all_tools()
            all_declarations = []
            for tool_group in raw_tools:
                if isinstance(tool_group, dict):
                    decls = tool_group.get("function_declarations") or tool_group.get(
                        "functionDeclarations"
                    )
                    if decls:
                        all_declarations.extend(decls)

            # Convert raw declarations to ToolDefinition objects for the normalizer
            from src.services.llm.types import ToolDefinition

            tool_definitions = []
            for decl in all_declarations:
                td = ToolDefinition(
                    name=decl.get("name", ""),
                    description=decl.get("description", ""),
                    parameters=decl.get("parameters", {}),
                    required=decl.get("parameters", {}).get("required", []),
                )
                tool_definitions.append(td)

            # Convert to OpenAI format
            openai_tools = self._tool_normalizer.to_openai(tool_definitions) if tool_definitions else None

            # Tool call loop
            iteration = 0
            tool_created_ids: dict[str, Any] = {}

            while iteration < _MAX_TOOL_ITERATIONS:
                iteration += 1

                # Make the API call
                llm_start = time.perf_counter()
                try:
                    if stream_callback:
                        # Streaming mode
                        streamed_text, tool_calls_raw, usage = await self._stream_response(
                            messages=messages,
                            tools=openai_tools,
                            stream_callback=stream_callback,
                        )
                    else:
                        # Non-streaming mode
                        streamed_text, tool_calls_raw, usage = await self._non_stream_response(
                            messages=messages,
                            tools=openai_tools,
                        )
                except StreamConsumerDisconnected as disc:
                    total_llm_time += time.perf_counter() - llm_start
                    # Preserve partial response
                    partial = disc.partial_turn_text
                    if partial:
                        if final_text and not final_text.endswith(("\n", " ")):
                            final_text += " "
                        final_text += partial
                    logger.info(
                        "[%s] client disconnected during stream (iteration %s)",
                        request_id,
                        iteration,
                    )
                    break
                except openai.OpenAIError as api_err:
                    raise _map_openai_error(api_err, self._model) from api_err

                total_llm_time += time.perf_counter() - llm_start

                # Track token usage
                if usage:
                    total_input_tokens += usage.get("prompt_tokens", 0)
                    total_output_tokens += usage.get("completion_tokens", 0)

                # Accumulate text
                if streamed_text:
                    if final_text and not final_text.endswith(("\n", " ")):
                        final_text += " "
                    final_text += streamed_text

                # Check for tool calls
                if not tool_calls_raw:
                    # No tool calls — final turn
                    logger.debug(
                        "[%s] iteration %s completed with no tools", request_id, iteration
                    )
                    break

                # Normalize tool calls
                normalized_calls = self._tool_normalizer.normalize_tool_calls_openai(
                    tool_calls_raw
                )

                if not normalized_calls:
                    break

                # Execute tool calls
                tool_start = time.perf_counter()

                # Add assistant message with tool calls to conversation
                assistant_tool_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": streamed_text or None,
                    "tool_calls": [
                        {
                            "id": tc.id if hasattr(tc, "id") else tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.function.name
                                if hasattr(tc, "function")
                                else tc.get("function", {}).get("name", ""),
                                "arguments": tc.function.arguments
                                if hasattr(tc, "function")
                                else tc.get("function", {}).get("arguments", "{}"),
                            },
                        }
                        for tc in tool_calls_raw
                    ],
                }
                messages.append(assistant_tool_msg)

                # Execute each tool and add results
                for call in normalized_calls:
                    tool_name = call.name
                    tool_args = call.arguments

                    try:
                        tool_args = await enrich_tool_args_for_llm(
                            tool_name,
                            tool_args,
                            context=context,
                            created_ids=tool_created_ids or None,
                            user_id=user_id,
                        )
                        tool_result = await handle_tool_call(
                            tool_name=tool_name,
                            args=tool_args,
                            user_id=user_id,
                            context=context,
                            progress_callback=progress_callback,
                        )
                    except Exception as e:
                        logger.error("Tool execution error for %s: %s", tool_name, e)
                        tool_result = {"error": str(e)}

                    # Track successful tool results for dependency resolution
                    if "error" not in tool_result:
                        merge_successful_tool_result_into_created_ids(
                            tool_created_ids, tool_name, tool_result
                        )

                    # Track query results
                    if tool_name.startswith("get_user_"):
                        query_results.append(
                            {
                                "tool_name": tool_name,
                                "result": tool_result,
                                "component_type": tool_result.get("_component_type"),
                                "query_type": tool_result.get("_query_type"),
                                "data": (
                                    tool_result.get("courses")
                                    or tool_result.get("goals")
                                    or tool_result.get("schedules")
                                    or tool_result.get("notes")
                                    or tool_result.get("resources")
                                ),
                            }
                        )

                    # Track executed actions
                    if tool_name.startswith("create_") or tool_name in [
                        "recommend_resources",
                        "retake_note",
                        "add_summary_to_note",
                        "add_tags_to_note",
                        "complete_review",
                        "update_course_outline",
                    ]:
                        executed_actions.append(
                            {
                                "type": map_gemini_tool_to_action_type(tool_name),
                                "data": tool_args,
                                "result": tool_result,
                            }
                        )

                    # Add tool result message to conversation
                    tool_result_msg = self._tool_normalizer.to_tool_result_openai(
                        call.id, tool_result
                    )
                    messages.append(tool_result_msg)

                total_tool_time += time.perf_counter() - tool_start

                # Clear tools for next iteration text (don't pass tools on final response)
                # Actually, keep tools available so the model can call more if needed
            else:
                # Max iterations reached without a final text-only response
                if not final_text:
                    final_text = (
                        "I encountered an issue processing your request. Please try again."
                    )

        except OpenAIError:
            # Re-raise our structured errors
            raise
        except openai.OpenAIError as api_err:
            raise _map_openai_error(api_err, self._model) from api_err
        except StreamConsumerDisconnected:
            # Already handled above, but catch at top level too
            pass
        except Exception as e:
            if is_websocket_consumer_disconnect(e):
                logger.info("[%s] client disconnected: %s", request_id, e)
            else:
                logger.error("[%s] unexpected error: %s", request_id, e)
                raise OpenAIError(
                    model=self._model,
                    status_code=None,
                    category="unknown",
                    message=f"Unexpected error: {e}",
                    retriable=False,
                ) from e

        usage_info = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model_name": self._model,
        }

        total_time = time.perf_counter() - request_start
        logger.info(
            "[%s] total=%.2fs llm=%.2fs tools=%.2fs iterations=%d",
            request_id,
            total_time,
            total_llm_time,
            total_tool_time,
            iteration,
        )

        return final_text, usage_info, executed_actions, query_results

    async def _stream_response(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream_callback: Any,
    ) -> tuple[str, list, dict[str, int] | None]:
        """Make a streaming API call and emit StreamEvents via the callback.

        Returns (accumulated_text, tool_calls_list, usage_dict).
        Raises StreamConsumerDisconnected if the client disconnects.
        """
        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            create_kwargs["tools"] = tools

        streamed_text_parts: list[str] = []
        # Accumulate tool calls by index
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] | None = None

        stream = await self._client.chat.completions.create(**create_kwargs)

        try:
            async for chunk in stream:
                # Handle usage in the final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                    }

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Handle text content
                if delta and delta.content:
                    text_fragment = delta.content
                    streamed_text_parts.append(text_fragment)

                    # Emit StreamEvent via callback
                    try:
                        await stream_callback(text_fragment, False)
                    except BaseException as stream_err:
                        if is_websocket_consumer_disconnect(stream_err):
                            raise StreamConsumerDisconnected(
                                "".join(streamed_text_parts)
                            ) from stream_err
                        raise

                # Handle tool call deltas
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {
                                "id": tc_delta.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }

                        entry = tool_calls_by_index[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["function"]["arguments"] += tc_delta.function.arguments

                # Check for finish
                if choice.finish_reason:
                    # Send done signal
                    if stream_callback and choice.finish_reason == "stop":
                        try:
                            await stream_callback("", True)
                        except BaseException as stream_err:
                            if is_websocket_consumer_disconnect(stream_err):
                                raise StreamConsumerDisconnected(
                                    "".join(streamed_text_parts)
                                ) from stream_err
                            raise

        except StreamConsumerDisconnected:
            raise
        except openai.OpenAIError:
            raise
        except BaseException as e:
            if is_websocket_consumer_disconnect(e):
                raise StreamConsumerDisconnected("".join(streamed_text_parts)) from e
            raise

        # Collect tool calls in order
        tool_calls_list = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]

        return "".join(streamed_text_parts), tool_calls_list, usage

    async def _non_stream_response(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[str, list, dict[str, int] | None]:
        """Make a non-streaming API call.

        Returns (response_text, tool_calls_list, usage_dict).
        """
        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**create_kwargs)

        # Extract text
        text = ""
        tool_calls_list: list = []
        usage: dict[str, int] | None = None

        if response.choices:
            choice = response.choices[0]
            message = choice.message

            if message.content:
                text = message.content

            if message.tool_calls:
                tool_calls_list = list(message.tool_calls)

        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
            }

        return text, tool_calls_list, usage
