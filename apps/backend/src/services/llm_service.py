"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

from __future__ import annotations

import asyncio
import os
import warnings
from datetime import UTC

import httpx
from fastapi import HTTPException

genai = None
# Suppress the Google Gemini deprecation warning temporarily
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    try:
        import google.generativeai as _genai

        genai = _genai
    except Exception:
        # Keep module importable even if the dependency isn't installed.
        # We'll raise a clearer error when the service is actually used.
        genai = None

try:
    # Only available when google-generativeai is installed
    from google.generativeai.types import HarmBlockThreshold, HarmCategory
except Exception:  # pragma: no cover - depends on optional dependency
    HarmBlockThreshold = None  # type: ignore[assignment]
    HarmCategory = None  # type: ignore[assignment]

# System instruction to define Maigie's persona
SYSTEM_INSTRUCTION = """
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

GUIDELINES:
- Be friendly, supportive, and encouraging
- When users ask questions or want to see their data, use the appropriate query tools (get_user_courses, get_user_goals, etc.)
- When users want to create or modify something, use the appropriate action tools (create_course, create_note, etc.)
- For casual conversation (greetings, thanks, etc.), respond naturally without using tools
- Always provide helpful context and explanations in your responses
- When a user asks for a study plan/schedule for a topic they already have a course for, use the existing course
"""


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

        # Configure API lazily (prevents import-time failures in worker contexts)
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

        self.model = genai.GenerativeModel(
            model_name="models/gemini-3-flash-preview", system_instruction=SYSTEM_INSTRUCTION
        )

        # Safety settings (block hate speech, etc.)
        if HarmCategory is None or HarmBlockThreshold is None:
            raise RuntimeError(
                "google-generativeai types are unavailable; cannot configure safety settings."
            )
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        }

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

            # Start a chat session with history
            chat = self.model.start_chat(history=history)

            # Send the enhanced message
            response = await chat.send_message_async(
                enhanced_message, safety_settings=self.safety_settings
            )

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
            # Get tool definitions
            tools = get_all_tools()

            # Create model with tools
            model_with_tools = genai.GenerativeModel(
                model_name="models/gemini-3-flash-preview",
                system_instruction=SYSTEM_INSTRUCTION,
                tools=tools,
            )

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
            message_content = enhanced_message_text
            if image_url and image_url in downloaded_images:
                img_data = downloaded_images[image_url]
                message_content = [
                    enhanced_message_text,
                    {"mime_type": img_data["mime_type"], "data": img_data["data"]},
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
                                    processed_parts.append(downloaded_images[part])
                                # Skip if download failed
                            else:
                                processed_parts.append(part)
                        else:
                            processed_parts.append(part)
                    processed_history.append({**hist_msg, "parts": processed_parts})
                else:
                    processed_history.append(hist_msg)

            # Start chat session
            chat = model_with_tools.start_chat(history=processed_history)

            # Track executed actions and query results
            executed_actions = []
            query_results = []
            total_input_tokens = 0
            total_output_tokens = 0
            final_text = ""  # Initialize final_text

            # Tool call loop
            max_iterations = 10  # Prevent infinite loops
            iteration = 0
            tool_results = []  # Initialize tool_results

            while iteration < max_iterations:
                iteration += 1

                # Send message (first iteration) or tool results (subsequent iterations)
                if iteration == 1:
                    response = await chat.send_message_async(
                        message_content, safety_settings=self.safety_settings
                    )
                else:
                    # Send tool results from previous iteration
                    response = await chat.send_message_async(
                        tool_results, safety_settings=self.safety_settings
                    )

                # Track token usage
                if hasattr(response, "usage_metadata"):
                    total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    total_output_tokens += response.usage_metadata.candidates_token_count or 0

                # Check for function calls - check both function_calls property and parts
                function_calls = []
                if hasattr(response, "function_calls") and response.function_calls:
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
                    # If streaming is enabled and this is the final iteration, re-request with streaming
                    if stream_callback and iteration == 1:
                        # Re-send with streaming for perceived performance
                        # Note: We already got the response, so we can stream from what we have
                        # For true streaming, we'd need to restructure, but this avoids a second API call
                        try:
                            final_text = (
                                response.text if hasattr(response, "text") and response.text else ""
                            )
                            # Stream the response in chunks for perceived faster response
                            if final_text:
                                chunk_size = 50  # Characters per chunk
                                for i in range(0, len(final_text), chunk_size):
                                    chunk = final_text[i : i + chunk_size]
                                    is_final = (i + chunk_size) >= len(final_text)
                                    await stream_callback(chunk, is_final)
                                    if not is_final:
                                        await asyncio.sleep(
                                            0.02
                                        )  # Small delay for smooth streaming
                        except ValueError as e:
                            print(f"⚠️ Could not get text from response: {e}")
                            final_text = ""
                    else:
                        # Non-streaming response
                        try:
                            final_text = (
                                response.text if hasattr(response, "text") and response.text else ""
                            )
                        except ValueError as e:
                            print(f"⚠️ Could not get text from response: {e}")
                            final_text = ""
                    break

                # Execute function calls
                tool_results = []
                for function_call in function_calls:
                    tool_name = function_call.name
                    # Convert protobuf args to plain Python dict (for JSON serialization)
                    tool_args = _convert_proto_to_dict(dict(function_call.args))

                    print(f"🔧 Executing tool: {tool_name} with args: {tool_args}")

                    # Execute tool handler
                    try:
                        tool_result = await handle_tool_call(
                            tool_name=tool_name,
                            args=tool_args,
                            user_id=user_id,
                            context=context,
                            progress_callback=progress_callback,
                        )

                        # Check if this is a query tool
                        if tool_name.startswith("get_user_"):
                            # Store query result for component response
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

                        # Track actions (for action tools)
                        if tool_name.startswith("create_") or tool_name in [
                            "recommend_resources",
                            "retake_note",
                            "add_summary_to_note",
                            "add_tags_to_note",
                        ]:
                            executed_actions.append(
                                {
                                    "type": self._map_tool_to_action_type(tool_name),
                                    "data": tool_args,
                                    "result": tool_result,
                                }
                            )

                        # Format tool result for Gemini
                        tool_results.append(
                            genai.protos.FunctionResponse(name=tool_name, response=tool_result)
                        )
                    except Exception as e:
                        print(f"❌ Tool execution error: {e}")
                        # Send error result back to model
                        tool_results.append(
                            genai.protos.FunctionResponse(
                                name=tool_name, response={"error": str(e)}
                            )
                        )
            else:
                # Max iterations reached
                final_text = "I encountered an issue processing your request. Please try again."

            usage_info = {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "model_name": "gemini-3-flash-preview",
            }

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
            "recommend_resources": "recommend_resources",
            "retake_note": "retake_note",
            "add_summary_to_note": "add_summary",
            "add_tags_to_note": "add_tags",
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

        # Always include context_parts (at minimum current date/time)
        if context_parts:
            context_str = "\n".join(context_parts)
            enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

        return enhanced_message

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

            response = await self.model.generate_content_async(
                summary_prompt, safety_settings=self.safety_settings
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
            minimal_model = genai.GenerativeModel(
                "gemini-2.0-flash-lite",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.7,
                ),
                safety_settings=self.safety_settings,
            )

            response = await minimal_model.generate_content_async(
                prompt, safety_settings=self.safety_settings
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
            outline_model = genai.GenerativeModel(
                "gemini-2.0-flash",
                system_instruction=SYSTEM_INSTRUCTION,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=900,
                    temperature=0.2,
                ),
                safety_settings=self.safety_settings,
            )

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

            response = await outline_model.generate_content_async(
                prompt, safety_settings=self.safety_settings
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

            response = await self.model.generate_content_async(
                rewrite_prompt, safety_settings=self.safety_settings
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

            response = await self.model.generate_content_async(
                tag_prompt, safety_settings=self.safety_settings
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
            response = await self.model.generate_content_async(
                content, safety_settings=self.safety_settings
            )

            return response.text

        except Exception as e:
            print(f"❌ Gemini Vision Error: {e}")
            import traceback

            traceback.print_exc()
            return "I'm sorry, I encountered an error analyzing that image."


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
