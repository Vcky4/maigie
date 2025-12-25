"""
LLM Service using Google Gemini.
Handles chat logic and tool execution.
"""

import os
import warnings

# Suppress the Google Gemini deprecation warning temporarily
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import google.generativeai as genai

from fastapi import HTTPException
from google.generativeai.types import HarmBlockThreshold, HarmCategory

# Configure API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# System instruction to define Maigie's persona
SYSTEM_INSTRUCTION = """
You are Maigie, an intelligent study companion.
Your goal is to help students organize learning, generate courses, manage schedules, create notes, and summarize content.

CRITICAL INSTRUCTION FOR ACTIONS:
If the user asks to generate a course, study plan, schedule, or create a note, you must NOT just describe it.
You MUST output a strict JSON block at the very end of your response inside specific tags.

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

RULES:
1. Only generate the JSON if the user explicitly asks to *create*, *generate*, *retake*, *rewrite*, or *summarize* something.
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
5. The JSON must be valid.
6. Keep the conversational part of your response encouraging and brief.
7. When creating notes, use the topic/course information from context to make the note relevant and contextual.
"""


class GeminiService:
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name="models/gemini-flash-latest", system_instruction=SYSTEM_INSTRUCTION
        )

        # Safety settings (block hate speech, etc.)
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        }

    async def get_chat_response(
        self, history: list, user_message: str, context: dict = None
    ) -> str:
        """
        Send message to Gemini and get response.
        History should be formatted as a list of contents.
        Context can include: topicId, courseId, pageContext, etc.
        """
        try:
            # Build enhanced message with context if provided
            enhanced_message = user_message

            if context:
                context_parts = []
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

                if context_parts:
                    context_str = "\n".join(context_parts)
                    enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

            # Start a chat session with history
            chat = self.model.start_chat(history=history)

            # Send the enhanced message
            response = await chat.send_message_async(
                enhanced_message, safety_settings=self.safety_settings
            )

            return response.text

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
Make it more comprehensive, well-structured, and educational with proper markdown formatting.
Use headings, lists, code blocks, and other markdown elements appropriately.

CRITICAL: Return ONLY the improved note content in markdown format. Do not include any:
- Introductory phrases like "That is an excellent topic" or "I have rewritten"
- Conversational text or commentary
- Explanations about what you did
- Concluding remarks

Just provide the rewritten content directly, starting with the first heading or paragraph.

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


# Global instance
llm_service = GeminiService()
