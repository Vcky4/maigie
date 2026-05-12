"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import UTC
from typing import Any, cast

import httpx
from fastapi import HTTPException

from src.config import get_settings
from src.services.llm.gemini_chat_tools import run_gemini_chat_with_tools
from src.services.llm.gemini_sdk import genai, new_gemini_client, types as _types
from src.services.llm.prompts import SYSTEM_INSTRUCTION
from src.services.llm.protocol import ChatWithToolsProvider
from src.services.llm_chat_context import (
    build_enhanced_chat_user_message,
    map_gemini_tool_to_action_type,
)
from src.services.llm_registry import LlmTask, default_model_for, gemini_api_key

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self):
        if genai is None:
            raise RuntimeError(
                "google-genai is not installed. Install it to enable Gemini features."
            )

        self.model_name = default_model_for(LlmTask.CHAT_DEFAULT)
        self.system_instruction = SYSTEM_INSTRUCTION
        self.client = new_gemini_client(gemini_api_key() or None)

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
            enhanced_message = build_enhanced_chat_user_message(user_message, context)

            # Process history - replace image URLs with downloaded data (if any) or just format to Content objects
            processed_history = []
            for msg_idx, hist_msg in enumerate(history):
                if isinstance(hist_msg, dict) and "parts" in hist_msg:
                    processed_parts = []
                    for part_idx, part in enumerate(hist_msg["parts"]):
                        if isinstance(part, str):
                            processed_parts.append({"text": part})
                        elif isinstance(part, dict):
                            processed_parts.append(part)
                        else:
                            processed_parts.append(part)
                    processed_history.append(
                        {"role": hist_msg.get("role", "user"), "parts": processed_parts}
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
                "model_name": default_model_for(LlmTask.CHAT_DEFAULT),
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
        """Send message to Gemini with function calling (delegates to run_gemini_chat_with_tools)."""
        return await run_gemini_chat_with_tools(
            history=history,
            user_message=user_message,
            context=context,
            user_id=user_id,
            user_name=user_name,
            image_url=image_url,
            progress_callback=progress_callback,
            stream_callback=stream_callback,
            safety_settings=self.safety_settings,
        )

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

        extraction_prompt = f"""Analyze the following user messages from a study conversation and extract any personal facts the user shared about themselves that would be useful for Maigie (their academic operating system) to remember.

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
            client = new_gemini_client(gemini_api_key() or None)
            response = await client.aio.models.generate_content(
                model=default_model_for(LlmTask.FACT_EXTRACTION_LITE),
                contents=extraction_prompt,
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
            client = new_gemini_client(gemini_api_key() or None)
            response = await client.aio.models.generate_content(
                model=default_model_for(LlmTask.MINIMAL_RESPONSE),
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

            client = new_gemini_client(gemini_api_key() or None)
            response = await client.aio.models.generate_content(
                model=default_model_for(LlmTask.COURSE_OUTLINE),
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
            # 1. Download the image bytes from the URL (CDN), then Bunny storage API if TLS/CDN fails
            image_data: bytes | None = None
            mime_type = "image/jpeg"
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(image_url)
                    if response.status_code == 200:
                        image_data = response.content
                        mime_type = response.headers.get("content-type", "image/jpeg")
                    else:
                        print(f"❌ Image URL returned status {response.status_code}")
            except Exception as e:
                print(f"❌ Image download error: {e}")

            if not image_data:
                from src.services.storage_service import storage_service as _storage

                fb = await _storage.fetch_public_chat_image_bytes(image_url)
                if fb:
                    image_data, raw_ct = fb
                    mime_type = (raw_ct or "").split(";", 1)[0].strip() or "image/jpeg"

            if not image_data:
                return "I'm sorry, I couldn't access the image URL."

            # 2. Prepare the content for Gemini
            # Gemini treats images as a distinct part of the prompt content
            content = [prompt, {"mime_type": mime_type, "data": image_data}]

            # 3. Generate response
            # Convert image bytes to part and prepare content list properly
            img_part = _types.Part.from_bytes(data=image_data, mime_type=mime_type)
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


def _transient_gemini_error(exc: BaseException) -> bool:
    """True when a short wait or model switch might help (rate limits, overload)."""
    msg = str(exc).lower()
    return (
        "429" in msg
        or "resource exhausted" in msg
        or "too many requests" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "503" in msg
        or "502" in msg
        or "unavailable" in msg
        or "500" in msg
        or "internal error" in msg
        or "deadline" in msg
        or "timeout" in msg
    )


def _gemini_model_not_found(exc: BaseException) -> bool:
    """True when the model id is invalid or no longer exposed on this API version."""
    msg = str(exc).lower()
    return "404" in msg and ("not found" in msg or "not_found" in msg)


# gemini-1.5-* ids often 404 on generativelanguage v1beta for generateContent.
_DEFAULT_GEMINI_ROTATING_MODELS = "gemini-2.0-flash,gemini-2.5-flash,gemini-2.0-flash-lite"


def _gemini_rotating_model_list(primary_env: str) -> list[str]:
    """Models for a feature: feature-specific setting, else GEMINI_ROTATING_MODELS, else default."""
    s = get_settings()
    if primary_env == "GEMINI_EXAM_PREP_MODELS":
        raw = (
            s.GEMINI_EXAM_PREP_MODELS or s.GEMINI_ROTATING_MODELS or _DEFAULT_GEMINI_ROTATING_MODELS
        )
    elif primary_env == "GEMINI_SCHEDULE_AI_MODELS":
        raw = (
            s.GEMINI_SCHEDULE_AI_MODELS
            or s.GEMINI_ROTATING_MODELS
            or _DEFAULT_GEMINI_ROTATING_MODELS
        )
    else:
        raw = s.GEMINI_ROTATING_MODELS or _DEFAULT_GEMINI_ROTATING_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


def _exam_prep_model_chain() -> list[str]:
    return _gemini_rotating_model_list("GEMINI_EXAM_PREP_MODELS")


def _schedule_ai_model_chain() -> list[str]:
    """Models for schedule review / AI regeneration (same rotation semantics as exam prep)."""
    return _gemini_rotating_model_list("GEMINI_SCHEDULE_AI_MODELS")


def _parse_llm_json_array(text: str) -> list[Any] | None:
    """
    Parse a JSON array from LLM output. Tolerates markdown fences and leading prose.

    Uses json.JSONDecoder.raw_decode from the first '[' so a single well-formed array is
    parsed end-to-end. A greedy full-text bracket regex often breaks on nested brackets
    or trailing prose, yielding empty parses and no saved questions for most topics.
    """
    import json

    raw = (text or "").strip()
    if not raw:
        return None

    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            chunk = part.strip()
            if not chunk:
                continue
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].lstrip()
            if chunk.startswith("["):
                raw = chunk
                break

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("[")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        data, _end = decoder.raw_decode(raw[start:])
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


async def _gemini_generate_content_with_model_rotation(
    *,
    contents: Any,
    config: Any,
    models: list[str],
    max_rounds: int = 6,
    base_delay_sec: float = 2.0,
    log_prefix: str = "gemini",
) -> Any:
    """
    Call generate_content, round-robin a different model on each attempt.

    On transient errors (429, overload, …), backoff then retry with the next model in the list.
    On 404 for a model id, drop that model for the rest of this call and continue immediately.
    """
    if genai is None:
        raise RuntimeError("Google Genai client is not available")
    if not models:
        raise RuntimeError("No Gemini models configured")

    client = new_gemini_client(gemini_api_key() or None)
    last_exc: BaseException | None = None
    unavailable: set[str] = set()
    initial_n = len(models)
    max_attempts = max_rounds * max(initial_n, 1)
    backoff_slot = 0

    for attempt in range(max_attempts):
        eligible = [m for m in models if m not in unavailable]
        if not eligible:
            break
        model_name = eligible[attempt % len(eligible)]
        try:
            return await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except BaseException as e:
            last_exc = e
            if _gemini_model_not_found(e):
                logger.warning(
                    "%s model not found, skipping model=%s: %s", log_prefix, model_name, e
                )
                unavailable.add(model_name)
                continue
            if not _transient_gemini_error(e):
                logger.warning("%s non-retryable error model=%s: %s", log_prefix, model_name, e)
                raise
            if attempt >= max_attempts - 1:
                logger.warning(
                    "%s exhausted attempts (last model=%s): %s", log_prefix, model_name, e
                )
                break
            delay = min(base_delay_sec * (2 ** min(backoff_slot, 5)) + random.random(), 45.0)
            backoff_slot += 1
            logger.info(
                "%s retry in %.1fs (model=%s attempt=%s/%s): %s",
                log_prefix,
                delay,
                model_name,
                attempt + 1,
                max_attempts,
                e,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _exam_prep_generate_with_retry(
    *,
    contents: Any,
    config: Any,
    attempts_per_model: int = 6,
    base_delay_sec: float = 2.0,
) -> Any:
    """
    Gemini generate_content for exam-prep flows with backoff on 429/overload.

    Models from GEMINI_EXAM_PREP_MODELS, then GEMINI_ROTATING_MODELS, then defaults.
    Each retry uses the next model in the chain (round-robin).
    """
    return await _gemini_generate_content_with_model_rotation(
        contents=contents,
        config=config,
        models=_exam_prep_model_chain(),
        max_rounds=attempts_per_model,
        base_delay_sec=base_delay_sec,
        log_prefix="exam_prep Gemini",
    )


async def extract_exam_topics(subject: str, material_texts: list[str]) -> list[dict]:
    """
    Extract key topics/chapters from exam prep materials using AI.
    Returns a list of {title, description} dictionaries.
    """
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
        response = await _exam_prep_generate_with_retry(
            contents=prompt,
            config=_types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        topics = _parse_llm_json_array(text)
        if not topics:
            return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]

        return [
            {
                "title": t.get("title", "Unknown Topic"),
                "description": t.get("description", ""),
            }
            for t in topics
            if isinstance(t, dict) and t.get("title")
        ] or [{"title": f"General {subject}", "description": f"Overview of {subject}"}]

    except Exception as e:
        logger.warning("extract_exam_topics failed (using fallback topic): %s", e, exc_info=True)
        return [{"title": f"General {subject}", "description": f"Overview of {subject}"}]


async def extract_past_paper_questions(text: str, subject: str) -> list[dict]:
    """
    Parse a past exam paper text into individual questions with answers/options.
    Returns structured question data ready for storage.
    """
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
        response = await _exam_prep_generate_with_retry(
            contents=prompt,
            config=_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.1,
            ),
        )
        text_response = (response.text or "").strip()
        questions = _parse_llm_json_array(text_response)
        if not questions:
            return []

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

    except Exception as e:
        logger.warning("extract_past_paper_questions failed: %s", e, exc_info=True)
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
    if genai is None:
        return []

    existing_str = ""
    if existing_questions:
        existing_str = "\n\nAVOID duplicating these existing questions:\n" + "\n".join(
            f"- {q}" for q in existing_questions[:10]
        )

    prompt = f"""Generate {count} exam-style questions for the topic "{topic_title}" in the subject "{subject}".

Every question MUST test content specific to "{topic_title}" — not generic {subject} trivia unrelated to this topic.

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
        response = await _exam_prep_generate_with_retry(
            contents=prompt,
            config=_types.GenerateContentConfig(
                max_output_tokens=4000,
                temperature=0.4,
            ),
        )
        text_response = (response.text or "").strip()
        questions = _parse_llm_json_array(text_response)
        if not questions:
            logger.warning(
                "generate_exam_questions: no JSON array parsed for topic=%r subject=%r (preview=%r)",
                topic_title,
                subject,
                text_response[:400].replace("\n", " "),
            )
            return []

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

    except Exception as e:
        logger.warning("generate_exam_questions failed: %s", e, exc_info=True)
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
    if not gemini_api_key():
        logger.warning(
            "get_schedule_review_suggestions skipped for user %s: GEMINI_API_KEY not set",
            user_id,
        )
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
    # Upcoming schedule: any block overlapping [now, week_end] (not only fully inside the window)
    schedules = await db.scheduleblock.find_many(
        where={
            "userId": user_id,
            "AND": [{"startAt": {"lt": week_end}}, {"endAt": {"gt": now}}],
        },
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
        response = await _gemini_generate_content_with_model_rotation(
            contents=prompt,
            config=_types.GenerateContentConfig(
                max_output_tokens=600,
                temperature=0.3,
            ),
            models=_schedule_ai_model_chain(),
            max_rounds=6,
            base_delay_sec=2.0,
            log_prefix="schedule_review Gemini",
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
        logger.warning("get_schedule_review_suggestions failed: %s", e, exc_info=True)
        return []


class _LazyGeminiService:
    """Lazy proxy to avoid import-time side effects/crashes."""

    _instance: GeminiService | None = None

    def _get(self) -> GeminiService:
        if self._instance is None:
            self._instance = GeminiService()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get(), name)

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
        return await self._get().get_chat_response_with_tools(
            history,
            user_message,
            context,
            user_id,
            user_name,
            image_url,
            progress_callback,
            stream_callback,
        )


# Backwards-compatible global proxy
llm_service = _LazyGeminiService()


def chat_with_tools_provider() -> ChatWithToolsProvider:
    """Return the process-wide :class:`ChatWithToolsProvider` (Gemini today)."""
    return cast(ChatWithToolsProvider, llm_service)
