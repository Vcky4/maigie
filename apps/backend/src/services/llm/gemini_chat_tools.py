"""Gemini agentic chat with tools (streaming + manual tool loop).

Moved from ``llm_service.GeminiService.get_chat_response_with_tools`` for Phase B adapter layout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.services.llm.base_adapter import BaseProviderAdapter
from src.services.llm.capabilities import ChatCapability, StructuredOutputCapability, VisionCapability
from src.services.llm.gemini_sdk import new_gemini_client, types as _types
from src.services.llm.prompts import build_personalized_system_instruction
from src.services.llm.streaming import StreamConsumerDisconnected, is_websocket_consumer_disconnect
from src.services.llm_chat_context import (
    build_enhanced_chat_user_message,
    map_gemini_tool_to_action_type,
)
from src.services.llm_registry import LlmTask, default_model_for, gemini_api_key

logger = logging.getLogger(__name__)


def _convert_proto_to_dict(obj):
    """Recursively convert protobuf objects to plain Python dicts/lists."""
    import json

    if obj is None:
        return None

    if hasattr(obj, "keys") and callable(obj.keys):
        return {k: _convert_proto_to_dict(v) for k, v in obj.items()}

    if hasattr(obj, "__iter__") and not isinstance(obj, str | bytes | dict):
        try:
            return [_convert_proto_to_dict(item) for item in obj]
        except TypeError:
            pass

    if isinstance(obj, str | int | float | bool):
        return obj

    if isinstance(obj, dict):
        return {k: _convert_proto_to_dict(v) for k, v in obj.items()}

    if isinstance(obj, list | tuple):
        return [_convert_proto_to_dict(item) for item in obj]

    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


async def run_gemini_chat_with_tools(
    *,
    history: list,
    user_message: str,
    context: dict | None,
    user_id: str | None,
    user_name: str | None,
    image_url: str | None,
    progress_callback: Any,
    stream_callback: Any,
    safety_settings: list[Any],
    model_id: str | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Gemini streaming chat with function calling; same contract as ``GeminiService``."""
    from src.services.chat_tool_arg_enrichment import (
        enrich_tool_args_for_llm,
        merge_successful_tool_result_into_created_ids,
    )
    from src.services.gemini_tool_handlers import handle_tool_call
    from src.services.gemini_tools import get_all_tools

    try:
        request_start = time.perf_counter()
        request_id = f"agentic_{int(request_start * 1000)}"
        executed_actions: list[dict[str, Any]] = []
        query_results: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        final_text = ""
        total_llm_time = 0.0
        total_tool_time = 0.0

        # Get tool definitions and convert to new SDK format
        raw_tools = get_all_tools()
        # raw_tools is [{"function_declarations": [...]}] (old format)
        # New SDK expects a list of types.Tool objects with UPPERCASE type strings
        all_declarations = []
        for tool_group in raw_tools:
            if isinstance(tool_group, dict) and "function_declarations" in tool_group:
                all_declarations.extend(tool_group["function_declarations"])

        def _uppercase_types(obj):
            """Recursively convert lowercase type strings to uppercase for new SDK."""
            if isinstance(obj, dict):
                result = {}
                for k, v in obj.items():
                    if k == "type" and isinstance(v, str):
                        result[k] = v.upper()
                    else:
                        result[k] = _uppercase_types(v)
                return result
            elif isinstance(obj, list):
                return [_uppercase_types(item) for item in obj]
            return obj

        all_declarations = [_uppercase_types(d) for d in all_declarations]
        tools = [_types.Tool(function_declarations=all_declarations)] if all_declarations else None

        # Build personalized system instruction with user's name
        system_instruction = build_personalized_system_instruction(user_name)

        # Create client
        client = new_gemini_client(gemini_api_key() or None)

        # Build enhanced message with context
        enhanced_message_text = build_enhanced_chat_user_message(user_message, context)

        # Helper to check if URL is an image
        def _is_image_url(url: str) -> bool:
            return (
                any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])
                or "image" in url.lower()
                or any(domain in url.lower() for domain in ["bunnycdn", "storage", "cdn"])
            )

        # Collect all image URLs to download (current message + history)
        image_urls_to_download = []
        if image_url:
            image_urls_to_download.append(image_url)

        # Scan history for image URLs
        history_image_positions = []  # [(msg_idx, part_idx, url), ...]
        for msg_idx, hist_msg in enumerate(history):
            if isinstance(hist_msg, dict) and "parts" in hist_msg:
                for part_idx, part in enumerate(hist_msg["parts"]):
                    if isinstance(part, str) and part.startswith(("http://", "https://")):
                        if _is_image_url(part):
                            history_image_positions.append((msg_idx, part_idx, part))
                            image_urls_to_download.append(part)

        # Download all images in parallel
        downloaded_images = {}  # url -> {"mime_type": ..., "data": ...}
        if image_urls_to_download:
            async with httpx.AsyncClient(timeout=15.0) as http_client:

                async def download_image(url: str):
                    try:
                        response = await http_client.get(url)
                        if response.status_code == 200:
                            return url, {
                                "mime_type": response.headers.get("content-type", "image/jpeg"),
                                "data": response.content,
                            }
                    except Exception as e:
                        print(f"⚠️ Failed to download image {url[:50]}...: {e}")
                    try:
                        from src.services.storage_service import storage_service as _storage

                        fb = await _storage.fetch_public_chat_image_bytes(url)
                        if fb:
                            data, raw_ct = fb
                            mt = (raw_ct or "").split(";", 1)[0].strip() or "image/jpeg"
                            return url, {"mime_type": mt, "data": data}
                    except Exception as e2:
                        print(f"⚠️ Storage fallback failed for {url[:50]}...: {e2}")
                    return url, None

                # Download all images in parallel
                results = await asyncio.gather(
                    *[download_image(url) for url in image_urls_to_download]
                )
                for url, img_data in results:
                    if img_data:
                        downloaded_images[url] = img_data
                        print(f"🖼️ Downloaded image: {url[:50]}...")

        # Prepare message content (multimodal if image_url provided)
        message_content = [_types.Part(text=enhanced_message_text)]
        if image_url and image_url in downloaded_images:
            img_data = downloaded_images[image_url]
            message_content = [
                _types.Part(text=enhanced_message_text),
                _types.Part(
                    inline_data=_types.Blob(mime_type=img_data["mime_type"], data=img_data["data"])
                ),
            ]
            print(f"🖼️ Including image in message: {image_url}")

        # Process history - replace image URLs with downloaded data
        processed_history = []
        for msg_idx, hist_msg in enumerate(history):
            if isinstance(hist_msg, dict) and "parts" in hist_msg:
                processed_parts = []
                for part_idx, part in enumerate(hist_msg["parts"]):
                    if isinstance(part, str):
                        if part.startswith(("http://", "https://")) and _is_image_url(part):
                            # Replace URL with downloaded image data
                            if part in downloaded_images:
                                img_data = downloaded_images[part]
                                processed_parts.append(
                                    {
                                        "inline_data": {
                                            "mime_type": img_data["mime_type"],
                                            "data": img_data["data"],
                                        }
                                    }
                                )
                            # Skip if download failed
                        else:
                            processed_parts.append({"text": part})
                    elif isinstance(part, dict):
                        # Function calls or other dicts. For safety, pass them in if they match schema
                        processed_parts.append(part)
                    else:
                        processed_parts.append(part)
                processed_history.append(
                    {"role": hist_msg.get("role", "user"), "parts": processed_parts}
                )
            else:
                processed_history.append(hist_msg)

        # Start chat session
        # Use the explicitly passed model_id, falling back to the task default
        resolved_model = model_id or default_model_for(LlmTask.CHAT_TOOLS_SESSION)
        chat = client.aio.chats.create(
            model=resolved_model,
            history=processed_history,
            config=_types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools,
                safety_settings=safety_settings,
                # Manual tool loop below; disable SDK automatic function calling to avoid
                # UNEXPECTED_TOOL_CALL / AFC state issues (see googleapis/python-genai#1818).
                automatic_function_calling=_types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        # Tool call loop
        max_iterations = 6  # Prevent infinite loops while reducing latency
        iteration = 0
        tool_results = []  # Initialize tool_results
        # IDs from successful tools this request (for $placeholders in dependent tool calls)
        tool_created_ids: dict[str, Any] = {}

        while iteration < max_iterations:
            iteration += 1

            # Send message (first iteration) or tool results (subsequent iterations)
            streamed_turn_text = ""

            async def _send_streaming_request(payload):
                # google-genai AsyncChat: await coroutine, then async-iterate chunks
                response_stream = await chat.send_message_stream(payload)
                last_response = None
                streamed_text_parts = []
                last_chunk_text = None
                streamed_function_calls = []

                async for chunk in response_stream:
                    last_response = chunk
                    try:
                        # In the new SDK, reading .text when a function call is present throws a ValueError
                        chunk_text = chunk.text
                    except ValueError:
                        chunk_text = None
                    except Exception:
                        chunk_text = None

                    # Extract function calls
                    if hasattr(chunk, "function_calls") and chunk.function_calls:
                        streamed_function_calls.extend(chunk.function_calls)
                    elif (
                        hasattr(chunk, "candidates")
                        and chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                    ):
                        for part in chunk.candidates[0].content.parts:
                            if hasattr(part, "function_call") and part.function_call:
                                streamed_function_calls.append(part.function_call)

                    if chunk_text:
                        text_delta = chunk_text
                        if last_chunk_text and chunk_text.startswith(last_chunk_text):
                            text_delta = chunk_text[len(last_chunk_text) :]
                        last_chunk_text = chunk_text

                        if text_delta:
                            streamed_text_parts.append(text_delta)
                            if stream_callback:
                                try:
                                    await stream_callback(text_delta, False)
                                except BaseException as stream_err:
                                    if is_websocket_consumer_disconnect(stream_err):
                                        raise StreamConsumerDisconnected(
                                            "".join(streamed_text_parts)
                                        ) from stream_err
                                    raise

                if stream_callback and last_chunk_text is not None:
                    try:
                        await stream_callback("", True)
                    except BaseException as stream_err:
                        if is_websocket_consumer_disconnect(stream_err):
                            raise StreamConsumerDisconnected(
                                "".join(streamed_text_parts)
                            ) from stream_err
                        raise

                return last_response, "".join(streamed_text_parts), streamed_function_calls

            stream_payload = message_content if iteration == 1 else tool_results
            llm_start = time.perf_counter()
            try:
                response, streamed_turn_text, streamed_function_calls = (
                    await _send_streaming_request(stream_payload)
                )
            except StreamConsumerDisconnected as disc:
                streamed_turn_text = disc.partial_turn_text
                streamed_function_calls = []
                response = None
                total_llm_time += time.perf_counter() - llm_start
                if streamed_turn_text:
                    stripped_turn = streamed_turn_text.strip()
                    if final_text and stripped_turn.startswith(final_text.strip()):
                        new_part = stripped_turn[len(final_text.strip()) :]
                        final_text += new_part
                    else:
                        if final_text and not final_text.endswith(("\n", " ")):
                            final_text += " "
                        final_text += streamed_turn_text
                logger.info(
                    "[%s] client disconnected during stream (iteration %s)",
                    request_id,
                    iteration,
                )
                break
            total_llm_time += time.perf_counter() - llm_start

            # Accumulate final_text, but avoid duplicating prefix if model repeats itself
            if streamed_turn_text:
                stripped_turn = streamed_turn_text.strip()
                if final_text and stripped_turn.startswith(final_text.strip()):
                    # Model repeated previous turn's text, only add the new part
                    new_part = stripped_turn[len(final_text.strip()) :]
                    final_text += new_part
                else:
                    if final_text and not final_text.endswith(("\n", " ")):
                        final_text += " "
                    final_text += streamed_turn_text

            # Some streamed turns may not end with a final response object.
            # Avoid hard failures and return a graceful fallback instead.
            if response is None and not streamed_turn_text and not streamed_function_calls:
                final_text = (
                    "I'm sorry, I couldn't generate a response right now. " "Please try again."
                )
                print(f"⏱️ [{request_id}] LLM iteration {iteration} ended without response")
                break

            # Track token usage
            if hasattr(response, "usage_metadata"):
                total_input_tokens += response.usage_metadata.prompt_token_count or 0
                total_output_tokens += response.usage_metadata.candidates_token_count or 0

            # Check for function calls
            function_calls = []
            if streamed_function_calls:
                function_calls = streamed_function_calls
            elif hasattr(response, "function_calls") and response.function_calls:
                function_calls = list(response.function_calls)
            elif hasattr(response, "parts") and getattr(response, "parts", None):
                for part in response.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                # No tool calls - final turn
                finish_reason = None
                try:
                    cands = getattr(response, "candidates", None)
                    if cands:
                        finish_reason = cands[0].finish_reason
                except Exception:
                    finish_reason = None

                if finish_reason == _types.FinishReason.UNEXPECTED_TOOL_CALL and not final_text:
                    final_text = (
                        "I hit a temporary issue coordinating tools for that request. "
                        "Please try again or rephrase your message slightly."
                    )
                elif not final_text:
                    try:
                        final_text = (
                            response.text if hasattr(response, "text") and response.text else ""
                        )
                    except (ValueError, Exception):
                        final_text = "I'm sorry, I couldn't generate a response."
                print(f"⏱️ [{request_id}] LLM iteration {iteration} completed with no tools")
                break

            # Execute function calls
            tool_results = []

            def _has_dependency_placeholders(value) -> bool:
                if isinstance(value, str):
                    return "$" in value
                if isinstance(value, dict):
                    return any(_has_dependency_placeholders(v) for v in value.values())
                if isinstance(value, list):
                    return any(_has_dependency_placeholders(v) for v in value)
                return False

            async def _execute_tool(function_call):
                tool_name = function_call.name
                tool_args = _convert_proto_to_dict(dict(function_call.args))
                print(f"🔧 Executing tool: {tool_name} with args: {tool_args}")
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
                    return tool_name, tool_args, tool_result, None
                except Exception as e:
                    print(f"❌ Tool execution error: {e}")
                    return tool_name, tool_args, {"error": str(e)}, str(e)

            tool_start = time.perf_counter()
            independent_calls = []
            dependent_calls = []
            for function_call in function_calls:
                tool_args = _convert_proto_to_dict(dict(function_call.args))
                if _has_dependency_placeholders(tool_args):
                    dependent_calls.append(function_call)
                else:
                    independent_calls.append(function_call)

            execution_results = []
            if independent_calls:
                ind_results = await asyncio.gather(
                    *[_execute_tool(function_call) for function_call in independent_calls]
                )
                execution_results.extend(ind_results)
                for tool_name, tool_args, tool_result, tool_error in ind_results:
                    if tool_error is None:
                        merge_successful_tool_result_into_created_ids(
                            tool_created_ids, tool_name, tool_result
                        )
            for function_call in dependent_calls:
                dep_result = await _execute_tool(function_call)
                execution_results.append(dep_result)
                tool_name, tool_args, tool_result, tool_error = dep_result
                if tool_error is None:
                    merge_successful_tool_result_into_created_ids(
                        tool_created_ids, tool_name, tool_result
                    )
            total_tool_time += time.perf_counter() - tool_start

            for tool_name, tool_args, tool_result, tool_error in execution_results:
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
                                "type": map_gemini_tool_to_action_type(tool_name),
                                "data": tool_args,
                                "result": tool_result,
                            }
                        )

                tool_results.append(
                    _types.Part.from_function_response(name=tool_name, response=tool_result)
                )
        else:
            # Max iterations reached
            final_text = "I encountered an issue processing your request. Please try again."

        usage_info = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model_name": resolved_model,
        }

        total_time = time.perf_counter() - request_start
        print(
            f"⏱️ [{request_id}] total={total_time:.2f}s llm={total_llm_time:.2f}s "
            f"tools={total_tool_time:.2f}s iterations={iteration}"
        )

        return final_text, usage_info, executed_actions, query_results

    except Exception as e:
        if isinstance(e, StreamConsumerDisconnected):
            pt = e.partial_turn_text or ""
            if pt:
                stripped_turn = pt.strip()
                if final_text and stripped_turn.startswith(final_text.strip()):
                    final_text += stripped_turn[len(final_text.strip()) :]
                else:
                    if final_text and not final_text.endswith(("\n", " ")):
                        final_text += " "
                    final_text += pt
            logger.info("get_chat_response_with_tools: client disconnected (stream): %s", e)
            return (
                final_text,
                {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "model_name": resolved_model,
                },
                executed_actions,
                query_results,
            )
        if is_websocket_consumer_disconnect(e):
            logger.info("get_chat_response_with_tools: client disconnected: %s", e)
            return (
                final_text,
                {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "model_name": resolved_model,
                },
                executed_actions,
                query_results,
            )
        print(f"Gemini Error with tools: {e}")
        from src.services.llm.errors import GeminiError

        raise GeminiError(
            model=model_id or default_model_for(LlmTask.CHAT_TOOLS_SESSION),
            status_code=500,
            category="server_error",
            message=f"Gemini service error: {e}",
            retriable=True,
        )


class GeminiChatToolsAdapter(BaseProviderAdapter):
    """Gemini implementation of :class:`BaseProviderAdapter`.

    Each instance is bound to a specific Gemini model ID so the router can
    select between e.g. gemini-2.5-flash and gemini-2.0-flash-lite.
    """

    __slots__ = ("_safety", "_model_id")

    def __init__(self, safety_settings: list[Any], model_id: str | None = None) -> None:
        self._safety = safety_settings
        self._model_id = model_id or default_model_for(LlmTask.CHAT_TOOLS_SESSION)

    # --- BaseProviderAdapter interface ---

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_id(self) -> str:
        return self._model_id

    def supported_capabilities(self) -> set[type]:
        # All Gemini flash models support chat, vision, and structured output
        # (except gemini-2.0-flash-lite which lacks vision, but we include it
        # for simplicity — the router's feature flags handle fine-grained control)
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
        return await run_gemini_chat_with_tools(
            history=history,
            user_message=user_message,
            context=context,
            user_id=user_id,
            user_name=user_name,
            image_url=image_url,
            progress_callback=progress_callback,
            stream_callback=stream_callback,
            safety_settings=self._safety,
            model_id=self._model_id,
        )
