"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

from __future__ import annotations

import asyncio
from typing import Any
import os
import time
import warnings
from datetime import UTC

import httpx
from fastapi import HTTPException

genai = None
# Suppress the Google Gemini deprecation warning temporarily
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        from google import genai as _genai
        from google.genai import types as _types

        genai = _genai
    except Exception:
        # Keep module importable even if the dependency isn't installed.
        # We'll raise a clearer error when the service is actually used.
        genai = None


# Base system instruction to define Maigie's persona
_SYSTEM_INSTRUCTION_BASE = """
You are Maigie, an intelligent study companion.
Your goal is to help students organize learning, generate courses, manage schedules, create notes, and summarize content.

IMPORTANT DATE CONTEXT:
- The user's current date and time will be provided in the context of each conversation
- When creating schedules, goals, or any date-related actions, ALWAYS use dates relative to the CURRENT DATE provided in the context
- NEVER use hardcoded years - always calculate dates based on the current date provided

CRITICAL - AVOID DUPLICATES:
- BEFORE creating any new course, ALWAYS first use get_user_courses to check if the user already has a relevant course on that topic
- If a matching or similar course exists, USE that existing course instead of creating a duplicate
- When creating schedules or goals for a topic, first check existing courses and link to them
- Only create a new course if no relevant course exists

COURSE OUTLINE UPDATES:
- When a user provides a course outline (text or image), use update_course_outline to populate the course with modules and topics.
- ALWAYS call get_user_courses first to find the matching course by name.
- If the outline is a FLAT list of topics (no modules), group them into logical modules (4-6 modules) before calling update_course_outline.
- If the user says "outline for X" or "here is the outline for X", match X to an existing course.
- Images may contain course outlines/syllabi — extract the topics from the image and structure them into modules.

PERSONALIZATION & MEMORY:
- You have access to get_my_profile to retrieve the user's full profile including their name, courses, goals, study streak, and remembered facts about them.
- When the user asks personal questions like "who am I?", "what do you know about me?", or anything about their profile/progress, use get_my_profile.
- You have access to save_user_fact to remember important things the user tells you about themselves.
- When the user shares personal information relevant to their learning (e.g., learning preferences, exam dates, struggles, strengths, personal goals, background), use save_user_fact to remember it.
- Do NOT save trivial or obvious facts. Focus on information that helps you be a better study companion.
- Examples of facts worth saving: "I'm a visual learner", "My bar exam is in June", "I struggle with organic chemistry", "I prefer studying in the morning", "I'm a 3rd year medical student".

GUIDELINES:
- Be friendly, supportive, and encouraging
- Address the user by their first name when appropriate (their name is provided below)
- When users ask questions or want to see their data, use the appropriate query tools (get_user_courses, get_user_goals, etc.)
- When users want to create or modify something, use the appropriate action tools (create_course, create_note, etc.)
- For casual conversation (greetings, thanks, etc.), respond naturally without using tools
- Always provide helpful context and explanations in your responses
- When a user asks for a study plan/schedule for a topic they already have a course for, use the existing course

ADAPTIVE SCHEDULING & SEASON AWARENESS:
- You MUST understand where the student is in their academic year. If you don't know their current semester dates, exam periods, or term breaks, PROACTIVELY ask them (e.g., "By the way, when do your midterms start?" or "Are we in finals week or a new semester?").
- Use save_user_fact to memorize these milestone dates (e.g., 'Fall semester ends Dec 15', 'Midterms are Oct 10-20').
- Adjust scheduling based on the season: during exam periods, suggest more intense, compacted review sessions; during breaks, suggest lighter reading or rest; at the start of a semester, focus on establishing routine.
- Timetables change every semester. If asked to schedule sessions but you don't know the user's current semester timetable, availability, or work hours, you MUST ask them before creating the schedule (e.g., "Before I build this schedule, what does your new semester timetable look like so I can find the best gaps?").
- ALWAYS use check_schedule_conflicts before calling create_schedule to ensure the time slot is truly free.
- Remember to use Learning Insights (like 'Optimal study time') and User Facts when picking times.
"""

# Static fallback for cases where user_name is unavailable
SYSTEM_INSTRUCTION = _SYSTEM_INSTRUCTION_BASE + "\nThe user's name is not available.\n"


def build_personalized_system_instruction(user_name: str | None = None) -> str:
    """Build a personalized system instruction with the user's name."""
    if user_name:
        first_name = user_name.strip().split()[0] if user_name.strip() else "there"
        return (
            _SYSTEM_INSTRUCTION_BASE
            + f"\nThe user's name is {user_name} (first name: {first_name}).\n"
        )
    return SYSTEM_INSTRUCTION


def _convert_proto_to_dict(obj):
    """Recursively convert protobuf objects to plain Python dicts/lists.

    This handles Google protobuf types like MapComposite and RepeatedComposite
    that can't be directly serialized to JSON.
    """
    import json

    if obj is None:
        return None

    # Handle protobuf MapComposite (dict-like)
    if hasattr(obj, "keys") and callable(obj.keys):
        return {k: _convert_proto_to_dict(v) for k, v in obj.items()}

    # Handle protobuf RepeatedComposite (list-like)
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, dict)):
        try:
            return [_convert_proto_to_dict(item) for item in obj]
        except TypeError:
            pass

    # Handle basic types
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # Handle dict
    if isinstance(obj, dict):
        return {k: _convert_proto_to_dict(v) for k, v in obj.items()}

    # Handle list/tuple
    if isinstance(obj, (list, tuple)):
        return [_convert_proto_to_dict(item) for item in obj]

    # Fallback: try to convert to string
    try:
        # Try JSON serialization to test if it's serializable
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


