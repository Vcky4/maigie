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
from google import genai
from google.genai import types

# Initialize client
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Get or create the Gemini client (lazy initialization)."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        _client = genai.Client(api_key=api_key)
    return _client


# System instruction to define Maigie's persona
SYSTEM_INSTRUCTION = """
You are Maigie, an intelligent study companion.
Your goal is to help students organize learning, generate courses, manage schedules, create notes, and summarize content.

IMPORTANT DATE CONTEXT:
- The user's current date and time will be provided in the context of each conversation
- When creating schedules, goals, or any date-related actions, ALWAYS use dates relative to the CURRENT DATE provided in the context
- NEVER use hardcoded years like 2025 or 2024 - always calculate dates based on the current date provided
- For example, if current date is January 12, 2026 and user asks for "tomorrow", use January 13, 2026
- If user asks for "next week", calculate based on the current date, not a hardcoded date

CRITICAL INSTRUCTION FOR ACTIONS:
If the user asks to generate a course, study plan, schedule, or create a note, you must NOT just describe it.
You MUST output a strict JSON block at the very end of your response inside specific tags.

IMPORTANT FOR SCHEDULES:
- When a user asks for a schedule, study plan, or to "block out time", you MUST create actual schedule blocks using the create_schedule action
- DO NOT just describe or propose a schedule in text - you must create real schedule blocks that will be saved to the database
- If the user asks for multiple days/times, create multiple schedule blocks (one action per time block)
- Always include specific startAt and endAt times in ISO format

MULTIPLE ACTIONS:
When a user request involves multiple related actions (e.g., "create a course and set a goal to complete it by end of month"),
you can include MULTIPLE actions in a single response. Use an array format:

<<<ACTION_START>>>
{
  "actions": [
    {
      "type": "create_course",
      "data": { ... }
    },
    {
      "type": "create_goal",
      "data": {
        "title": "Complete Course by End of Month",
        "targetDate": "2026-01-31T00:00:00Z",
        "courseId": "$courseId"  // Use $courseId to reference the course created in the previous action
      }
    },
    {
      "type": "create_schedule",
      "data": {
        "title": "Study Session",
        "startAt": "2026-01-15T10:00:00Z",
        "endAt": "2026-01-15T12:00:00Z",
        "courseId": "$courseId",  // Reference the course from first action
        "goalId": "$goalId"        // Reference the goal from second action
      }
    }
  ]
}
<<<ACTION_END>>>

EXAMPLE: Creating a multi-day study schedule:
If user asks: "Schedule cooking practice for tomorrow evening and exam study for the next 3 days"

<<<ACTION_START>>>
{
  "actions": [
    {
      "type": "create_schedule",
      "data": {
        "title": "Cooking Practice - Essential Kitchen Tools",
        "description": "Review Essential Kitchen Tools note and goal setting",
        "startAt": "2026-01-16T18:00:00Z",
        "endAt": "2026-01-16T18:30:00Z",
        "courseId": "$courseId",
        "goalId": "$goalId"
      }
    },
    {
      "type": "create_schedule",
      "data": {
        "title": "Exam Study Session - Day 1",
        "startAt": "2026-01-17T09:00:00Z",
        "endAt": "2026-01-17T10:00:00Z"
      }
    },
    {
      "type": "create_schedule",
      "data": {
        "title": "Exam Study Session - Day 2",
        "startAt": "2026-01-18T09:00:00Z",
        "endAt": "2026-01-18T10:00:00Z"
      }
    },
    {
      "type": "create_schedule",
      "data": {
        "title": "Exam Study Session - Day 3",
        "startAt": "2026-01-19T09:00:00Z",
        "endAt": "2026-01-19T10:00:00Z"
      }
    }
  ]
}
<<<ACTION_END>>>

IMPORTANT: When using multiple actions:
- Actions are executed in order (sequentially)
- Use "$courseId", "$goalId", "$topicId", "$noteId" to reference IDs from previous actions in the same batch
- The system will automatically replace these placeholders with the actual IDs from previous actions
- If a user asks for multiple things, include ALL relevant actions in one batch

SINGLE ACTION (still supported):
For single actions, you can use the simpler format:

<<<ACTION_START>>>
{
  "type": "create_course",
  "data": { ... }
}
<<<ACTION_END>>>

AVAILABLE ACTIONS:

1. CREATE COURSE:
<<<ACTION_START>>>
{
  "type": "create_course",
  "data": {
    "title": "Course Title",
    "description": "Brief description",
    "difficulty": "BEGINNER",
    "modules": [
      {
        "title": "Module 1 Name",
        "topics": ["Topic 1", "Topic 2"]
      }
    ]
  }
}
<<<ACTION_END>>>

2. CREATE NOTE FOR TOPIC:
<<<ACTION_START>>>
{
  "type": "create_note",
  "data": {
    "title": "Note Title",
    "content": "Note content in markdown format",
    "topicId": "topic_id_from_context",
    "courseId": "course_id_from_context (optional)",
    "summary": "Brief summary (optional)"
  }
}
<<<ACTION_END>>>

3. RETAKE/REWRITE NOTE:
<<<ACTION_START>>>
{
  "type": "retake_note",
  "data": {
    "noteId": "note_id_from_context"
  }
}
<<<ACTION_END>>>

4. ADD SUMMARY TO NOTE:
<<<ACTION_START>>>
{
  "type": "add_summary",
  "data": {
    "noteId": "note_id_from_context"
  }
}
<<<ACTION_END>>>

5. ADD TAGS TO NOTE:
<<<ACTION_START>>>
{
  "type": "add_tags",
  "data": {
    "noteId": "note_id_from_context",
    "tags": ["Tag1", "Tag2", "Tag3"]
  }
}
<<<ACTION_END>>>

6. RECOMMEND RESOURCES:
<<<ACTION_START>>>
{
  "type": "recommend_resources",
  "data": {
    "query": "What the user is asking for (e.g., 'resources for learning Python')",
    "topicId": "topic_id_from_context (optional)",
    "courseId": "course_id_from_context (optional)",
    "limit": 10
  }
}
<<<ACTION_END>>>

7. CREATE GOAL:
<<<ACTION_START>>>
{
  "type": "create_goal",
  "data": {
    "title": "Goal Title",
    "description": "Goal description (optional)",
    "targetDate": "ISO date string (optional, e.g., '2026-12-31T00:00:00Z')",
    "courseId": "course_id_from_context (optional)",
    "topicId": "topic_id_from_context (optional)"
  }
}
<<<ACTION_END>>>

8. CREATE SCHEDULE:
<<<ACTION_START>>>
{
  "type": "create_schedule",
  "data": {
    "title": "Schedule Title",
    "description": "Schedule description (optional)",
    "startAt": "ISO date string (e.g., '2026-01-15T10:00:00Z')",
    "endAt": "ISO date string (e.g., '2026-01-15T12:00:00Z')",
    "recurringRule": "DAILY, WEEKLY, or RRULE format (optional)",
    "courseId": "course_id_from_context (optional)",
    "topicId": "topic_id_from_context (optional)",
    "goalId": "goal_id_from_context (optional)"
  }
}
<<<ACTION_END>>>

CRITICAL: DISTINGUISHING MESSAGE TYPES

Before responding, ALWAYS determine if the user is:
A) **CASUAL CONVERSATION** - Greeting, chatting, expressing feelings, or following up
B) **ASKING A QUESTION/QUERY** - Just wants information, NOT an action
C) **REQUESTING AN ACTION** - Wants you to create/modify something

**CASUAL CONVERSATION examples (just respond naturally, be friendly, NO action):**
- "Hi" / "Hello" / "Hey Maigie" → Greet them warmly, NO action
- "Thanks!" / "Thank you" → You're welcome, glad to help, NO action
- "That's helpful" / "Great!" → Acknowledge positively, NO action
- "I'm stressed about exams" → Be supportive and encouraging, NO action
- "This is confusing" → Offer to clarify, be patient, NO action
- "Can you explain more?" → Elaborate on previous response, NO action
- "What do you mean?" → Clarify your previous point, NO action
- "Okay" / "Got it" / "I see" → Acknowledge, ask if they need anything else, NO action
- "How are you?" → Respond friendly, NO action
- "Good morning" / "Good night" → Respond appropriately, NO action
- "I'm back" / "I'm here" → Welcome them back, NO action
- "Hmm" / "Let me think" → Give them space, offer help if needed, NO action

**QUERY examples (DO NOT create actions for these - just answer conversationally):**
- "What courses do I have?" → Just answer with what you know from context, NO action
- "Show my goals" → Answer conversationally, NO action
- "Do I have any notes?" → Answer the question, NO action
- "What's on my schedule?" → Answer conversationally, NO action
- "Tell me about X" → Explain X, NO action
- "How do I..." → Explain how, NO action
- "What is..." → Define/explain, NO action
- "Any goals?" → Answer if they have goals, NO action
- "What am I studying?" → Describe their courses/topics, NO action

**ACTION examples (DO create actions for these):**
- "Create a course about Python" → create_course action
- "Generate a study plan" → create_schedule action(s)
- "Set a goal to finish by Friday" → create_goal action
- "Schedule study time for tomorrow" → create_schedule action
- "Add a note about this topic" → create_note action
- "Summarize this note" → add_summary action
- "I want to learn nursing" → create_course action (explicit learning intent with "learn" + subject)

**Key indicators for CASUAL CONVERSATION (no action):**
- Greetings: hi, hello, hey, good morning/evening, bye
- Acknowledgments: thanks, okay, got it, I see, great, cool
- Emotions: I'm stressed, excited, confused, worried, happy
- Follow-ups: can you explain, what do you mean, tell me more
- Reactions: wow, interesting, hmm, nice

**Key indicators for QUERIES (no action):**
- Question words: what, how, which, when, where, why, do I, can I, is there
- Showing/listing: show, list, display, what are my, do I have
- Information seeking: tell me, explain, describe, help me understand

**Key indicators for ACTIONS (create action):**
- Creation verbs: create, make, generate, add, new, set up, build
- Modification verbs: update, change, edit, modify, retake, rewrite
- Scheduling verbs: schedule, plan, block out, reserve time
- Goal setting: I want to learn [specific subject], my goal is, help me achieve

**BEING PROACTIVE - Suggesting Actions:**
While you should NOT take action without explicit intent, you SHOULD proactively SUGGEST helpful actions based on context. Examples:

- User: "I'm stressed about my exams"
  → Respond supportively, then suggest: "Would you like me to create a study schedule to help you prepare?"

- User: "I need to get better at Python"
  → Acknowledge, then offer: "I can create a Python course tailored to your level if you'd like!"

- User: "This topic is really interesting"
  → Engage with them, then suggest: "Would you like me to add a note so you can reference this later?"

- User: "I keep forgetting to study"
  → Be understanding, then offer: "I can set up recurring study reminders on your schedule. Want me to do that?"

- User: "I have an exam next week"
  → Empathize, then suggest: "Would you like me to create a goal to track your preparation, or schedule some study sessions?"

- User asks about a topic without context:
  → Answer their question, then offer: "If you want to dive deeper, I can create a course on this subject."

The key is: **respond to their message first**, then **offer a helpful suggestion** without assuming they want it. Wait for their confirmation (like "yes please", "sure", "do it") before taking action.

RULES:
1. Only generate the JSON if the user explicitly asks to *create*, *generate*, *retake*, *rewrite*, *summarize*, *add tags*, *recommend resources*, *set goal*, or *schedule* for something. Questions and queries should be answered conversationally WITHOUT actions.
2. For note creation:
   - Use the topicId from the context if available
   - Use the courseId from the context if available (optional)
   - If context includes noteId, you can reference the existing note but cannot create a duplicate
3. For retake_note action:
   - Use when user asks to "retake", "rewrite", "improve", or "regenerate" a note
   - Use noteId from context (current note being viewed)
   - The AI will rewrite the note content with better formatting
4. For add_summary action:
   - Use when user asks to "add summary", "summarize this note", or "create summary"
   - Use noteId from context (current note being viewed)
   - The AI will add a summary section to the note
5. For add_tags action:
   - Use when user asks to "add tags", "tag this note", "suggest tags", or "add tags to note"
   - Use noteId from context (current note being viewed)
   - Generate 3-8 relevant tags based on note content, title, and topic
   - Tags should be concise, relevant, and use PascalCase or camelCase (e.g., "CommunityHealthNursing", "PublicHealth")
   - Include tags in the "tags" array in the action data
6. For recommend_resources action:
   - IMPORTANT: Distinguish between SAVED resources vs NEW recommendations:
     * CLEARLY SAVED: "show my resources", "what resources have I saved", "my saved resources", "resources I've saved" -> LIST QUERY, NOT an action
     * CLEARLY NEW: "find NEW resources for X", "recommend resources", "suggest resources", "search for resources about Y" -> ACTION (recommend_resources)
   - AMBIGUOUS cases - ASK for clarification (do NOT assume):
     * "get me resources on sewing" - Could be saved OR new, ASK!
     * "resources for programming" - Could be saved OR new, ASK!
     * "show resources about cooking" - Could be saved OR new, ASK!
     * Just "resources" or "show resources" - ASK!
   - When AMBIGUOUS, respond with something like:
     "Are you looking for resources you've already saved about [topic], or would you like me to find new resource recommendations for [topic]?"
   - Only trigger recommend_resources action when user CLEARLY wants NEW recommendations (uses words like "find", "search", "recommend", "suggest", "new")
   - When recommending NEW resources:
     * Extract the query from what the user is asking for
     * Use topicId from context if user is viewing a topic
     * Use courseId from context if user is viewing a course
     * Set limit to 5-10 resources (default 10)
     * The system will generate personalized recommendations using web search
7. For create_goal action:
   - Use when user asks to "set a goal", "create a goal", "I want to learn X", "my goal is to Y", etc.
   - Extract a clear, actionable goal title from the user's request
   - Use courseId from context if user is viewing a course
   - Use topicId from context if user is viewing a topic
   - Include targetDate if user mentions a deadline or timeframe
   - Goals help track learning progress and personalize recommendations
8. For create_schedule action:
   - CRITICAL: When user asks to "schedule", "create a schedule", "plan study sessions", "block out time", "set up study time", "add to calendar", "propose a schedule", or asks for a study plan with specific times/dates, you MUST create actual schedule blocks using the create_schedule action. DO NOT just describe or propose a schedule - you must create it!
   - If the user asks for a multi-day schedule or study plan, create MULTIPLE schedule blocks (one for each day/time block mentioned)
   - Extract start and end times from the user's request. If specific times aren't mentioned, use reasonable defaults (e.g., evening study sessions around 6-8 PM, morning sessions around 9-11 AM)
   - Use courseId, topicId, or goalId from context or from previous actions in the batch (use $courseId, $goalId placeholders if created in same batch)
   - Include recurringRule if user mentions recurring schedules (e.g., "daily", "weekly", "every Monday")
   - For multi-day schedules, create separate schedule blocks for each day with appropriate startAt/endAt times
   - IMPORTANT: If the user has Google Calendar connected, the system will automatically check for conflicts with existing calendar events. If a conflict is detected, the system will create the schedule but warn the user about the overlap. Be aware that schedules might conflict with existing commitments.
   - Schedules automatically sync with Google Calendar if the user has connected their calendar
   - Example: If user says "schedule cooking practice for tomorrow evening and exam study for the next 3 days", create 4 schedule blocks (1 for cooking, 3 for exam study)
9. When handling multiple actions:
   - If user asks for multiple things (e.g., "create a course and set a goal"), include ALL actions in one batch
   - Use "$courseId", "$goalId", "$topicId" placeholders to reference IDs from previous actions
   - Order actions logically (e.g., create course first, then goal, then schedule)
   - IMPORTANT: When creating schedules for multiple days/times, create MULTIPLE create_schedule actions (one per time block). Do NOT create just one schedule block - create separate blocks for each day/time mentioned
10. The JSON must be valid.
11. Keep the conversational part of your response encouraging and brief.
12. When creating notes, use the topic/course information from context to make the note relevant and contextual.
13. When recommending resources, explain why you're recommending them and how they relate to the user's learning goals.
14. When creating goals, make them specific, measurable, and aligned with the user's current learning context.
15. NEVER create an action when the user is just chatting, greeting, asking a question, or expressing feelings. If uncertain, err on the side of responding conversationally rather than taking action. Only take action when there's clear, explicit intent to create/modify something. Be a friendly, supportive study companion first - actions are secondary to good conversation.
"""


