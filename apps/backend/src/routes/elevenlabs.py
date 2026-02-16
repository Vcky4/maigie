"""
ElevenLabs Text-to-Speech and Conversational AI API routes.
Proxies requests to keep API key server-side.
Provides voice-agent context endpoint for injecting user data into the agent.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from prisma import Client as PrismaClient
from pydantic import BaseModel

from src.config import get_settings
from src.dependencies import CurrentUser, PremiumUser
from src.services.elevenlabs_service import elevenlabs_service
from src.services.llm_service import llm_service
from src.utils.dependencies import get_db_client

logger = logging.getLogger(__name__)

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


# --- Conversational AI: signed WebSocket URL for real-time voice agent ---


@router.get("/convai/signed-url")
async def get_convai_signed_url(current_user: PremiumUser):
    """
    Get a signed WebSocket URL for ElevenLabs Conversational AI.

    The signed URL allows the client to open a WebSocket directly to ElevenLabs
    without exposing the API key. Requires Maigie Plus subscription.
    """
    settings = get_settings()
    if not settings.ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Voice agent is not configured (missing API key)",
        )
    if not settings.ELEVENLABS_AGENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Voice agent is not configured (missing agent ID)",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": settings.ELEVENLABS_AGENT_ID},
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Failed to get signed URL from ElevenLabs",
            )
        data = resp.json()
        return {"signed_url": data["signed_url"]}
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs API error: {str(e)}",
        )


# --- Conversational AI: voice agent context for prompt injection ---


VOICE_SYSTEM_PROMPT = """You are Maigie, an intelligent and friendly AI study companion.
You are currently in VOICE MODE — speaking with the user in real-time.

Keep responses concise and conversational (1-3 sentences for simple answers).
Use natural speech patterns — avoid markdown, bullet points, or code blocks.
When listing items, say them naturally (e.g. "You have three courses: X, Y, and Z").

You have access to tools to look up the user's current view, courses, schedule, goals, and topic content.
Use getCurrentView when the user refers to "this", "what I'm looking at", or anything about the current screen.
Use getUserData to fetch fresh data when asked about courses, schedule, or goals.
Use getTopicContent when tutoring or quizzing on a specific topic.