class GeminiService:
    def __init__(self):
        if genai is None:
            raise RuntimeError(
                "google-generativeai is not installed. Install it to enable Gemini features."
            )

        self.model_name = "gemini-3-flash-preview"
        self.system_instruction = SYSTEM_INSTRUCTION
        self.client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        # Safety settings (new google.genai SDK format)
        self.safety_settings = [
            _types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_MEDIUM_AND_ABOVE",
            ),
            _types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_MEDIUM_AND_ABOVE",
            ),
        ]

    async def get_chat_response(
        self, history: list, user_message: str, context: dict = None
    ) -> tuple[str, dict]:
        """
        Send message to Gemini and get response.
        History should be formatted as a list of contents.
        Context can include: topicId, courseId, pageContext, etc.
        """
        try:
            # Build enhanced message with context if provided
            enhanced_message = user_message

            # Always add current date/time context
            from datetime import datetime, timezone

            current_datetime = datetime.now(UTC)
            current_date_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M UTC")

            context_parts = [f"Current Date & Time: {current_date_str}"]

            if context:
                if context.get("pageContext"):
                    context_parts.append(f"Current Page Context: {context['pageContext']}")

                # Course information
                if context.get("courseTitle"):
                    context_parts.append(f"Current Course: {context['courseTitle']}")
                    if context.get("courseDescription"):
                        context_parts.append(f"Course Description: {context['courseDescription']}")
                elif context.get("courseId"):
                    context_parts.append(f"Current Course ID: {context['courseId']}")

                # Topic information
                if context.get("topicTitle"):
                    context_parts.append(f"Current Topic: {context['topicTitle']}")
                    if context.get("moduleTitle"):
                        context_parts.append(f"Module: {context['moduleTitle']}")
                    if context.get("topicContent"):
                        # Include topic content for context (truncated for cost savings)
                        topic_content = context["topicContent"]
                        if len(topic_content) > 300:
                            topic_content = topic_content[:300] + "..."
                        context_parts.append(f"Topic Content: {topic_content}")
                elif context.get("topicId"):
                    context_parts.append(f"Current Topic ID: {context['topicId']}")

                # Note information
                if context.get("noteTitle"):
                    context_parts.append(f"Current Note: {context['noteTitle']}")
                    if context.get("noteContent"):
                        # Include note content for context (truncated for cost savings)
                        note_content = context["noteContent"]
                        if len(note_content) > 300:
                            note_content = note_content[:300] + "..."
                        context_parts.append(f"Note Content: {note_content}")
                    if context.get("noteSummary"):
                        context_parts.append(f"Note Summary: {context['noteSummary']}")
                elif context.get("noteId"):
                    context_parts.append(f"Current Note ID: {context['noteId']}")

                # Retrieved Items (from RAG/Database Search)
                if context.get("retrieved_items"):
                    context_parts.append("\nPossibly Relevant Items found in Database:")
                    for item in context["retrieved_items"]:
                        context_parts.append(str(item))
                    context_parts.append("(Use these IDs if the user refers to these items)")

            # Always include context_parts (at minimum current date/time)
            if context_parts:
                context_str = "\n".join(context_parts)
                enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

            # Process history - replace image URLs with downloaded data (if any) or just format to Content objects
            processed_history = []
            for msg_idx, hist_msg in enumerate(history):
                if isinstance(hist_msg, dict) and "parts" in hist_msg:
                    processed_parts = []
                    for part_idx, part in enumerate(hist_msg["parts"]):
                        if isinstance(part, str):
                            processed_parts.append(_types.Part(text=part))
                        elif isinstance(part, dict):
                            processed_parts.append(part)
                        else:
                            processed_parts.append(part)
                    processed_history.append(
                        _types.Content(role=hist_msg.get("role", "user"), parts=processed_parts)
                    )
                else:
                    processed_history.append(hist_msg)

            # Start a chat session with history
            chat = self.client.aio.chats.create(
                model=self.model_name,
                history=processed_history,
                config=_types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    safety_settings=self.safety_settings,
                ),
            )

            # Send the enhanced message
            response = await chat.send_message(enhanced_message)

            # Extract token usage from response
            usage_info = {
                "input_tokens": 0,
                "output_tokens": 0,
                "model_name": "gemini-1.5-flash",  # Default model name
            }

            # Try to get usage metadata from response
            if hasattr(response, "usage_metadata"):
                usage_metadata = response.usage_metadata
                if hasattr(usage_metadata, "prompt_token_count"):
                    usage_info["input_tokens"] = usage_metadata.prompt_token_count or 0
                if hasattr(usage_metadata, "candidates_token_count"):
                    usage_info["output_tokens"] = usage_metadata.candidates_token_count or 0
                if hasattr(usage_metadata, "total_token_count"):
                    # If we have total but not individual, estimate
                    if usage_info["input_tokens"] == 0 and usage_info["output_tokens"] == 0:
                        total = usage_metadata.total_token_count or 0
                        # Rough estimate: 70% input, 30% output
                        usage_info["input_tokens"] = int(total * 0.7)
                        usage_info["output_tokens"] = int(total * 0.3)

            # Get model name from response if available
            if hasattr(response, "model"):
                usage_info["model_name"] = response.model or usage_info["model_name"]

            return response.text, usage_info

        except Exception as e:
            print(f"Gemini Error: {e}")
            raise HTTPException(status_code=500, detail="AI Service unavailable")

    async def get_chat_response_with_tools(
        self,
        history: list,
        user_message: str,
        context: dict = None,
        user_id: str = None,
        user_name: str = None,
        image_url: str = None,
        progress_callback=None,
        stream_callback=None,
    ) -> tuple[str, dict, list[dict], list[dict]]:
        """
        Send message to Gemini with function calling support.

        Args:
            history: Chat history
            user_message: User's text message
            context: Additional context dictionary
            user_id: User ID for tool execution
            user_name: User's display name for personalization
            image_url: Optional image URL to include in the message
            progress_callback: Optional async callback for progress updates during tool execution
                              Signature: async def callback(progress: int, stage: str, message: str, **kwargs)
            stream_callback: Optional async callback for streaming text responses
                            Signature: async def callback(chunk: str, is_final: bool)

        Returns:
            tuple: (response_text, usage_info, executed_actions, query_results)
            - response_text: Final text response from model
            - usage_info: Token usage information
            - executed_actions: List of actions executed via tool calls
            - query_results: List of query tool results formatted for component responses
        """
        from src.services.gemini_tools import get_all_tools
        from src.services.gemini_tool_handlers import handle_tool_call
        import httpx

        try:
            request_start = time.perf_counter()
            request_id = f"agentic_{int(request_start * 1000)}"

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
            tools = (
                [_types.Tool(function_declarations=all_declarations)] if all_declarations else None
            )

            # Build personalized system instruction with user's name
            system_instruction = build_personalized_system_instruction(user_name)

            # Create client
            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

            # Build enhanced message with context
            enhanced_message_text = self._build_enhanced_message(user_message, context)

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
                        inline_data=_types.Blob(
                            mime_type=img_data["mime_type"], data=img_data["data"]
                        )
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
                                        _types.Part(
                                            inline_data=_types.Blob(
                                                mime_type=img_data["mime_type"],
                                                data=img_data["data"],
                                            )
                                        )
                                    )
                                # Skip if download failed
                            else:
                                processed_parts.append(_types.Part(text=part))
                        elif isinstance(part, dict):
                            # Function calls or other dicts. For safety, pass them in if they match schema
                            processed_parts.append(part)
                        else:
                            processed_parts.append(part)
                    processed_history.append(
                        _types.Content(role=hist_msg.get("role", "user"), parts=processed_parts)
                    )
                else:
                    processed_history.append(hist_msg)

            # Start chat session
            chat = client.aio.chats.create(
                model="gemini-3-flash-preview",
                history=processed_history,
                config=_types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=tools,
                    safety_settings=self.safety_settings,
                ),
            )

            # Track executed actions and query results
            executed_actions = []
            query_results = []
            total_input_tokens = 0
            total_output_tokens = 0
            final_text = ""  # Initialize final_text
            total_llm_time = 0.0
            total_tool_time = 0.0

            # Tool call loop
            max_iterations = 6  # Prevent infinite loops while reducing latency
            iteration = 0
            tool_results = []  # Initialize tool_results

            while iteration < max_iterations:
                iteration += 1

                # Send message (first iteration) or tool results (subsequent iterations)
                streamed_text = ""

                async def _send_streaming_request(payload):
                    response_stream = chat._send_message_stream(payload)
                    last_response = None
                    streamed_text_parts = []
                    last_chunk_text = None
                    streamed_function_calls = []

                    async for chunk in response_stream:
                        last_response = chunk
                        print(f"DEBUG: stream chunk parts: {getattr(chunk, 'parts', None)}")
                        try:
                            chunk_text = chunk.text
                            print(f"DEBUG: chunk_text={repr(chunk_text)}")
                        except ValueError as e:
                            print(f"DEBUG: ValueError reading chunk.text: {e}")
                            # Ignore non-text parts (e.g., function_call)
                            chunk_text = None
                        except Exception as e:
                            print(f"DEBUG: Unexpected error reading chunk.text: {e}")
                            chunk_text = None

                        if hasattr(chunk, "parts") and chunk.parts is not None:
                            for part in chunk.parts:
                                if hasattr(part, "function_call") and part.function_call:
                                    streamed_function_calls.append(part.function_call)
                        if chunk_text:
                            streamed_text_parts.append(chunk_text)
                            if last_chunk_text is not None:
                                await stream_callback(last_chunk_text, False)
                            last_chunk_text = chunk_text

                    if last_chunk_text is not None:
                        await stream_callback(last_chunk_text, True)

                    return last_response, "".join(streamed_text_parts), streamed_function_calls

                if iteration == 1:
                    llm_start = time.perf_counter()
                    if stream_callback:
                        response, streamed_text, streamed_function_calls = (
                            await _send_streaming_request(message_content)
                        )
                    else:
                        response = await chat.send_message(message_content)
                    total_llm_time += time.perf_counter() - llm_start
                    last_payload = message_content
                else:
                    # Send tool results from previous iteration
                    llm_start = time.perf_counter()
                    if stream_callback:
                        response, streamed_text, streamed_function_calls = (
                            await _send_streaming_request(tool_results)
                        )
                    else:
                        response = await chat.send_message(tool_results)
                    total_llm_time += time.perf_counter() - llm_start
                    last_payload = tool_results

                # Track token usage
                if hasattr(response, "usage_metadata"):
                    total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    total_output_tokens += response.usage_metadata.candidates_token_count or 0

                # Check for function calls - check both function_calls property and parts
                function_calls = []
                if stream_callback and streamed_function_calls:
                    function_calls = streamed_function_calls
                    print(f"📞 Found {len(function_calls)} function calls via stream parts")
                elif hasattr(response, "function_calls") and response.function_calls:
                    function_calls = list(response.function_calls)
                    print(
                        f"📞 Found {len(function_calls)} function calls via response.function_calls"
                    )
                elif hasattr(response, "parts"):
                    # Check parts for function calls (some models return them in parts)
                    for part in response.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            function_calls.append(part.function_call)
                    if function_calls:
                        print(f"📞 Found {len(function_calls)} function calls via response.parts")

                if not function_calls:
                    # No tool calls - this is the final response
                    if stream_callback:
                        if streamed_text:
                            final_text = streamed_text
                        else:
                            try:
                                final_text = (
                                    response.text
                                    if hasattr(response, "text") and response.text
                                    else ""
                                )
                            except ValueError as e:
                                print(f"⚠️ Could not get text from response: {e}")
                                final_text = ""
                            if not final_text:
                                final_text = "I'm sorry, I couldn't generate a response."
                    else:
                        # Non-streaming response
                        try:
                            final_text = (
                                response.text if hasattr(response, "text") and response.text else ""
                            )
                        except ValueError as e:
                            print(f"⚠️ Could not get text from response: {e}")
                            final_text = ""
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
                    execution_results.extend(
                        await asyncio.gather(
                            *[_execute_tool(function_call) for function_call in independent_calls]
                        )
                    )
                for function_call in dependent_calls:
                    execution_results.append(await _execute_tool(function_call))
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
                                    "type": self._map_tool_to_action_type(tool_name),
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
                "model_name": "gemini-3-flash-preview",
            }

            total_time = time.perf_counter() - request_start
            print(
                f"⏱️ [{request_id}] total={total_time:.2f}s llm={total_llm_time:.2f}s "
                f"tools={total_tool_time:.2f}s iterations={iteration}"
            )

            return final_text, usage_info, executed_actions, query_results

        except Exception as e:
            print(f"Gemini Error with tools: {e}")
            raise HTTPException(status_code=500, detail="AI Service unavailable")

    def _map_tool_to_action_type(self, tool_name: str) -> str:
        """Map tool name to action type for backward compatibility."""
        mapping = {
            "create_course": "create_course",
            "create_note": "create_note",
            "create_goal": "create_goal",
            "create_schedule": "create_schedule",
            "check_schedule_conflicts": "check_schedule_conflicts",
            "recommend_resources": "recommend_resources",
            "retake_note": "retake_note",
            "add_summary_to_note": "add_summary",
            "add_tags_to_note": "add_tags",
            "complete_review": "complete_review",
            "update_course_outline": "update_course_outline",
            "delete_course": "delete_course",
        }
        return mapping.get(tool_name, tool_name)

    def _build_enhanced_message(self, user_message: str, context: dict = None) -> str:
        """Build enhanced message with context."""
        enhanced_message = user_message

        # Always add current date/time context
        from datetime import datetime

        current_datetime = datetime.now(UTC)
        current_date_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M UTC")

        context_parts = [f"Current Date & Time: {current_date_str}"]

        if context:
            if context.get("pageContext"):
                context_parts.append(f"Current Page Context: {context['pageContext']}")

            # Course information
            if context.get("courseTitle"):
                context_parts.append(f"Current Course: {context['courseTitle']}")
                if context.get("courseDescription"):
                    context_parts.append(f"Course Description: {context['courseDescription']}")
            elif context.get("courseId"):
                context_parts.append(f"Current Course ID: {context['courseId']}")

            # Topic information
            if context.get("topicTitle"):
                context_parts.append(f"Current Topic: {context['topicTitle']}")
                if context.get("moduleTitle"):
                    context_parts.append(f"Module: {context['moduleTitle']}")
                if context.get("topicContent"):
                    # Include topic content for context (truncated for cost savings)
                    topic_content = context["topicContent"]
                    if len(topic_content) > 300:
                        topic_content = topic_content[:300] + "..."
                    context_parts.append(f"Topic Content: {topic_content}")
            elif context.get("topicId"):
                context_parts.append(f"Current Topic ID: {context['topicId']}")

            # Note information
            if context.get("noteTitle"):
                context_parts.append(f"Current Note: {context['noteTitle']}")
                if context.get("noteContent"):
                    # Include note content for context (truncated for cost savings)
                    note_content = context["noteContent"]
                    if len(note_content) > 300:
                        note_content = note_content[:300] + "..."
                    context_parts.append(f"Note Content: {note_content}")
                if context.get("noteSummary"):
                    context_parts.append(f"Note Summary: {context['noteSummary']}")
            elif context.get("noteId"):
                context_parts.append(f"Current Note ID: {context['noteId']}")

            # Retrieved Items (from RAG/Database Search)
            if context.get("retrieved_items"):
                context_parts.append("\nPossibly Relevant Items found in Database:")
                for item in context["retrieved_items"]:
                    context_parts.append(str(item))
                context_parts.append("(Use these IDs if the user refers to these items)")

            # Long-term memory context (conversation summaries + learning insights)
            if context.get("memory_context"):
                context_parts.append(f"\n{context['memory_context']}")

        # Always include context_parts (at minimum current date/time)
        if context_parts:
            context_str = "\n".join(context_parts)
            enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

        return enhanced_message

    async def extract_user_facts_from_conversation(
        self, messages: list[dict], user_id: str
    ) -> list[dict]:
        """
        Extract personal facts from a conversation that the AI may not have saved via tool.
        This runs as a background task after meaningful conversations.

        Args:
            messages: List of conversation messages [{"role": "user"/"assistant", "content": "..."}]
            user_id: User ID to save facts for

        Returns:
            List of extracted facts
        """
        if not genai:
            return []

        # Only process user messages for fact extraction
        user_messages = [
            m["content"] for m in messages if m.get("role") == "user" and m.get("content")
        ]
        if not user_messages or len(user_messages) < 2:
            return []

        conversation_text = "\n".join(f"User: {msg}" for msg in user_messages[-10:])

        extraction_prompt = f"""Analyze the following user messages from a study conversation and extract any personal facts the user shared about themselves that would be useful for a study companion AI to remember.

Only extract MEANINGFUL facts like:
- Learning preferences (e.g., "visual learner", "prefers practice problems")
- Academic background (e.g., "3rd year medical student", "studying for the bar exam")
- Personal goals or deadlines (e.g., "exam is in March", "wants to finish by summer")
- Struggles or weaknesses (e.g., "has trouble with calculus", "poor at memorization")
- Strengths (e.g., "good at writing", "strong in biology")
- Personal context (e.g., "works part-time", "has ADHD")

Do NOT extract:
- Trivial greetings or thanks
- Course names or goal titles (these are tracked separately)
- Anything already obvious from the conversation context

For each fact, provide a category and content.
Categories: preference, personal, academic, goal, struggle, strength, other

Return a JSON array of objects with "category" and "content" keys.
If no meaningful facts are found, return an empty array [].

User messages:
{conversation_text}

JSON array:"""

        try:
            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite", contents=extraction_prompt
            )

            if not response or not response.text:
                return []

            import json

            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]

            facts = json.loads(text)
            if not isinstance(facts, list):
                return []

            from src.core.database import db as db_client

            saved_facts = []
            for fact in facts[:5]:
                category = fact.get("category", "other")
                content = fact.get("content", "").strip()
                if not content:
                    continue

                valid_categories = [
                    "preference",
                    "personal",
                    "academic",
                    "goal",
                    "struggle",
                    "strength",
                    "other",
                ]
                if category not in valid_categories:
                    category = "other"

                try:
                    # Dedup check
                    existing = await db_client.userfact.find_many(
                        where={
                            "userId": user_id,
                            "category": category,
                            "isActive": True,
                        },
                        take=20,
                    )
                    is_duplicate = False
                    content_lower = content.lower()
                    for e in existing:
                        e_lower = e.content.lower()
                        new_words = set(content_lower.split())
                        existing_words = set(e_lower.split())
                        if new_words and existing_words:
                            overlap = len(new_words & existing_words) / max(
                                len(new_words), len(existing_words)
                            )
                            if overlap > 0.7:
                                is_duplicate = True
                                break

                    if not is_duplicate:
                        await db_client.userfact.create(
                            data={
                                "userId": user_id,
                                "category": category,
                                "content": content,
                                "source": "conversation",
                                "confidence": 0.7,
                            }
                        )
                        saved_facts.append(fact)
                except Exception as e:
                    print(f"Error saving extracted fact: {e}")

            return saved_facts

        except Exception as e:
            print(f"Error extracting user facts: {e}")
            return []

    async def generate_summary(self, content: str) -> str:
        """
        Generate a summary of the provided content.
        """
        try:
            summary_prompt = f"""Please provide a concise summary of the following content.
Focus on the key points and main ideas. Keep it brief but comprehensive.

IMPORTANT: Return ONLY the summary content. Do not include any introductory phrases,
concluding remarks, or conversational text like "Here is a summary:" or "Keep up the excellent work!".
Just provide the summary directly.

Content:
{content}

Summary:"""

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=summary_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings),
            )

            # Clean up any remaining conversational text that might have been added
            summary_text = response.text.strip()

            # Remove common AI introductory phrases if they appear
            intro_phrases = [
                "That's a great",
                "Here is a",
                "Here's a",
                "This is a",
                "Below is a",
                "The following is",
                "Keep up the",
                "Great work",
            ]

            for phrase in intro_phrases:
                if summary_text.lower().startswith(phrase.lower()):
                    # Find the first sentence end after the intro
                    sentences = summary_text.split(".")
                    if len(sentences) > 1:
                        # Skip the first sentence if it's an intro phrase
                        summary_text = ".".join(sentences[1:]).strip()
                        if summary_text.startswith(" "):
                            summary_text = summary_text[1:]
                    break

            # Remove outro phrases at the end
            outro_phrases = [
                "keep up the excellent work",
                "great work",
                "excellent work",
                "well done",
            ]

            summary_lower = summary_text.lower()
            for phrase in outro_phrases:
                if summary_lower.endswith(phrase.lower() + "."):
                    # Remove the last sentence if it's an outro
                    sentences = summary_text.rsplit(".", 1)
                    if len(sentences) > 1:
                        summary_text = sentences[0].strip() + "."
                    break

            return summary_text

        except Exception as e:
            print(f"Gemini Summary Error: {e}")
            raise HTTPException(status_code=500, detail="Summary generation failed")

    async def generate_minimal_response(self, prompt: str, max_tokens: int = 50) -> dict:
        """
        Generate a minimal AI response with strict token limits.
        Used for brief insights and tips that don't require full conversation context.

        Args:
            prompt: The prompt to generate a response for
            max_tokens: Maximum tokens for the response (default 50)

        Returns:
            Dictionary with 'text' and 'total_tokens' keys, or None if failed
        """
        try:
            # Use Flash model for minimal responses (faster and cheaper)
            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=prompt,
                config=_types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                    safety_settings=self.safety_settings,
                ),
            )

            # Calculate tokens used
            input_tokens = len(prompt) // 4  # Rough estimate
            output_tokens = len(response.text) // 4 if response.text else 0
            total_tokens = input_tokens + output_tokens

            return {
                "text": response.text.strip() if response.text else "",
                "total_tokens": total_tokens,
            }

        except Exception as e:
            print(f"Minimal response generation error: {e}")
            return None

    async def generate_course_outline(
        self,
        *,
        topic: str,
        difficulty: str = "BEGINNER",
        user_message: str | None = None,
        max_modules: int = 6,
        max_topics_per_module: int = 6,
    ) -> dict:
        """
        Generate a course outline (modules + topic titles) as JSON.

        This is designed to be run in a background job and MUST return valid JSON.
        It intentionally avoids generating long topic content to keep costs low.
        """
        try:
            difficulty_norm = (difficulty or "BEGINNER").upper()

            # Use a fast/cheap model for structured outline generation.
            user_msg = user_message or ""
            prompt = f"""Generate a course outline for the topic below.

Topic: {topic}
Difficulty: {difficulty_norm}

Constraints:
- Return ONLY valid JSON (no markdown, no code fences, no commentary).
- Keep it concise and structured for a learning app.
- Use at most {max_modules} modules.
- Each module should have at most {max_topics_per_module} topic titles.
- Topics should be short titles (no paragraphs).

Output JSON schema:
{{
  "title": "string",
  "description": "string",
  "difficulty": "BEGINNER|INTERMEDIATE|ADVANCED",
  "modules": [
    {{
      "title": "string",
      "description": "string (optional)",
      "topics": ["string", "string"]
    }}
  ]
}}

If the user message includes constraints (timeframe, focus areas), reflect them:
User message: {user_msg}

JSON:"""

            client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=900,
                    temperature=0.2,
                    safety_settings=self.safety_settings,
                ),
            )
            text = (response.text or "").strip()

            # Parse JSON robustly (extract first {...} block if needed)
            import json
            import re

            try:
                return json.loads(text)
            except Exception:
                match = re.search(r"\{[\s\S]*\}", text)
                if not match:
                    raise ValueError("No JSON object found in outline response")
                return json.loads(match.group(0))
        except Exception as e:
            print(f"Course outline generation error: {e}")
            # Fallback minimal outline (ensures task can still succeed)
            safe_topic = topic.strip() or "Your Topic"
            return {
                "title": f"Learning {safe_topic}",
                "description": f"A structured course on {safe_topic}.",
                "difficulty": (difficulty or "BEGINNER").upper(),
                "modules": [
                    {
                        "title": "Module 1: Foundations",
                        "topics": [
                            f"Introduction to {safe_topic}",
                            "Key concepts and terminology",
                            "Common pitfalls",
                        ],
                    },
                    {
                        "title": "Module 2: Practice",
                        "topics": [
                            "Core techniques",
                            "Exercises and drills",
                            "Review and next steps",
                        ],
                    },
                ],
            }

    async def rewrite_note_content(
        self, content: str, title: str = None, context: dict = None
    ) -> str:
        """
        Rewrite and improve note content with better markdown formatting.
        """
        try:
            context_info = ""
            if context:
                context_parts = []
                if context.get("topicTitle"):
                    context_parts.append(f"Topic: {context['topicTitle']}")
                if context.get("courseTitle"):
                    context_parts.append(f"Course: {context['courseTitle']}")
                if context_parts:
                    context_info = "\n".join(context_parts) + "\n\n"

            title_info = f"Note Title: {title}\n\n" if title else ""

            rewrite_prompt = f"""Please rewrite and improve the following note content.

IMPORTANT STYLE CONSTRAINTS:
- Maintain the ORIGINAL TONE and VOICE of the raw note.
- Do NOT make the writing sound overly academic, robotic, or textbook-like.
- Keep the language natural, relatable, and close to how a human would explain it.
- Do NOT replace simple explanations with heavy formal jargon unless the idea already exists in the raw note.
- Any added details must EXTEND the original ideas, not introduce new perspectives or frameworks.

STRUCTURE REQUIREMENTS:
- Improve clarity using headings, lists, and tables where helpful.
- Add light technical terms ONLY to clarify what is already being said.
- Preserve the original flow of ideas and emphasis.

FORMATTING:
- Use proper Markdown (headings, lists, tables, equations if already implied).
- Do not over-format or over-organize to the point of losing the raw feel.

CRITICAL OUTPUT RULES:
Return ONLY the rewritten note content in Markdown format.
Do NOT include:
- Introductory phrases (e.g., “Here is the rewritten version”)
- Explanations of changes
- Commentary or conclusions

Begin directly with the first heading or paragraph.

{title_info}{context_info}Original Content:
{content}

Rewritten Content:"""

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=rewrite_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings),
            )

            rewritten_text = response.text.strip()

            # Clean up any conversational text that might have been added
            # Remove common AI introductory phrases if they appear
            intro_phrases = [
                "That is an excellent",
                "That's an excellent",
                "That is a great",
                "That's a great",
                "I have rewritten",
                "I've rewritten",
                "Here is the",
                "Here's the",
                "Below is the",
                "The following is",
                "I have improved",
                "I've improved",
                "Organizing this material",
            ]

            for phrase in intro_phrases:
                if rewritten_text.lower().startswith(phrase.lower()):
                    # Find the first sentence end after the intro
                    sentences = rewritten_text.split(".")
                    if len(sentences) > 1:
                        # Skip the first sentence if it's an intro phrase
                        rewritten_text = ".".join(sentences[1:]).strip()
                        if rewritten_text.startswith(" "):
                            rewritten_text = rewritten_text[1:]
                    break

            # Remove outro phrases at the end
            outro_phrases = [
                "keep up the excellent work",
                "great work",
                "excellent work",
                "well done",
                "set you up well",
            ]

            rewritten_lower = rewritten_text.lower()
            for phrase in outro_phrases:
                if phrase in rewritten_lower:
                    # Try to remove sentences containing the outro phrase
                    sentences = rewritten_text.split(".")
                    # Remove sentences that contain the outro phrase
                    cleaned_sentences = [s for s in sentences if phrase not in s.lower()]
                    if len(cleaned_sentences) < len(sentences):
                        rewritten_text = ".".join(cleaned_sentences).strip()
                        if rewritten_text and not rewritten_text.endswith("."):
                            rewritten_text += "."
                    break

            return rewritten_text

        except Exception as e:
            print(f"Gemini Rewrite Error: {e}")
            raise HTTPException(status_code=500, detail="Note rewrite failed")

    async def generate_tags(
        self, content: str, title: str = None, topic_title: str = None
    ) -> list[str]:
        """
        Generate relevant tags for a note based on its content.
        """
        try:
            title_info = f"Note Title: {title}\n\n" if title else ""
            topic_info = f"Topic: {topic_title}\n\n" if topic_title else ""

            tag_prompt = f"""Please generate 3-8 relevant tags for the following note content.
Tags should be:
- Concise and descriptive (1-3 words each)
- Use PascalCase or camelCase format (e.g., "CommunityHealthNursing", "PublicHealth")
- Relevant to the main topics, concepts, or subject areas covered
- Useful for filtering and organizing notes

IMPORTANT: Return ONLY a JSON array of tag strings. Do not include any introductory text, explanations, or commentary.
Just return the array, for example: ["Tag1", "Tag2", "Tag3"]

{title_info}{topic_info}Note Content:
{content}

Tags (JSON array):"""

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=tag_prompt,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings),
            )

            tags_text = response.text.strip()

            # Try to extract JSON array from the response
            import json
            import re

            # Look for JSON array pattern
            json_match = re.search(r"\[.*?\]", tags_text, re.DOTALL)
            if json_match:
                tags = json.loads(json_match.group(0))
            else:
                # Fallback: try to parse the entire response as JSON
                tags = json.loads(tags_text)

            # Ensure it's a list of strings
            if isinstance(tags, list):
                # Filter out empty strings and normalize
                tags = [str(tag).strip() for tag in tags if tag and str(tag).strip()]
                return tags[:8]  # Limit to 8 tags max
            else:
                return []

        except Exception as e:
            print(f"Gemini Tag Generation Error: {e}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Tag generation failed")

    async def analyze_image(self, prompt: str, image_url: str) -> str:
        """
        Analyze an image from a URL using Gemini Vision capabilities.
        """
        print(f"👁️ Gemini analyzing image: {image_url}")

        try:
            # 1. Download the image bytes from the URL
            async with httpx.AsyncClient() as client:
                response = await client.get(image_url)
                if response.status_code != 200:
                    print(f"❌ Failed to download image: {response.status_code}")
                    return "I'm sorry, I couldn't access the image URL."

                image_data = response.content
                mime_type = response.headers.get("content-type", "image/jpeg")

            # 2. Prepare the content for Gemini
            # Gemini treats images as a distinct part of the prompt content
            content = [prompt, {"mime_type": mime_type, "data": image_data}]

            # 3. Generate response
            # Convert image bytes to part and prepare content list properly
            from google.genai import types as genai_types

            img_part = genai_types.Part.from_bytes(data=image_data, mime_type=mime_type)
            new_content = [prompt, img_part]
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=new_content,
                config=_types.GenerateContentConfig(safety_settings=self.safety_settings),
            )

            return response.text

        except Exception as e:
            print(f"❌ Gemini Vision Error: {e}")
            import traceback

            traceback.print_exc()
            return "I'm sorry, I encountered an error analyzing that image."


