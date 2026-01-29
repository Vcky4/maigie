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
        self.model = genai.GenerativeModel("models/gemini-3-flash-preview")

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
            response = await self.model.generate_content_async(
                [
                    "Transcribe ONLY the actual spoken words in this audio. "
                    "If there is silence, background noise, or no clear speech, respond with an empty string. "
                    "Do not add any commentary, markdown, or descriptions. "
                    "Do not transcribe silence as words. "
                    "Only output the exact words that were spoken, nothing else.",
                    audio_part,
                ]
            )

            return response.text.strip()

        except Exception as e:
            print(f"Gemini Voice Error: {e}")
            raise HTTPException(status_code=500, detail="Failed to process voice audio")


voice_service = VoiceService()
