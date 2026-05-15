"""
Voice Service.
Handles Audio Transcription using Google Gemini.
"""

from fastapi import HTTPException, UploadFile
from google import genai
from google.genai import types

from src.services.llm_registry import LlmTask, default_model_for, gemini_api_key


# Get the client lazily
def get_client():
    return genai.Client(api_key=gemini_api_key() or None)


class VoiceService:
    def __init__(self):
        # We use Flash because it's fast and supports audio
        self.model_name = default_model_for(LlmTask.VOICE_TRANSCRIPTION)

    async def transcribe_audio(self, file: UploadFile) -> str:
        """
        Uploads audio to Gemini and asks for a transcript.
        """
        try:
            # 1. Read the file content
            content = await file.read()

            # 2. Prepare the audio blob for Gemini (Inline Data)
            # Gemini expects the raw bytes and a mime_type
            mime_type = file.content_type or "audio/webm"  # Default for web browsers

            audio_part = types.Part.from_bytes(data=content, mime_type=mime_type)

            # 3. Prompt Gemini to transcribe with better instructions
            client = get_client()
            response = await client.aio.models.generate_content(
                model=self.model_name,
                contents=[
                    "Transcribe ONLY the actual spoken words in this audio. "
                    "If there is silence, background noise, or no clear speech, respond with an empty string. "
                    "Do not add any commentary, markdown, or descriptions. "
                    "Do not transcribe silence as words. "
                    "Only output the exact words that were spoken, nothing else.",
                    audio_part,
                ],
            )

            return response.text.strip()

        except Exception as e:
            print(f"Gemini Voice Error: {e}")
            raise HTTPException(status_code=500, detail="Failed to process voice audio")


voice_service = VoiceService()