async def extract_exam_topics(subject: str, material_texts: list[str]) -> list[dict]:
    """
    Extract key topics/chapters from exam prep materials using AI.
    Returns a list of {title, description} dictionaries.
    """
    import json
    import re

    if genai is None:
        return []

    # Combine material texts (truncated to avoid token limits)
    combined = ""
    for text in material_texts:
        if text:
            remaining = 30000 - len(combined)
            if remaining <= 0:
                break
            combined += text[:remaining] + "\n\n---\n\n"

    if not combined.strip():
        return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]

    prompt = f"""Analyze the following study materials for the subject "{subject}" and extract the KEY TOPICS
that a student should study for their exam.

Study Material:
{combined[:30000]}

Return ONLY a JSON array of topics. Each topic should have:
- "title": Short topic name (2-6 words)
- "description": Brief description (1-2 sentences)

Guidelines:
- Extract 5-15 distinct topics
- Group related content under broader topics
- Order them logically (foundations first, advanced later)
- Focus on topics that are likely to appear on an exam
- No markdown, no code fences, no commentary

JSON array:"""

    try:
        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types

        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()

        try:
            topics = json.loads(text)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]
            topics = json.loads(match.group(0))

        if isinstance(topics, list):
            return [
                {
                    "title": t.get("title", "Unknown Topic"),
                    "description": t.get("description", ""),
                }
                for t in topics
                if isinstance(t, dict) and t.get("title")
            ]
        return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]

    except Exception as e:
        print(f"extract_exam_topics error: {e}")
        return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]