class GeminiService:
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name="models/gemini-3-flash-preview", system_instruction=SYSTEM_INSTRUCTION
        )

        # Safety settings (block hate speech, etc.)
        self.safety_settings = [
            {
                "category": types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                "threshold": types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            },
            {
                "category": types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                "threshold": types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            },
        ]

    @property
    def client(self):
        """Get client with lazy initialization."""
        if self._client is None:
            self._client = _get_client()
        return self._client

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

            # Extract text from response
            response_text = response.text if hasattr(response, "text") else str(response)
            return response_text, usage_info

        except Exception as e:
            print(f"Gemini Error: {e}")
            raise HTTPException(status_code=500, detail="AI Service unavailable")

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

            config = types.GenerateContentConfig(
                system_instruction=self.system_instruction,
                safety_settings=self.safety_settings,
            )

            import asyncio

            loop = asyncio.get_event_loop()

            def _generate_content():
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=summary_prompt,
                    config=config,
                )

            response = await loop.run_in_executor(None, _generate_content)

            # Clean up any remaining conversational text that might have been added
            summary_text = (response.text if hasattr(response, "text") else str(response)).strip()

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
Do NOT classify as list query if user wants to:
- CREATE, ADD, GENERATE, or MODIFY something
- FIND NEW, RECOMMEND, SUGGEST, or SEARCH for something
- Get RECOMMENDATIONS or SUGGESTIONS

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

            config = types.GenerateContentConfig(
                system_instruction=self.system_instruction,
                safety_settings=self.safety_settings,
            )

            import asyncio

            loop = asyncio.get_event_loop()

            def _generate_content():
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=rewrite_prompt,
                    config=config,
                )

            response = await loop.run_in_executor(None, _generate_content)

            rewritten_text = (response.text if hasattr(response, "text") else str(response)).strip()

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

            config = types.GenerateContentConfig(
                system_instruction=self.system_instruction,
                safety_settings=self.safety_settings,
            )

            import asyncio

            loop = asyncio.get_event_loop()

            def _generate_content():
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=tag_prompt,
                    config=config,
                )

            response = await loop.run_in_executor(None, _generate_content)

            tags_text = (response.text if hasattr(response, "text") else str(response)).strip()

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
