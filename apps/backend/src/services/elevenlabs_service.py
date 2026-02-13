"""
ElevenLabs Text-to-Speech service for Smart AI Tutor and Exam Prep voice mode.
"""

from __future__ import annotations

import httpx
from src.config import get_settings


class ElevenLabsService:
    """Service for ElevenLabs text-to-speech streaming."""

    BASE_URL = "https://api.elevenlabs.io/v1"

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: str | None = None,
        model_id: str = "eleven_multilingual_v2",
        optimize_streaming_latency: int = 2,
    ):
        """
        Convert text to speech and yield audio chunks.

        Args:
            text: Text to convert to speech
            voice_id: ElevenLabs voice ID (default from config)
            model_id: Model to use for generation
            optimize_streaming_latency: 0-4, higher = faster but may reduce quality

        Yields:
            bytes: Audio chunks (mp3)
        """
        settings = get_settings()
        api_key = settings.ELEVENLABS_API_KEY
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is not configured")

        voice_id = voice_id or settings.ELEVENLABS_VOICE_ID
        url = f"{self.BASE_URL}/text-to-speech/{voice_id}/stream"

        params = {
            "output_format": "mp3_44100_128",
            "optimize_streaming_latency": optimize_streaming_latency,
        }

        payload = {
            "text": text,
            "model_id": model_id,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                params=params,
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ValueError(
                        f"ElevenLabs API error {response.status_code}: {body.decode()[:200]}"
                    )
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk


elevenlabs_service = ElevenLabsService()