async def extract_past_paper_questions(text: str, subject: str) -> list[dict]:
    """
    Parse a past exam paper text into individual questions with answers/options.
    Returns structured question data ready for storage.
    """
    import json
    import re

    if genai is None:
        return []

    prompt = f"""You are analyzing a past exam paper for "{subject}". Extract each individual question from this text.

Past Paper Text:
{text[:25000]}

For EACH question, extract:
- "questionText": The full question text
- "questionType": One of "MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "FILL_IN_BLANK"
- "options": For MCQ, an array of {{"label": "A", "text": "option text", "isCorrect": true/false}}. If the correct answer is provided, mark it. If not, make your best educated guess based on subject knowledge.
- "correctAnswer": The correct answer text (for short answer/fill-in)
- "explanation": A clear explanation of WHY this is the correct answer (2-4 sentences)
- "difficulty": "EASY", "MEDIUM", or "HARD"
- "year": If a year is mentioned in the paper, include it

Return ONLY a JSON array. No markdown, no code fences, no commentary.
If you can't identify the correct answer with certainty, still provide your best answer with explanation.

JSON array:"""

    try:
        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types

        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.1,
            ),
        )
        text_response = (response.text or "").strip()

        try:
            questions = json.loads(text_response)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", text_response)
            if not match:
                return []
            questions = json.loads(match.group(0))

        if isinstance(questions, list):
            return [
                {
                    "questionText": q.get("questionText", ""),
                    "questionType": q.get("questionType", "MULTIPLE_CHOICE"),
                    "options": q.get("options"),
                    "correctAnswer": q.get("correctAnswer"),
                    "explanation": q.get("explanation", "No explanation available."),
                    "difficulty": q.get("difficulty", "MEDIUM"),
                    "year": q.get("year"),
                    "source": "PAST_QUESTION",
                }
                for q in questions
                if isinstance(q, dict) and q.get("questionText")
            ]
        return []

    except Exception as e:
        print(f"extract_past_paper_questions error: {e}")
        return []


