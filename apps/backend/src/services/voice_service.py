"""
Voice Service.
Handles Audio Transcription using Google Gemini.
"""

import os

import google.generativeai as genai
from fastapi import HTTPException, UploadFile

# Reuse the API key from your environment
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


class VoiceService:
    def __init__(self):
        # We use Flash because it's fast and supports audio
        self.model = genai.GenerativeModel("models/gemini-flash-latest")

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

            # 3. Prompt Gemini to transcribe
            response = await self.model.generate_content_async(
                [
                    "Please transcribe this audio file exactly as spoken. Do not add any commentary or markdown.",
                    audio_part,
                ]
            )

            return response.text.strip()

        except Exception as e:
            print(f"Gemini Voice Error: {e}")
            raise HTTPException(status_code=500, detail="Failed to process voice audio")


voice_service = VoiceService()
