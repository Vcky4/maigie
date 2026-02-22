"""
AI assistant routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..utils.metrics import AI_USAGE_COUNTER

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str
    conversation_id: str | None = None


class DemoChatRequest(BaseModel):
    """Request model for unauthenticated demo chat."""

    message: str
    history: list = []


class SummarizeRequest(BaseModel):
    """Request model for summarize endpoint."""

    content: str
    content_type: str = "text"


class ProcessRequest(BaseModel):
    """Request model for AI process endpoint."""

    message: str
    conversation_id: str | None = None
    context: dict | None = None


class PlanRequest(BaseModel):
    """Request model for create plan endpoint."""

    goal: str
    duration_weeks: int = 4


@router.post("/chat/demo")
async def demo_chat(request: DemoChatRequest):
    """
    Unauthenticated chat endpoint for the landing page demo.
    """
    AI_USAGE_COUNTER.inc()

    from ..services.llm_service import llm_service

    context = {
        "pageContext": "Landing Page Public Demo",
        "instructions": (
            "You are Maigie, an AI study companion. You are in a public demo mode on the landing page. "
            "Keep responses extremely brief (1-3 sentences maximum) and focused on showcasing your capabilities "
            "like explaining concepts, scheduling, or taking notes. Provide plain text responses."
        ),
    }

    try:
        response_text, _ = await llm_service.get_chat_response(
            history=request.history, user_message=request.message, context=context
        )
        return {"response": response_text}
    except Exception as e:
        return {
            "response": "I'm having a little trouble connecting right now, but I'm usually much faster! Try again in a moment."
        }


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Chat with AI assistant.

    Standard chat functionality available to all users.
    """
    # TODO: Implement AI chat
    return {
        "message": "Chat endpoint - implementation pending",
        "user_message": request.message,
    }


@router.post("/summary")
async def summarize(request: SummarizeRequest):
    """
    Summarize content using AI.

    Available to all users with basic rate limits.
    """
    # TODO: Implement summarization
    return {
        "message": "Summarization endpoint - implementation pending",
        "content_length": len(request.content),
    }


@router.post("/process")
async def process(request: ProcessRequest):
    """
    Process AI conversation and content generation.

    This is the core AI processing endpoint that handles conversation
    and content generation. Tracks AI usage for subscription quota enforcement.

    Args:
        request: ProcessRequest containing message and optional context

    Returns:
        Response with AI-generated content or action recommendations
    """
    # Increment AI usage counter for quota enforcement
    AI_USAGE_COUNTER.inc()

    # TODO: Implement full AI processing logic
    # This would include:
    # - Intent classification
    # - Context enrichment
    # - LLM call
    # - Action dispatching
    # - Response formatting

    return {
        "message": "AI processing endpoint - implementation pending",
        "user_message": request.message,
        "conversation_id": request.conversation_id,
        "status": "processing",
    }


@router.post("/create-plan")
async def create_plan(request: PlanRequest):
    """Create study plan."""
    # TODO: Implement study plan creation
    return {
        "message": "Study plan creation endpoint - implementation pending",
        "goal": request.goal,
        "duration_weeks": request.duration_weeks,
    }
