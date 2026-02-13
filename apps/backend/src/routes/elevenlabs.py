"""
ElevenLabs Text-to-Speech API routes.
Proxies TTS requests to keep API key server-side. Used by Smart AI Tutor and Exam Prep voice mode.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.dependencies import CurrentUser, PremiumUser
from src.services.elevenlabs_service import elevenlabs_service
from src.services.llm_service import llm_service


router = APIRouter(tags=["elevenlabs"])


class TextToSpeechRequest(BaseModel):
    """Request body for text-to-speech."""

    text: str
    voice_id: str | None = None
    model_id: str = "eleven_multilingual_v2"
    optimize_streaming_latency: int = 2


@router.post("/text-to-speech/stream")
async def text_to_speech_stream(
    request: TextToSpeechRequest,
    current_user: PremiumUser,
):
    """
    Convert text to speech using ElevenLabs. Streams audio back.
    Requires Maigie Plus subscription.
    """
    if not request.text or len(request.text.strip()) == 0:
        raise HTTPException(status_code=400, detail="Text is required")

    if len(request.text) > 5000:
        raise HTTPException(
            status_code=400,
            detail="Text exceeds maximum length of 5000 characters",
        )

    try:
        return StreamingResponse(
            elevenlabs_service.text_to_speech_stream(
                text=request.text.strip(),
                voice_id=request.voice_id,
                model_id=request.model_id,
                optimize_streaming_latency=request.optimize_streaming_latency,
            ),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=speech.mp3",
            },
        )
    except ValueError as e:
        if "ELEVENLABS_API_KEY" in str(e):
            raise HTTPException(
                status_code=503,
                detail="Voice service is temporarily unavailable",
            )
        raise HTTPException(status_code=400, detail=str(e))


# --- Smart AI Tutor: single-turn Q&A for topic tutoring ---


class TutorAskRequest(BaseModel):
    """Request for tutor Q&A (topic context + user question)."""

    topic_title: str
    topic_content: str | None = None
    course_title: str | None = None
    user_message: str


class TutorAskResponse(BaseModel):
    """AI tutor response."""

    response: str


@router.post("/tutor/ask", response_model=TutorAskResponse)
async def tutor_ask(
    request: TutorAskRequest,
    current_user: PremiumUser,
):
    """
    Ask the AI tutor a question about a topic. Returns text response.
    Use with /text-to-speech/stream to speak the response (11labs).
    """
    if not request.user_message or len(request.user_message.strip()) == 0:
        raise HTTPException(status_code=400, detail="user_message is required")

    context = {
        "topicTitle": request.topic_title,
        "topicContent": request.topic_content or "",
        "courseTitle": request.course_title or "",
    }
    history = []
    response_text, _ = await llm_service.get_chat_response(
        history=history,
        user_message=request.user_message,
        context=context,
    )
    return TutorAskResponse(response=response_text or "")