async def generate_exam_questions(
    subject: str,
    topic_title: str,
    context_text: str,
    count: int = 5,
    existing_questions: list[str] | None = None,
) -> list[dict]:
    """
    Generate new exam-style questions from study materials for a specific topic.
    Returns structured question data.
    """
    import json
    import re

    if genai is None:
        return []

    existing_str = ""
    if existing_questions:
        existing_str = "\n\nAVOID duplicating these existing questions:\n" + "\n".join(
            f"- {q}" for q in existing_questions[:10]
        )

    prompt = f"""Generate {count} exam-style questions for the topic "{topic_title}" in the subject "{subject}".

Study Material Context:
{context_text[:20000]}
{existing_str}

Requirements:
- Generate a MIX of question types: mostly MULTIPLE_CHOICE, some TRUE_FALSE, and a few SHORT_ANSWER
- For MULTIPLE_CHOICE: provide exactly 4 options (A, B, C, D) with ONE correct answer
- For TRUE_FALSE: correctAnswer should be "TRUE" or "FALSE"
- For SHORT_ANSWER: correctAnswer should be a concise answer (1-3 words)
- Include a clear explanation for each answer (2-4 sentences explaining WHY)
- Vary difficulty: mix of EASY, MEDIUM, and HARD
- Questions should test understanding, not just recall
- Make them realistic exam questions

Return ONLY a JSON array with {count} questions. Each question:
{{
  "questionText": "string",
  "questionType": "MULTIPLE_CHOICE" | "TRUE_FALSE" | "SHORT_ANSWER",
  "options": [{{"label": "A", "text": "...", "isCorrect": false}}, ...] (for MCQ only),
  "correctAnswer": "string",
  "explanation": "string",
  "difficulty": "EASY" | "MEDIUM" | "HARD"
}}

No markdown, no code fences.
JSON array:"""

    try:
        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types

        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.4,
            ),
        )
        text_response = (response.text or "").strip()

        try:
            questions = json.loads(text_response)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", text_response)
            if not match:
                return []
            questions = json.loads(match.group(0))

        if isinstance(questions, list):
            return [
                {
                    "questionText": q.get("questionText", ""),
                    "questionType": q.get("questionType", "MULTIPLE_CHOICE"),
                    "options": q.get("options"),
                    "correctAnswer": q.get("correctAnswer"),
                    "explanation": q.get("explanation", "No explanation available."),
                    "difficulty": q.get("difficulty", "MEDIUM"),
                    "source": "AI_GENERATED",
                }
                for q in questions
                if isinstance(q, dict) and q.get("questionText")
            ]
        return []

    except Exception as e:
        print(f"generate_exam_questions error: {e}")
        return []


