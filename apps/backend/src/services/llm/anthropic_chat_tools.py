"""Anthropic agentic chat with tools (streaming + manual tool loop).

Implements the ``ChatWithToolsProvider`` protocol using the Anthropic Messages API
via the ``anthropic`` SDK. Handles tool calling, streaming, error mapping, and
disconnect resilience.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import anthropic

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import (
    ChatCapability,
    StructuredOutputCapability,
    VisionCapability,
)
from src.services.llm.errors import AnthropicError
from src.services.llm.prompts import build_personalized_system_instruction
from src.services.llm.streaming import (
    StreamConsumerDisconnected,
    is_websocket_consumer_disconnect,
)
from src.services.llm.tool_normalizer import ToolNormalizer

logger = logging.getLogger(__name__)

# Maximum number of tool-call loop iterations before forced termination.
_MAX_TOOL_ITERATIONS = 6

# Default timeout for Anthropic API calls (seconds).
_DEFAULT_TIMEOUT = 60.0

# Maximum tokens for Anthropic responses.
_DEFAULT_MAX_TOKENS = 4096


def _map_anthropic_error(exc: Exception, model: str) -> AnthropicError:
    """Map an Anthropic SDK exception to a structured AnthropicError.

    Categories:
        - rate_limit: 429 Too Many Requests
        - auth: 401 Unauthorized / 403 Forbidden
        - invalid_request: 400 Bad Request
        - server_error: 500+ / timeouts
        - overloaded: 529 Overloaded
        - unknown: anything else
    """
    status_code: int | None = None
    category = "unknown"
    retriable = False
    message = str(exc)

    if isinstance(exc, anthropic.RateLimitError):
        status_code = getattr(exc, "status_code", 429)
        category = "rate_limit"
        retriable = True
    elif isinstance(exc, anthropic.AuthenticationError):
        status_code = getattr(exc, "status_code", 401)
        category = "auth"
        retriable = False
    elif isinstance(exc, anthropic.PermissionDeniedError):
        status_code = getattr(exc, "status_code", 403)
        category = "auth"
        retriable = False
    elif isinstance(exc, anthropic.BadRequestError):
        status_code = getattr(exc, "status_code", 400)
        category = "invalid_request"
        retriable = False
    elif isinstance(exc, anthropic.NotFoundError):
        status_code = getattr(exc, "status_code", 404)
        category = "invalid_request"
        retriable = False
    elif isinstance(exc, anthropic.UnprocessableEntityError):
        status_code = getattr(exc, "status_code", 422)
        category = "invalid_request"
        retriable = False
    elif isinstance(exc, anthropic.InternalServerError):
        status_code = getattr(exc, "status_code", 500)
        category = "server_error"
        retriable = True
    elif isinstance(exc, anthropic.APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code == 529:
            category = "overloaded"
            retriable = True
        elif status_code and status_code >= 500:
            category = "server_error"
            retriable = True
        else:
            category = "unknown"
            retriable = False
    elif isinstance(exc, anthropic.APITimeoutError):
        status_code = None
        category = "server_error"
        retriable = True
        message = f"Anthropic API timeout after {_DEFAULT_TIMEOUT}s: {exc}"
    elif isinstance(exc, anthropic.APIConnectionError):
        status_code = None
        category = "server_error"
        retriable = True
        message = f"Anthropic API connection error: {exc}"
    else:
        category = "unknown"
        retriable = False

    return AnthropicError(
        model=model,
        status_code=status_code,
        category=category,
        message=message,
        retriable=retriable,
    )


def _convert_history_to_anthropic(
    history: list,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert internal history format to Anthropic messages format.

    Anthropic requires:
    - System message as a separate top-level parameter (not in messages array)
    - Messages array with alternating user/assistant roles
    - Content can be a string or list of content blocks

    Returns:
        Tuple of (system_prompt_or_None, messages_list)
    """
    system_prompt: str | None = None
    messages: list[dict[str, Any]] = []

    for msg in history:
        if isinstance(msg, dict):
            role = msg.get("role", "user")

            # Extract system message
            if role == "system":
                # System messages become the top-level system param
                parts = msg.get("parts", [])
                if parts:
                    if isinstance(parts[0], str):
                        system_prompt = parts[0]
                    elif isinstance(parts[0], dict) and "text" in parts[0]:
                        system_prompt = parts[0]["text"]
                elif "content" in msg:
                    system_prompt = msg["content"] if isinstance(msg["content"], str) else ""
                continue

            # Map role
            anthropic_role = "assistant" if role == "model" or role == "assistant" else "user"

            # Extract content
            content: str | list[dict[str, Any]] = ""
            if "parts" in msg:
                parts = msg["parts"]
                if len(parts) == 1 and isinstance(parts[0], str):
                    content = parts[0]
                elif len(parts) == 1 and isinstance(parts[0], dict) and "text" in parts[0]:
                    content = parts[0]["text"]
                else:
                    # Multi-part content (text + images, function calls, etc.)
                    content_blocks: list[dict[str, Any]] = []
                    for part in parts:
                        if isinstance(part, str):
                            content_blocks.append({"type": "text", "text": part})
                        elif isinstance(part, dict):
                            if "text" in part:
                                content_blocks.append({"type": "text", "text": part["text"]})
                            elif "function_call" in part:
                                # Convert function call to tool_use block
                                fc = part["function_call"]
                                content_blocks.append(
                                    {
                                        "type": "tool_use",
                                        "id": fc.get("id", f"toolu_{id(fc)}"),
                                        "name": fc.get("name", ""),
                                        "input": fc.get("args", {}),
                                    }
                                )
                            elif "function_response" in part:
                                # Convert function response to tool_result block
                                fr = part["function_response"]
                                content_blocks.append(
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": fr.get("id", f"toolu_{id(fr)}"),
                                        "content": json.dumps(fr.get("response", {})),
                                    }
                                )
                            elif "inline_data" in part:
                                # Image data
                                img = part["inline_data"]
                                content_blocks.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": img.get("mime_type", "image/jpeg"),
                                            "data": img.get("data", b""),
                                        },
                                    }
                                )
                    content = content_blocks if content_blocks else ""
            elif "content" in msg:
                content = msg["content"]

            if content:
                messages.append({"role": anthropic_role, "content": content})

    return system_prompt, messages


