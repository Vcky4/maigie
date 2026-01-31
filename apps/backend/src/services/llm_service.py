"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

from __future__ import annotations

import os
import warnings
from datetime import UTC

import httpx  # <--- Added for image download
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

GUIDELINES:
- Be friendly, supportive, and encouraging
- When users ask questions or want to see their data, use the appropriate query tools (get_user_courses, get_user_goals, etc.)
- When users want to create or modify something, use the appropriate action tools (create_course, create_note, etc.)
- For casual conversation (greetings, thanks, etc.), respond naturally without using tools
- Always provide helpful context and explanations in your responses
"""


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
        stream: bool = False,
    ):
        """
        Send message to Gemini with function calling support.

        Args:
            stream: If True, streams word-by-word. If False, yields entire text at once.

        Yields:
            dict: Chunks with "type" field:
                - "text_chunk": Text content (word-by-word if stream=True, full text if stream=False)
                - "done": Final metadata (usage_info, executed_actions, query_results)
                - "error": Error occurred (error message and metadata)
        """
        from src.services.gemini_tools import get_all_tools
        from src.services.gemini_tool_handlers import handle_tool_call

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

            # Handle images if present in context
            image_parts = []
            if context and context.get("fileUrls"):
                file_urls = context.get("fileUrls")
                # fileUrls can be a string (single URL) or list of URLs
                if isinstance(file_urls, str):
                    file_urls = [file_urls]
                elif not isinstance(file_urls, list):
                    file_urls = []

                # Download and prepare images
                for image_url in file_urls:
                    try:
                        print(f"🖼️ Downloading image for Gemini: {image_url}")
                        async with httpx.AsyncClient() as client:
                            img_response = await client.get(image_url)
                            if img_response.status_code == 200:
                                image_data = img_response.content
                                mime_type = img_response.headers.get("content-type", "image/jpeg")
                                image_parts.append({"mime_type": mime_type, "data": image_data})
                                print(f"✅ Image downloaded successfully: {len(image_data)} bytes")
                            else:
                                print(f"⚠️ Failed to download image: {img_response.status_code}")
                    except Exception as e:
                        print(f"⚠️ Error downloading image {image_url}: {e}")

            # Build multimodal content (text + images)
            if image_parts:
                # Combine text and images for multimodal input
                enhanced_message = [enhanced_message_text] + image_parts
                print(f"📸 Sending message with {len(image_parts)} image(s)")
            else:
                enhanced_message = enhanced_message_text

            # Start chat session
            chat = model_with_tools.start_chat(history=history)

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
                        enhanced_message, safety_settings=self.safety_settings
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

                # Check for function calls - must check before accessing .text
                # Gemini raises ValueError when accessing .text if response has function calls
                has_function_calls = False
                try:
                    if hasattr(response, "function_calls") and response.function_calls:
                        has_function_calls = len(response.function_calls) > 0
                except (AttributeError, TypeError):
                    has_function_calls = False

                if not has_function_calls:
                    # Try to get text response - this will raise ValueError if function calls exist
                    try:
                        final_text = (
                            response.text if hasattr(response, "text") and response.text else ""
                        )
                        # Successfully got text, no function calls - break the loop
                        break
                    except ValueError as e:
                        # ValueError means response has function calls but our check missed them
                        # Fall through to process function calls
                        print(f"⚠️ Detected function calls via ValueError: {e}")
                        # Try to get function calls again
                        try:
                            if hasattr(response, "function_calls") and response.function_calls:
                                has_function_calls = True
                            else:
                                # No function calls found, but text access failed - use empty string
                                final_text = ""
                                break
                        except Exception:
                            final_text = ""
                            break
                    except AttributeError:
                        # No text attribute - use empty string
                        final_text = ""
                        break

                # Execute function calls
                tool_results = []
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = dict(function_call.args)

                    print(f"🔧 Executing tool: {tool_name} with args: {tool_args}")

                    # Execute tool handler
                    try:
                        tool_result = await handle_tool_call(
                            tool_name=tool_name,
                            args=tool_args,
                            user_id=user_id,
                            context=context,
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

            # Always yield (function is always a generator when stream parameter exists)
            if final_text:
                if stream:
                    # Stream word by word for smooth UX
                    import asyncio

                    words = final_text.split()
                    for word in words:
                        yield {
                            "type": "text_chunk",
                            "content": word + " ",
                        }
                        # Small delay for smooth streaming effect
                        await asyncio.sleep(0.01)
                else:
                    # Non-streaming: yield entire text at once (for backward compatibility)
                    yield {
                        "type": "text_chunk",
                        "content": final_text,
                    }

            # Yield final metadata
            yield {
                "type": "done",
                "usage_info": usage_info,
                "executed_actions": executed_actions,
                "query_results": query_results,
            }

        except Exception as e:
            print(f"Gemini Error with tools: {e}")
            import traceback

            traceback.print_exc()
            # Always yield error (function is always a generator)
            yield {
                "type": "error",
                "error": str(e),
                "usage_info": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "model_name": "gemini-3-flash-preview",
                },
                "executed_actions": executed_actions,
                "query_results": query_results,
            }

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
        """Build enhanced message with context (extracted from get_chat_response)."""
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
