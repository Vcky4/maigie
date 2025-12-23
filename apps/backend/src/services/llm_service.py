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
Your goal is to help students organize learning, generate courses, and manage schedules.

CRITICAL INSTRUCTION FOR ACTIONS:
If the user asks to generate a course, study plan, or schedule, you must NOT just describe it.
You MUST output a strict JSON block at the very end of your response inside specific tags.

Format:
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

RULES:
1. Only generate the JSON if the user explicitly asks to *create* or *generate* something.
2. The JSON must be valid.
3. Keep the conversational part of your response encouraging and brief.
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

    async def get_chat_response(self, history: list, user_message: str) -> str:
        """
        Send message to Gemini and get response.
        History should be formatted as a list of contents.
        """
        try:
            # Start a chat session with history
            chat = self.model.start_chat(history=history)

            # Send the new message
            response = await chat.send_message_async(
                user_message, safety_settings=self.safety_settings
            )

            return response.text

        except Exception as e:
            print(f"Gemini Error: {e}")
            raise HTTPException(status_code=500, detail="AI Service unavailable")


# Global instance
llm_service = GeminiService()
