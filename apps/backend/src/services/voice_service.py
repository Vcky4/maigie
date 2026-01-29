"""
Voice Service.
Handles Audio Transcription using Google Gemini.
"""

import os

from fastapi import HTTPException, UploadFile
from google import genai

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


class VoiceService:
    def __init__(self):
        self._client = None  # Lazy initialization
        self.model_name = "gemini-3-flash-preview"

    @property
    def client(self):
        """Get client with lazy initialization."""
        if self._client is None:
            self._client = _get_client()
        return self._client

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

            audio_part = {"mime_type": mime_type, "data": content}

            # 3. Prompt Gemini to transcribe with better instructions
            from google.genai import types

            # Create audio part using inline_data
            audio_part = types.Part(inline_data=types.Blob(data=content, mime_type=mime_type))

            prompt = [
                "Transcribe ONLY the actual spoken words in this audio. "
                "If there is silence, background noise, or no clear speech, respond with an empty string. "
                "Do not add any commentary, markdown, or descriptions. "
                "Do not transcribe silence as words. "
                "Only output the exact words that were spoken, nothing else.",
                audio_part,
            ]

            import asyncio

            loop = asyncio.get_event_loop()

            def _generate_content():
                return self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )

            response = await loop.run_in_executor(None, _generate_content)
            response_text = response.text if hasattr(response, "text") else str(response)
            return response_text.strip()

        except Exception as e:
            print(f"Gemini Voice Error: {e}")
            raise HTTPException(status_code=500, detail="Failed to process voice audio")


voice_service = VoiceService()