class AnthropicChatToolsAdapter(BaseProviderAdapter):
    """Anthropic implementation of ChatWithToolsProvider.

    Uses the Anthropic Messages API with tool calling support. System messages
    are passed as a top-level parameter (Anthropic-specific). Handles streaming,
    tool call loops, error mapping, and disconnect resilience.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout,
        )
        self._tool_normalizer = ToolNormalizer()

    @property
    def model_id(self) -> str:
        return self._model

    def supported_capabilities(self) -> set[type]:
        # Anthropic does NOT offer embeddings
        return {ChatCapability, VisionCapability, StructuredOutputCapability}

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
        """Stream-capable chat with function calling via Anthropic Messages API.

        Returns a tuple of (response_text, usage_dict, actions, query_rows).
        """
        from src.services.chat_tool_arg_enrichment import (
            enrich_tool_args_for_llm,
            merge_successful_tool_result_into_created_ids,
        )
        from src.services.skills.handlers import handle_tool_call
        from src.services.skills import skill_registry
        from src.services.llm.context import (
            build_enhanced_chat_user_message,
            map_tool_to_action_type,
        )

        request_start = time.perf_counter()
        request_id = f"anthropic_{int(request_start * 1000)}"
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

            # Get tool definitions and convert to Anthropic format
            raw_tools = skill_registry.get_all_tools_legacy_format()
            all_declarations: list[dict[str, Any]] = []
            for tool_group in raw_tools:
                if isinstance(tool_group, dict) and "function_declarations" in tool_group:
                    all_declarations.extend(tool_group["function_declarations"])

            # Convert to internal ToolDefinition format, then to Anthropic format
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

            anthropic_tools = (
                self._tool_normalizer.to_anthropic(tool_definitions) if tool_definitions else []
            )

            # Build enhanced message with context
            enhanced_message_text = build_enhanced_chat_user_message(user_message, context)

            # Convert history to Anthropic format
            history_system, history_messages = _convert_history_to_anthropic(history)

            # Use the personalized system instruction (override any from history)
            system_text = system_instruction

            # Build the user message content (potentially multimodal)
            user_content: str | list[dict[str, Any]] = enhanced_message_text
            if image_url:
                user_content = [
                    {"type": "text", "text": enhanced_message_text},
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_url,
                        },
                    },
                ]

            # Append the current user message
            messages = list(history_messages)
            messages.append({"role": "user", "content": user_content})

            # Tool call loop
            iteration = 0
            tool_created_ids: dict[str, Any] = {}

            while iteration < _MAX_TOOL_ITERATIONS:
                iteration += 1

                # Build API call kwargs
                api_kwargs: dict[str, Any] = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": system_text,
                    "messages": messages,
                }
                if anthropic_tools:
                    api_kwargs["tools"] = anthropic_tools

                llm_start = time.perf_counter()

                if stream_callback:
                    # Streaming path
                    streamed_text, tool_use_blocks, usage = await self._stream_response(
                        api_kwargs, stream_callback, request_id, iteration
                    )
                else:
                    # Non-streaming path
                    streamed_text, tool_use_blocks, usage = await self._non_stream_response(
                        api_kwargs
                    )

                total_llm_time += time.perf_counter() - llm_start

                # Track token usage
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)

                # Accumulate text
                if streamed_text:
                    if final_text and not final_text.endswith(("\n", " ")):
                        final_text += " "
                    final_text += streamed_text

                # Check for tool calls
                if not tool_use_blocks:
                    # No tool calls - final turn
                    logger.info(
                        "[%s] iteration %d completed with no tools",
                        request_id,
                        iteration,
                    )
                    break

                # Execute tool calls
                tool_start = time.perf_counter()

                # Build assistant message with tool_use blocks for context
                assistant_content: list[dict[str, Any]] = []
                if streamed_text:
                    assistant_content.append({"type": "text", "text": streamed_text})
                for block in tool_use_blocks:
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": block["input"],
                        }
                    )
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools and build tool_result messages
                tool_result_blocks: list[dict[str, Any]] = []

                async def _execute_tool(block: dict[str, Any]):
                    tool_name = block["name"]
                    tool_args = dict(block["input"]) if block["input"] else {}
                    logger.debug("Executing tool: %s with args: %s", tool_name, tool_args)
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
                        return block["id"], tool_name, tool_args, tool_result, None
                    except Exception as e:
                        logger.error("Tool execution error for %s: %s", tool_name, e)
                        return (
                            block["id"],
                            tool_name,
                            tool_args,
                            {"error": str(e)},
                            str(e),
                        )

                # Execute tools (parallel for independent, sequential for dependent)
                def _has_dependency_placeholders(value) -> bool:
                    if isinstance(value, str):
                        return "$" in value
                    if isinstance(value, dict):
                        return any(_has_dependency_placeholders(v) for v in value.values())
                    if isinstance(value, list):
                        return any(_has_dependency_placeholders(v) for v in value)
                    return False

                independent_blocks = []
                dependent_blocks = []
                for block in tool_use_blocks:
                    if _has_dependency_placeholders(block.get("input", {})):
                        dependent_blocks.append(block)
                    else:
                        independent_blocks.append(block)

                execution_results = []
                if independent_blocks:
                    ind_results = await asyncio.gather(
                        *[_execute_tool(block) for block in independent_blocks]
                    )
                    execution_results.extend(ind_results)
                    for (
                        _call_id,
                        tool_name,
                        _tool_args,
                        tool_result,
                        tool_error,
                    ) in ind_results:
                        if tool_error is None:
                            merge_successful_tool_result_into_created_ids(
                                tool_created_ids, tool_name, tool_result
                            )

                for block in dependent_blocks:
                    dep_result = await _execute_tool(block)
                    execution_results.append(dep_result)
                    _call_id, tool_name, _tool_args, tool_result, tool_error = dep_result
                    if tool_error is None:
                        merge_successful_tool_result_into_created_ids(
                            tool_created_ids, tool_name, tool_result
                        )

                total_tool_time += time.perf_counter() - tool_start

                # Build tool results and track actions/queries
                for (
                    call_id,
                    tool_name,
                    tool_args,
                    tool_result,
                    tool_error,
                ) in execution_results:
                    if tool_error is None:
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
                                    "type": map_tool_to_action_type(tool_name),
                                    "data": tool_args,
                                    "result": tool_result,
                                }
                            )

                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": json.dumps(tool_result),
                        }
                    )

                # Add tool results as a user message (Anthropic format)
                messages.append({"role": "user", "content": tool_result_blocks})

            else:
                # Max iterations reached
                if not final_text:
                    final_text = (
                        "I encountered an issue processing your request. " "Please try again."
                    )

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

        except StreamConsumerDisconnected as disc:
            # Client disconnected during streaming - preserve partial response
            partial = disc.partial_turn_text or ""
            if partial:
                if final_text and not final_text.endswith(("\n", " ")):
                    final_text += " "
                final_text += partial
            logger.info(
                "[%s] client disconnected during stream",
                request_id,
            )
            return (
                final_text,
                {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "model_name": self._model,
                },
                executed_actions,
                query_results,
            )
        except anthropic.APIError as e:
            raise _map_anthropic_error(e, self._model) from e
        except Exception as e:
            if isinstance(e, AnthropicError):
                raise
            if is_websocket_consumer_disconnect(e):
                logger.info("[%s] client disconnected: %s", request_id, e)
                return (
                    final_text,
                    {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "model_name": self._model,
                    },
                    executed_actions,
                    query_results,
                )
            raise _map_anthropic_error(e, self._model) from e

    async def _stream_response(
        self,
        api_kwargs: dict[str, Any],
        stream_callback: Any,
        request_id: str,
        iteration: int,
    ) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
        """Stream an Anthropic response, calling stream_callback for text deltas.

        Returns:
            Tuple of (accumulated_text, tool_use_blocks, usage_dict)
        """
        streamed_text_parts: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []
        current_tool_block: dict[str, Any] | None = None
        current_tool_json_parts: list[str] = []
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        try:
            async with self._client.messages.stream(**api_kwargs) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)

                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", None) == "tool_use":
                            current_tool_block = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "input": {},
                            }
                            current_tool_json_parts = []

                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta:
                            delta_type = getattr(delta, "type", None)
                            if delta_type == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    streamed_text_parts.append(text)
                                    try:
                                        await stream_callback(text, False)
                                    except BaseException as stream_err:
                                        if is_websocket_consumer_disconnect(stream_err):
                                            raise StreamConsumerDisconnected(
                                                "".join(streamed_text_parts)
                                            ) from stream_err
                                        raise
                            elif delta_type == "input_json_delta":
                                # Accumulate tool input JSON fragments
                                partial_json = getattr(delta, "partial_json", "")
                                if partial_json and current_tool_block is not None:
                                    current_tool_json_parts.append(partial_json)

                    elif event_type == "content_block_stop":
                        if current_tool_block is not None:
                            # Parse accumulated JSON for tool input
                            raw_json = "".join(current_tool_json_parts)
                            if raw_json:
                                try:
                                    current_tool_block["input"] = json.loads(raw_json)
                                except json.JSONDecodeError:
                                    current_tool_block["input"] = {}
                            tool_use_blocks.append(current_tool_block)
                            current_tool_block = None
                            current_tool_json_parts = []

                    elif event_type == "message_start":
                        msg = getattr(event, "message", None)
                        if msg:
                            msg_usage = getattr(msg, "usage", None)
                            if msg_usage:
                                usage["input_tokens"] += getattr(msg_usage, "input_tokens", 0)

                    elif event_type == "message_delta":
                        delta_usage = getattr(event, "usage", None)
                        if delta_usage:
                            usage["output_tokens"] += getattr(delta_usage, "output_tokens", 0)

            # Send done signal
            if streamed_text_parts:
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
        except anthropic.APIError:
            raise
        except BaseException as e:
            if is_websocket_consumer_disconnect(e):
                raise StreamConsumerDisconnected("".join(streamed_text_parts)) from e
            raise

        return "".join(streamed_text_parts), tool_use_blocks, usage

    async def _non_stream_response(
        self,
        api_kwargs: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
        """Make a non-streaming Anthropic API call.

        Returns:
            Tuple of (text, tool_use_blocks, usage_dict)
        """
        response = await self._client.messages.create(**api_kwargs)

        text_parts: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_use_blocks.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        usage: dict[str, int] = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
        }

        return "".join(text_parts), tool_use_blocks, usage
