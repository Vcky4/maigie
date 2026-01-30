"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

import os
import warnings

import httpx  # <--- Added for image download

# Suppress the Google Gemini deprecation warning temporarily
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import google.generativeai as genai

from datetime import UTC

from fastapi import HTTPException
from google.generativeai.types import HarmBlockThreshold, HarmCategory

# Configure API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# =============================================================================
# MODULAR PROMPT SYSTEM
# =============================================================================
# Split into base prompt + intent-specific modules for efficiency.
# This reduces token usage by ~70% for most requests.

# Model configuration
MODELS = {
    "lite": "gemini-2.0-flash-lite",  # Fast, cheap - greetings, list queries
    "flash": "gemini-2.0-flash",  # Smarter - course gen, notes, schedules
}

# Intent to model mapping
# Using Flash for all action-related intents to ensure strict format compliance
INTENT_MODEL_MAP = {
    # Simple intents -> Flash Lite (no actions needed)
    "greeting": "lite",
    "list_query": "lite",
    "clarification": "lite",
    # ALL action intents -> Flash (requires strict format compliance)
    "course_generation": "flash",
    "note_creation": "flash",
    "note_actions": "flash",
    "schedule_creation": "flash",
    "goal_creation": "flash",
    "resource_recommendation": "flash",
    "conversation": "flash",  # Default for unknown - may contain actions
}

# Base prompt (~300 tokens) - always included
BASE_PROMPT = """You are Maigie, an intelligent study companion.
You help students organize learning, generate courses, manage schedules, create notes, and summarize content.

IMPORTANT: The current date/time is provided in each message context. Always use relative dates based on this.

{intent_prompt}
"""

