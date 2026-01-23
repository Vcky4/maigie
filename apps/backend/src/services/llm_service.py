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

RULES:
1. Only generate the JSON if the user explicitly asks to *create*, *generate*, *retake*, *rewrite*, *summarize*, *add tags*, *recommend resources*, *set goal*, or *schedule* for something.
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
   - Use when user asks for "resources", "recommendations", "suggestions", "links", "videos", "articles", "books", etc.
   - Extract the query from what the user is asking for
   - Use topicId from context if user is viewing a topic
   - Use courseId from context if user is viewing a course
   - Set limit to 5-10 resources (default 10)
   - The system will generate personalized recommendations using RAG
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
"""


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
                        # Include topic content for context (truncated if too long)
                        topic_content = context["topicContent"]
                        if len(topic_content) > 500:
                            topic_content = topic_content[:500] + "..."
                        context_parts.append(f"Topic Content: {topic_content}")
                elif context.get("topicId"):
                    context_parts.append(f"Current Topic ID: {context['topicId']}")

                # Note information
                if context.get("noteTitle"):
                    context_parts.append(f"Current Note: {context['noteTitle']}")
                    if context.get("noteContent"):
                        # Include note content for context (truncated if too long)
                        note_content = context["noteContent"]
                        if len(note_content) > 500:
                            note_content = note_content[:500] + "..."
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