IMPORTANT DATE CONTEXT:
- The user's current date and time is provided below.
- When discussing schedules, goals, or deadlines, always use dates relative to the current date.
"""


async def _gather_voice_context(
    db: PrismaClient, user_id: str, user_name: str, section: str | None = None
) -> dict[str, Any]:
    """
    Gather user context for the voice agent.
    If section is provided, only fetch that section; otherwise fetch all.
    Returns both structured data and a prompt-ready text summary.
    """
    now = datetime.now(UTC)
    structured: dict[str, Any] = {}
    prompt_parts: list[str] = [
        f"Current Date & Time: {now.strftime('%A, %B %d, %Y at %H:%M UTC')}",
    ]
    if user_name:
        prompt_parts.append(f"User's name: {user_name}")

    fetch_all = section is None or section == "all"

    # -- Courses --
    if fetch_all or section == "courses":
        try:
            courses = await db.course.find_many(
                where={"userId": user_id, "archived": False},
                order={"updatedAt": "desc"},
                take=8,
                include={"modules": {"include": {"topics": True}}},
            )
            course_summaries = []
            for c in courses:
                total = sum(len(m.topics) for m in c.modules)
                completed = sum(1 for m in c.modules for t in m.topics if t.completed)
                progress = round((completed / total * 100) if total > 0 else 0)
                course_summaries.append(
                    {
                        "id": c.id,
                        "title": c.title,
                        "progress": progress,
                        "totalTopics": total,
                        "completedTopics": completed,
                    }
                )
            structured["courses"] = course_summaries
            if course_summaries:
                lines = [
                    f"  - {c['title']} ({c['progress']}% complete, {c['completedTopics']}/{c['totalTopics']} topics)"
                    for c in course_summaries
                ]
                prompt_parts.append("Courses:\n" + "\n".join(lines))
            else:
                prompt_parts.append("Courses: None yet.")
        except Exception:
            logger.exception("Failed to fetch courses for voice context")
            structured["courses"] = []

    # -- Goals --
    if fetch_all or section == "goals":
        try:
            goals = await db.goal.find_many(
                where={"userId": user_id, "status": "ACTIVE"},
                take=5,
            )
            goal_summaries = [
                {
                    "id": g.id,
                    "title": g.title,
                    "progress": g.progress or 0,
                    "targetDate": g.targetDate.isoformat() if g.targetDate else None,
                }
                for g in goals
            ]
            structured["goals"] = goal_summaries
            if goal_summaries:
                lines = []
                for g in goal_summaries:
                    line = f"  - {g['title']} ({g['progress']}% done"
                    if g["targetDate"]:
                        line += f", target: {g['targetDate'][:10]}"
                    line += ")"
                    lines.append(line)
                prompt_parts.append("Active Goals:\n" + "\n".join(lines))
            else:
                prompt_parts.append("Active Goals: None.")
        except Exception:
            logger.exception("Failed to fetch goals for voice context")
            structured["goals"] = []

    # -- Schedule (next 3 days) --
    if fetch_all or section == "schedule":
        try:
            schedules = await db.scheduleblock.find_many(
                where={
                    "userId": user_id,
                    "startAt": {"gte": now, "lte": now + timedelta(days=3)},
                },
                order={"startAt": "asc"},
                take=10,
            )
            sched_summaries = [
                {
                    "id": s.id,
                    "title": s.title,
                    "startAt": s.startAt.isoformat(),
                    "endAt": s.endAt.isoformat() if s.endAt else None,
                }
                for s in schedules
            ]
            structured["schedule"] = sched_summaries
            if sched_summaries:
                lines = [
                    f"  - {s['title']} at {s['startAt'][:16].replace('T', ' ')}"
                    for s in sched_summaries
                ]
                prompt_parts.append("Upcoming Schedule (next 3 days):\n" + "\n".join(lines))
            else:
                prompt_parts.append("Upcoming Schedule: Nothing scheduled in the next 3 days.")
        except Exception:
            logger.exception("Failed to fetch schedule for voice context")
            structured["schedule"] = []

    # -- Pending Reviews --
    if fetch_all or section == "reviews":
        try:
            reviews = await db.reviewitem.find_many(
                where={"userId": user_id, "nextReviewAt": {"lte": now + timedelta(days=1)}},
                include={"topic": True},
                take=10,
            )
            review_summaries = [
                {
                    "id": r.id,
                    "topicTitle": r.topic.title if r.topic else "Unknown",
                    "nextReviewAt": r.nextReviewAt.isoformat(),
                    "intervalDays": r.intervalDays,
                    "repetitionCount": r.repetitionCount,
                }
                for r in reviews
            ]
            structured["reviews"] = review_summaries
            if review_summaries:
                lines = [
                    f"  - {r['topicTitle']} (due: {r['nextReviewAt'][:10]})"
                    for r in review_summaries
                ]
                prompt_parts.append(
                    f"Pending Reviews ({len(review_summaries)}):\n" + "\n".join(lines)
                )
            else:
                prompt_parts.append("Pending Reviews: All caught up!")
        except Exception:
            logger.exception("Failed to fetch reviews for voice context")
            structured["reviews"] = []

    # -- Study Streak --
    if fetch_all:
        try:
            streak = await db.userstreak.find_unique(where={"userId": user_id})
            streak_val = streak.currentStreak if streak else 0
            structured["streak"] = streak_val
            if streak_val > 0:
                prompt_parts.append(
                    f"Study Streak: {streak_val} day{'s' if streak_val != 1 else ''}."
                )
        except Exception:
            structured["streak"] = 0

    prompt_context = "\n".join(prompt_parts)
    return {
        "promptContext": prompt_context,
        "structured": structured,
    }


class VoiceContextResponse(BaseModel):
    """Voice agent context: prompt-ready text and structured data."""

    promptContext: str
    systemPrompt: str
    structured: dict[str, Any]


@router.get("/convai/context", response_model=VoiceContextResponse)
async def get_voice_agent_context(
    current_user: PremiumUser,
    db: Annotated[PrismaClient, Depends(get_db_client)],
    section: str | None = Query(
        None,
        description="Fetch only a specific section: courses, goals, schedule, reviews, or all",
    ),
):
    """
    Get user context for the ElevenLabs voice agent.

    Returns a prompt-ready text summary and structured JSON.
    Used to inject personalized context into the agent's system prompt override
    and for mid-conversation data refreshes via client tools.
    """
    user_name = getattr(current_user, "name", "") or ""
    ctx = await _gather_voice_context(db, current_user.id, user_name, section)

    system_prompt = VOICE_SYSTEM_PROMPT + "\n" + ctx["promptContext"]

    return VoiceContextResponse(
        promptContext=ctx["promptContext"],
        systemPrompt=system_prompt,
        structured=ctx["structured"],
    )