# Intent-specific prompts
INTENT_PROMPTS = {
    # Conversation prompt (~150 tokens) - for greetings and casual chat
    "conversation": """Be friendly, supportive, and encouraging. Respond naturally to greetings and casual conversation.
If the user seems to need help, proactively suggest actions you can take (like creating courses, schedules, or goals).
Wait for their confirmation before taking action.""",
    # Course generation prompt (~400 tokens)
    "course_generation": """Create courses using ONLY the action tags below.

CRITICAL FORMAT REQUIREMENTS:
- You MUST wrap ALL action JSON in <<<ACTION_START>>> and <<<ACTION_END>>> tags
- NEVER use markdown code blocks like ```json or ```
- NEVER output raw JSON without the tags
- The JSON must be valid and properly formatted

Example format:
<<<ACTION_START>>>
{"type": "create_course", "data": {"title": "Course Title", "description": "Brief description", "difficulty": "BEGINNER", "modules": [{"title": "Module 1", "topics": ["Topic 1", "Topic 2"]}, {"title": "Module 2", "topics": ["Topic 3", "Topic 4"]}]}}
<<<ACTION_END>>>

Create 3-6 modules with 3-5 topics each. For multiple actions: {"actions": [{...}, {...}]}.""",
    # Schedule creation prompt (~350 tokens)
    "schedule_creation": """Create schedule blocks using ONLY the action tags below.

CRITICAL FORMAT REQUIREMENTS:
- You MUST wrap ALL action JSON in <<<ACTION_START>>> and <<<ACTION_END>>> tags
- NEVER use markdown code blocks like ```json or ```
- NEVER output raw JSON without the tags
- The JSON must be valid and properly formatted

For multiple schedules, wrap them in an actions array:

<<<ACTION_START>>>
{"actions": [
  {"type": "create_schedule", "data": {"title": "Session 1", "startAt": "2026-01-30T18:00:00Z", "endAt": "2026-01-30T20:00:00Z", "courseId": "$courseId"}},
  {"type": "create_schedule", "data": {"title": "Session 2", "startAt": "2026-01-31T18:00:00Z", "endAt": "2026-01-31T20:00:00Z", "courseId": "$courseId"}}
]}
<<<ACTION_END>>>

IMPORTANT: Use $courseId, $goalId placeholders ONLY if creating the course/goal in the SAME batch (before the schedule).
If NOT creating a course in the batch, OMIT the courseId field entirely (don't use placeholder).
Default schedule time: evenings 6-8 PM.""",
    # Note actions prompt (~300 tokens)
    "note_actions": """CRITICAL FORMAT REQUIREMENTS:
- You MUST wrap ALL action JSON in <<<ACTION_START>>> and <<<ACTION_END>>> tags
- NEVER use markdown code blocks like ```json or ```
- NEVER output raw JSON without the tags
- The JSON must be valid and properly formatted

Examples:

CREATE NOTE: <<<ACTION_START>>>{"type": "create_note", "data": {"title": "...", "content": "markdown content", "topicId": "from context"}}<<<ACTION_END>>>

RETAKE NOTE: <<<ACTION_START>>>{"type": "retake_note", "data": {"noteId": "from context"}}<<<ACTION_END>>>

ADD SUMMARY: <<<ACTION_START>>>{"type": "add_summary", "data": {"noteId": "from context"}}<<<ACTION_END>>>

ADD TAGS: <<<ACTION_START>>>{"type": "add_tags", "data": {"noteId": "from context", "tags": ["Tag1", "Tag2"]}}<<<ACTION_END>>>

Get IDs from the context provided in the message.""",
    # Goal creation prompt (~200 tokens)
    "goal_creation": """CRITICAL FORMAT REQUIREMENTS:
- You MUST wrap ALL action JSON in <<<ACTION_START>>> and <<<ACTION_END>>> tags
- NEVER use markdown code blocks like ```json or ```
- NEVER output raw JSON without the tags
- The JSON must be valid and properly formatted

Example:
<<<ACTION_START>>>
{"type": "create_goal", "data": {"title": "Clear, actionable goal", "description": "optional", "targetDate": "ISO date if deadline", "courseId": "from context"}}
<<<ACTION_END>>>

Make goals specific and measurable.""",
    # Resource recommendation prompt (~250 tokens)
    "resource_recommendation": """CRITICAL FORMAT REQUIREMENTS:
- You MUST wrap ALL action JSON in <<<ACTION_START>>> and <<<ACTION_END>>> tags
- NEVER use markdown code blocks like ```json or ```
- NEVER output raw JSON without the tags
- The JSON must be valid and properly formatted

Example:
<<<ACTION_START>>>
{"type": "recommend_resources", "data": {"query": "topic from user message or context", "courseId": "actual_id_from_context", "limit": 10}}
<<<ACTION_END>>>

IMPORTANT: 
- Extract query from user message or use course/topic from context
- If courseId is provided, it MUST be an actual ID (starts with 'c'), NOT a course title
- If no valid courseId is available in context, OMIT the courseId field entirely
- DO NOT ask for clarification, DO NOT use course titles as IDs""",
    # List query prompt (~100 tokens) - handled by code, minimal prompt needed
    "list_query": """Answer the user's question about their data (courses, goals, schedule, notes, resources) conversationally.
The system will provide the data - just format it nicely for the user.""",
    # Greeting prompt (~50 tokens)
    "greeting": """Respond warmly to the greeting. Be friendly and ask how you can help with their studies today.""",
    # Clarification prompt (~50 tokens)
    "clarification": """Clarify or elaborate on your previous response. Be helpful and patient.""",
}