async def get_schedule_review_suggestions(user_id: str, db: Any) -> list[dict[str, Any]]:
    """
    For daily AI schedule review: fetch user's courses, behaviour, and schedule,
    ask Gemini to suggest 0-5 new schedule blocks for the next 7 days; return list of
    { title, startAt, endAt, courseId? } for create_schedule.

    When the user has courses but no/few upcoming schedules, the AI recommends study blocks
    from their course content (e.g. incomplete topics) so they don't run out of study plans.
    """
    import json
    import re
    from datetime import UTC, datetime, timedelta

    if genai is None:
        return []
    now = datetime.now(UTC)
    week_end = now + timedelta(days=7)

    # User's courses with progress (for recommending study when schedule is empty)
    courses = await db.course.find_many(
        where={"userId": user_id, "archived": False},
        order={"updatedAt": "desc"},
        take=10,
        include={"modules": {"include": {"topics": True}}},
    )
    courses_str_parts = []
    for c in courses:
        total = sum(len(m.topics) for m in c.modules)
        completed = sum(1 for m in c.modules for t in m.topics if t.completed)
        incomplete_topics = [
            f"{m.title}: {t.title}" for m in c.modules for t in m.topics if not t.completed
        ][:8]
        courses_str_parts.append(
            f"- {c.title} (id={c.id}): {completed}/{total} topics done. "
            f"Incomplete: {', '.join(incomplete_topics[:4]) or 'none'}"
        )
    courses_str = "\n".join(courses_str_parts) if courses_str_parts else "No courses."

    # Recent behaviour (last 30)
    behaviour_logs = await db.schedulebehaviourlog.find_many(
        where={"userId": user_id},
        order={"createdAt": "desc"},
        take=30,
    )
    # Upcoming schedule (next 7 days)
    schedules = await db.scheduleblock.find_many(
        where={"userId": user_id, "startAt": {"gte": now}, "endAt": {"lte": week_end}},
        order={"startAt": "asc"},
        take=50,
    )
    # Due reviews (per-topic spaced repetition)
    due_reviews = await db.reviewitem.find_many(
        where={"userId": user_id, "nextReviewAt": {"lte": week_end}},
        include={"topic": True},
        order={"nextReviewAt": "asc"},
    )
    behaviour_str = (
        "\n".join(
            [
                f"- {b.behaviourType} ({b.entityType}) at {b.createdAt.isoformat()}"
                + (f" scheduled={b.scheduledAt}" if b.scheduledAt else "")
                for b in behaviour_logs[:20]
            ]
        )
        or "None yet."
    )
    schedule_str = (
        "\n".join(
            [f"- {s.title} {s.startAt.isoformat()} - {s.endAt.isoformat()}" for s in schedules[:20]]
        )
        or "None."
    )
    reviews_str = (
        "\n".join(
            [
                f"- Review: {r.topic.title if r.topic else 'Topic'} due {r.nextReviewAt.isoformat()}"
                for r in due_reviews[:15]
            ]
        )
        or "None."
    )
    current_date = now.strftime("%A, %B %d, %Y at %H:%M UTC")
    prompt = f"""You are a study schedule assistant. Recommend schedule blocks for the next 7 days.

IMPORTANT: If the user has courses but NO or very few upcoming schedules, suggest 3-5 study blocks from their course content (incomplete topics, next modules). They should not run out of study plans as long as they have courses to learn.

Otherwise, base suggestions on behaviour and due reviews. Learn from behaviour: COMPLETED_ON_TIME = reliable; COMPLETED_LATE/SKIPPED = suggest easier times or shorter blocks; RESCHEDULED = respect their preferred timing.

Current date and time: {current_date}

User's courses (with progress; use courseId when suggesting blocks for a course):
{courses_str}

Recent behaviour (last 20):
{behaviour_str}

Upcoming schedule (next 7 days):
{schedule_str}

Reviews due (spaced repetition):
{reviews_str}

Return ONLY a JSON array of 0-5 blocks. Each block: {{ "title": "string", "startAt": "ISO8601", "endAt": "ISO8601", "courseId": "optional cuid" }}.
Use times in the next 7 days. Prefer morning/afternoon slots for study. No markdown, no code fence, no commentary.
JSON:"""
    try:
        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        from google.genai import types as genai_types

        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=600,
                temperature=0.3,
            ),
        )
        text = (response.text or "").strip()
        try:
            arr = json.loads(text)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                return []
            arr = json.loads(match.group(0))
        if not isinstance(arr, list):
            return []
        out = []
        for item in arr[:5]:
            if (
                not isinstance(item, dict)
                or "title" not in item
                or "startAt" not in item
                or "endAt" not in item
            ):
                continue
            out.append(
                {
                    "title": str(item.get("title", "Study block")),
                    "startAt": item["startAt"],
                    "endAt": item["endAt"],
                    "courseId": item.get("courseId"),
                }
            )
        return out
    except Exception as e:
        print(f"get_schedule_review_suggestions error: {e}")
        return []


class _LazyGeminiService:
    """Lazy proxy to avoid import-time side effects/crashes."""

    _instance: "GeminiService | None" = None

    def _get(self) -> "GeminiService":
        if self._instance is None:
            self._instance = GeminiService()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


# Backwards-compatible global proxy
llm_service = _LazyGeminiService()