# Full legacy prompt for backward compatibility (used when intent is unknown or complex)
FULL_SYSTEM_INSTRUCTION = """You are Maigie, an intelligent study companion.
Your goal is to help students organize learning, generate courses, manage schedules, create notes, and summarize content.

IMPORTANT DATE CONTEXT:
- The user's current date and time will be provided in the context of each conversation
- When creating schedules, goals, or any date-related actions, ALWAYS use dates relative to the CURRENT DATE provided in the context
- NEVER use hardcoded years like 2025 or 2024 - always calculate dates based on the current date provided

CRITICAL FORMAT REQUIREMENTS FOR ACTIONS:
When the user requests actions (create, generate, schedule, etc.), you MUST:
1. Output JSON wrapped in <<<ACTION_START>>> and <<<ACTION_END>>> tags
2. NEVER use markdown code blocks like ```json or ```
3. NEVER output raw JSON without the tags
4. The JSON must be valid and properly formatted
5. Place the action block at the end of your response

MULTIPLE ACTIONS:
When a user request involves multiple related actions, use an array format:

<<<ACTION_START>>>
{
  "actions": [
    {"type": "create_course", "data": {...}},
    {"type": "create_goal", "data": {"title": "...", "courseId": "$courseId"}},
    {"type": "create_schedule", "data": {"title": "...", "startAt": "...", "endAt": "...", "courseId": "$courseId", "goalId": "$goalId"}}
  ]
}
<<<ACTION_END>>>

PLACEHOLDER USAGE:
- Use "$courseId", "$goalId", "$topicId", "$noteId" ONLY to reference IDs from previous actions in the SAME batch
- If NOT creating a course/goal in the batch, OMIT the courseId/goalId field (don't use placeholder)
- NEVER use course/goal titles as IDs - only use actual IDs (starting with 'c')

AVAILABLE ACTIONS:
1. create_course: {"title", "description", "difficulty", "modules": [{"title", "topics": [...]}]}
2. create_note: {"title", "content", "topicId", "courseId?", "summary?"}
3. retake_note: {"noteId"}
4. add_summary: {"noteId"}
5. add_tags: {"noteId", "tags": [...]}
6. recommend_resources: {"query", "topicId?", "courseId?", "limit"}
7. create_goal: {"title", "description?", "targetDate?", "courseId?", "topicId?"}
8. create_schedule: {"title", "description?", "startAt", "endAt", "recurringRule?", "courseId?", "topicId?", "goalId?"}

DISTINGUISHING MESSAGE TYPES:
- CASUAL CONVERSATION (greetings, thanks, emotions) → Respond naturally, NO action
- QUERIES (what courses do I have?, show goals) → Answer conversationally, NO action
- ACTION REQUESTS (create, generate, schedule, add) → Create appropriate action

Key indicators for ACTIONS: create, make, generate, add, new, set up, build, schedule, plan, block out
Key indicators for NO ACTION: hi, hello, thanks, what, how, show, list, tell me, explain

Be proactive - suggest helpful actions based on context, but wait for confirmation before acting.
Keep responses encouraging and brief. Be a friendly, supportive study companion."""


def get_prompt_for_intent(intent: str) -> str:
    """Get the appropriate system prompt for the given intent."""
    intent_prompt = INTENT_PROMPTS.get(intent, INTENT_PROMPTS["conversation"])
    return BASE_PROMPT.format(intent_prompt=intent_prompt)


def get_model_for_intent(intent: str) -> str:
    """Get the appropriate model name for the given intent."""
    model_key = INTENT_MODEL_MAP.get(intent, "flash")
    return MODELS[model_key]


# Legacy system instruction (kept for backward compatibility)
SYSTEM_INSTRUCTION = FULL_SYSTEM_INSTRUCTION


class GeminiService:
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name="models/gemini-3-flash-preview", system_instruction=SYSTEM_INSTRUCTION
        )

        # Safety settings (block hate speech, etc.)
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        }

        # Context caching for frequently used prompts
        # Maps intent -> (cached_model, cache_time)
        self._model_cache: dict[str, tuple[genai.GenerativeModel, float]] = {}
        self._cache_ttl = 3600  # 1 hour TTL for cached models

    def _get_cached_model(self, intent: str) -> genai.GenerativeModel:
        """
        Get or create a cached model for the given intent.
        Uses in-memory caching to avoid recreating models for each request.
        """
        import time

        current_time = time.time()

        # Check if we have a valid cached model
        if intent in self._model_cache:
            cached_model, cache_time = self._model_cache[intent]
            if current_time - cache_time < self._cache_ttl:
                return cached_model

        # Create new model with intent-specific prompt
        model_name = get_model_for_intent(intent)
        system_prompt = get_prompt_for_intent(intent)

        cached_model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
        )

        # Cache the model
        self._model_cache[intent] = (cached_model, current_time)

        return cached_model

    def clear_model_cache(self):
        """Clear the model cache."""
        self._model_cache.clear()

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

    async def get_chat_response_with_intent(
        self,
        history: list,
        user_message: str,
        context: dict = None,
        intent: str = None,
    ) -> tuple[str, dict]:
        """
        Send message to Gemini with intent-based model and prompt selection.
        Uses smaller, intent-specific prompts for efficiency.

        Args:
            history: Chat history
            user_message: User's message
            context: Optional context dict
            intent: Pre-classified intent (if None, uses default prompt)

        Returns:
            Tuple of (response_text, usage_info)
        """
        try:
            # Select model based on intent, using cached models for efficiency
            if intent:
                # Use cached model for known intents
                intent_model = self._get_cached_model(intent)
                model_name = get_model_for_intent(intent)
            else:
                # Fall back to default model with full system instruction
                intent_model = self.model
                model_name = MODELS["flash"]

            # Build enhanced message with context
            enhanced_message = user_message
            from datetime import datetime

            current_datetime = datetime.now(UTC)
            current_date_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M UTC")

            context_parts = [f"Current Date & Time: {current_date_str}"]

            if context:
                if context.get("pageContext"):
                    context_parts.append(f"Current Page Context: {context['pageContext']}")
                if context.get("courseTitle"):
                    context_parts.append(f"Current Course: {context['courseTitle']}")
                elif context.get("courseId"):
                    context_parts.append(f"Current Course ID: {context['courseId']}")
                if context.get("topicTitle"):
                    context_parts.append(f"Current Topic: {context['topicTitle']}")
                elif context.get("topicId"):
                    context_parts.append(f"Current Topic ID: {context['topicId']}")
                if context.get("noteTitle"):
                    context_parts.append(f"Current Note: {context['noteTitle']}")
                elif context.get("noteId"):
                    context_parts.append(f"Current Note ID: {context['noteId']}")
                if context.get("retrieved_items"):
                    context_parts.append("\nRelevant Items:")
                    for item in context["retrieved_items"]:
                        context_parts.append(str(item))

            if context_parts:
                context_str = "\n".join(context_parts)
                enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

            # Start chat and send message
            chat = intent_model.start_chat(history=history)
            response = await chat.send_message_async(
                enhanced_message, safety_settings=self.safety_settings
            )

            # Extract usage info
            usage_info = {
                "input_tokens": 0,
                "output_tokens": 0,
                "model_name": model_name,
                "intent": intent,
            }

            if hasattr(response, "usage_metadata"):
                usage_metadata = response.usage_metadata
                if hasattr(usage_metadata, "prompt_token_count"):
                    usage_info["input_tokens"] = usage_metadata.prompt_token_count or 0
                if hasattr(usage_metadata, "candidates_token_count"):
                    usage_info["output_tokens"] = usage_metadata.candidates_token_count or 0

            return response.text, usage_info

        except Exception as e:
            print(f"Gemini Error (intent-based): {e}")
            raise HTTPException(status_code=500, detail="AI Service unavailable")

    async def stream_chat_response(
        self,
        history: list,
        user_message: str,
        context: dict = None,
        intent: str = None,
    ):
        """
        Stream chat response chunks as they arrive from Gemini.

        Args:
            history: Chat history
            user_message: User's message
            context: Optional context dict
            intent: Pre-classified intent (if None, uses default model)

        Yields:
            Tuple of (chunk_text, is_final, usage_info)
            - chunk_text: The text chunk
            - is_final: Whether this is the final chunk
            - usage_info: Token usage info (only populated on final chunk)
        """
        try:
            # Select model based on intent
            if intent:
                streaming_model = self._get_cached_model(intent)
                model_name = get_model_for_intent(intent)
            else:
                streaming_model = self.model
                model_name = MODELS["flash"]

            # Build enhanced message with context
            enhanced_message = user_message
            from datetime import datetime

            current_datetime = datetime.now(UTC)
            current_date_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M UTC")

            context_parts = [f"Current Date & Time: {current_date_str}"]

            if context:
                if context.get("pageContext"):
                    context_parts.append(f"Current Page Context: {context['pageContext']}")
                if context.get("courseTitle"):
                    context_parts.append(f"Current Course: {context['courseTitle']}")
                elif context.get("courseId"):
                    context_parts.append(f"Current Course ID: {context['courseId']}")
                if context.get("topicTitle"):
                    context_parts.append(f"Current Topic: {context['topicTitle']}")
                elif context.get("topicId"):
                    context_parts.append(f"Current Topic ID: {context['topicId']}")
                if context.get("noteTitle"):
                    context_parts.append(f"Current Note: {context['noteTitle']}")
                elif context.get("noteId"):
                    context_parts.append(f"Current Note ID: {context['noteId']}")
                if context.get("retrieved_items"):
                    context_parts.append("\nRelevant Items:")
                    for item in context["retrieved_items"]:
                        context_parts.append(str(item))

            if context_parts:
                context_str = "\n".join(context_parts)
                enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

            # Start chat and send message with streaming
            chat = streaming_model.start_chat(history=history)

            # Use streaming response
            response = await chat.send_message_async(
                enhanced_message,
                safety_settings=self.safety_settings,
                stream=True,
            )

            # Stream chunks
            full_text = ""
            async for chunk in response:
                if chunk.text:
                    full_text += chunk.text
                    yield chunk.text, False, None

            # Final chunk with usage info
            usage_info = {
                "input_tokens": len(enhanced_message) // 4,  # Estimate
                "output_tokens": len(full_text) // 4,  # Estimate
                "model_name": model_name,
                "intent": intent,
            }

            # Try to get actual usage from response if available
            if hasattr(response, "usage_metadata"):
                usage_metadata = response.usage_metadata
                if hasattr(usage_metadata, "prompt_token_count"):
                    usage_info["input_tokens"] = (
                        usage_metadata.prompt_token_count or usage_info["input_tokens"]
                    )
                if hasattr(usage_metadata, "candidates_token_count"):
                    usage_info["output_tokens"] = (
                        usage_metadata.candidates_token_count or usage_info["output_tokens"]
                    )

            yield "", True, usage_info

        except Exception as e:
            print(f"Gemini Streaming Error: {e}")
            raise HTTPException(status_code=500, detail="AI Streaming unavailable")

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

    async def detect_list_query_intent(self, user_message: str) -> dict:
        """
        Use AI to detect if the user is asking to view/list their data.
        Returns the detected intent type or None if not a list query.

        This allows natural language like:
        - "what courses am I taking?" -> courses
        - "do I have anything scheduled?" -> schedule
        - "what have I saved?" -> resources
        - "any goals I should focus on?" -> goals
        - "what notes do I have on python?" -> notes

        Args:
            user_message: The user's message to analyze

        Returns:
            Dictionary with 'intent' (courses|goals|schedule|notes|resources|none),
            'is_list_query' (bool), and 'total_tokens' (int)
        """
        try:
            classification_prompt = f"""Classify this user message. Is the user asking to VIEW or LIST their existing SAVED data?

User message: "{user_message}"

IMPORTANT: Only classify as a list query if the user CLEARLY wants to SEE/VIEW/LIST their SAVED/EXISTING items.

CRITICAL - NEVER classify as list query if the message contains these ACTION words:
- "create", "make", "generate", "build", "add", "new", "write", "set up"
- "schedule", "plan", "block time"
- "summarize", "explain", "describe"
- Any request to DO something with content (like an image)

Examples that are NOT list queries (respond "none"):
- "create a note" -> none (action: create)
- "create a note explaining this image" -> none (action: create)
- "make me a schedule" -> none (action: create)
- "generate a course on farming" -> none (action: create)
- "add a goal" -> none (action: create)

Examples that ARE list queries:
- "show my notes" -> notes
- "what courses do I have?" -> courses
- "my goals" -> goals

CRITICAL for resources - be STRICT:
- CLEARLY SAVED: "show my resources", "my saved resources", "what resources do I have", "resources I saved" -> resources
- CLEARLY NEW: "find resources", "recommend resources", "suggest resources", "search for resources" -> none
- AMBIGUOUS (could be saved OR new):
  * "get me resources on X" -> none (ambiguous!)
  * "resources for Y" -> none (ambiguous!)
  * "show resources about Z" -> none (ambiguous!)
  * Just "resources" -> none (ambiguous!)
- When in doubt, respond with "none" to let the AI ask for clarification

Respond with ONLY one word from this list:
- courses (viewing/listing courses, subjects, classes, what they're learning)
- goals (viewing/listing goals, objectives, targets, milestones)
- schedule (viewing/listing schedule, calendar, upcoming events, what's planned)
- notes (viewing/listing notes, writings, documentation)
- resources (ONLY when user CLEARLY wants their SAVED resources - use words like "my", "saved", "I have")
- none (not a list query, wants new recommendations, or AMBIGUOUS - let AI ask for clarification)

Answer:"""

            minimal_model = genai.GenerativeModel(
                "gemini-2.0-flash-lite",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=10,
                    temperature=0.1,  # Low temperature for consistent classification
                ),
                safety_settings=self.safety_settings,
            )

            response = await minimal_model.generate_content_async(
                classification_prompt, safety_settings=self.safety_settings
            )

            # Parse the response
            intent = response.text.strip().lower() if response.text else "none"

            # Validate intent is one of expected values
            valid_intents = ["courses", "goals", "schedule", "notes", "resources", "none"]
            if intent not in valid_intents:
                # Try to extract a valid intent from the response
                for valid in valid_intents:
                    if valid in intent:
                        intent = valid
                        break
                else:
                    intent = "none"

            # Calculate tokens
            input_tokens = len(classification_prompt) // 4
            output_tokens = len(response.text) // 4 if response.text else 0
            total_tokens = input_tokens + output_tokens

            return {
                "intent": intent,
                "is_list_query": intent != "none",
                "total_tokens": total_tokens,
            }

        except Exception as e:
            print(f"Intent detection error: {e}")
            return {"intent": "none", "is_list_query": False, "total_tokens": 0}

    async def classify_intent(self, user_message: str, context: dict = None) -> dict:
        """
        Classify user intent into categories for routing to appropriate model and prompt.

        Returns:
            Dictionary with:
            - 'intent': The classified intent type
            - 'model': The recommended model ('lite' or 'flash')
            - 'model_name': Full model name (e.g., 'gemini-2.0-flash-lite')
            - 'prompt_key': Key to look up in INTENT_PROMPTS
            - 'total_tokens': Tokens used for classification
        """
        try:
            # Build context hint for better classification
            context_hint = ""
            if context:
                if context.get("noteId") or context.get("noteTitle"):
                    context_hint = " (User is viewing a note)"
                elif context.get("topicId") or context.get("topicTitle"):
                    context_hint = " (User is viewing a topic)"
                elif context.get("courseId") or context.get("courseTitle"):
                    context_hint = " (User is viewing a course)"

            classification_prompt = f"""Classify this user message into one category.{context_hint}

Message: "{user_message}"

Categories:
- greeting: Hi, hello, hey, good morning, how are you, thanks, bye
- list_query: Show my courses/goals/schedule/notes, what do I have, what am I studying
- course_generation: Create/generate/make a course, I want to learn [subject]
- schedule_creation: Schedule, plan, block time, set up study sessions, create schedule
- goal_creation: Set a goal, create goal, my goal is, I want to achieve
- note_actions: Create/retake/rewrite note, add summary, add tags, summarize this
- resource_recommendation: Find/recommend/suggest resources, search for materials
- clarification: What do you mean, can you explain, tell me more, I don't understand
- conversation: Everything else - questions, discussion, casual chat

Respond with ONLY one word from the categories above.

Answer:"""

            minimal_model = genai.GenerativeModel(
                MODELS["lite"],
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=10,
                    temperature=0.1,
                ),
                safety_settings=self.safety_settings,
            )

            response = await minimal_model.generate_content_async(
                classification_prompt, safety_settings=self.safety_settings
            )

            # Parse the response
            intent = response.text.strip().lower() if response.text else "conversation"

            # Validate and normalize intent
            valid_intents = [
                "greeting",
                "list_query",
                "course_generation",
                "schedule_creation",
                "goal_creation",
                "note_actions",
                "note_creation",
                "resource_recommendation",
                "clarification",
                "conversation",
            ]

            if intent not in valid_intents:
                # Try to extract a valid intent from the response
                for valid in valid_intents:
                    if valid in intent or intent in valid:
                        intent = valid
                        break
                else:
                    # Map common variations
                    intent_map = {
                        "course": "course_generation",
                        "schedule": "schedule_creation",
                        "goal": "goal_creation",
                        "note": "note_actions",
                        "resource": "resource_recommendation",
                        "resources": "resource_recommendation",
                    }
                    intent = intent_map.get(intent, "conversation")

            # Get model and prompt for this intent
            model_key = INTENT_MODEL_MAP.get(intent, "flash")
            model_name = MODELS[model_key]
            prompt_key = intent

            # Calculate tokens
            input_tokens = len(classification_prompt) // 4
            output_tokens = len(response.text) // 4 if response.text else 0
            total_tokens = input_tokens + output_tokens

            return {
                "intent": intent,
                "model": model_key,
                "model_name": model_name,
                "prompt_key": prompt_key,
                "total_tokens": total_tokens,
            }

        except Exception as e:
            print(f"Intent classification error: {e}")
            # Default to conversation with flash model
            return {
                "intent": "conversation",
                "model": "flash",
                "model_name": MODELS["flash"],
                "prompt_key": "conversation",
                "total_tokens": 0,
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


# Global instance
llm_service = GeminiService()
