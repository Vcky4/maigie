"""
AI assistant routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..dependencies import CurrentUser
from ..utils.metrics import AI_USAGE_COUNTER

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    message: str
    conversation_id: str | None = None
    context: dict | None = None


class DemoChatRequest(BaseModel):
    """Request model for unauthenticated demo chat."""

    message: str
    history: list = []


class SummarizeRequest(BaseModel):
    """Request model for summarize endpoint."""

    content: str
    content_type: str = "text"
    max_length: int = 200


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
async def chat(request: ChatRequest, current_user: CurrentUser):
    """
    Authenticated REST chat endpoint with full tool-calling support.

    This is the REST equivalent of the WebSocket chat — supports tool execution,
    database mutations, and returns structured responses.
    """
    AI_USAGE_COUNTER.inc()

    from ..core.database import db
    from ..services.llm_service import llm_service
    from ..services.memory_service import get_memory_context

    try:
        # Build context with memory
        context = request.context or {}
        try:
            memory_ctx = await get_memory_context(current_user.id, query=request.message)
            if memory_ctx:
                context["memory_context"] = memory_ctx
        except Exception:
            pass

        # Fetch recent chat history
        history = []
        if request.conversation_id:
            messages = await db.chatmessage.find_many(
                where={"sessionId": request.conversation_id},
                order={"createdAt": "asc"},
                take=20,
            )
            for msg in messages:
                role = "user" if msg.role == "USER" else "model"
                history.append({"role": role, "parts": [msg.content or ""]})

        # Get AI response with tools
        response_text, usage_info, executed_actions, query_results = (
            await llm_service.get_chat_response_with_tools(
                history=history,
                user_message=request.message,
                context=context,
                user_id=current_user.id,
                user_name=current_user.name,
            )
        )

        return {
            "response": response_text,
            "actions": executed_actions,
            "query_results": query_results,
            "usage": usage_info,
            "conversation_id": request.conversation_id,
        }

    except Exception as e:
        logger.error("Chat endpoint error: %s", e, exc_info=True)
        return {
            "response": "I encountered an error processing your message. Please try again.",
            "error": str(e),
        }


@router.post("/summary")
async def summarize(request: SummarizeRequest, current_user: CurrentUser):
    """
    Summarize content using AI.

    Takes arbitrary text content and returns a concise summary.
    """
    AI_USAGE_COUNTER.inc()

    from ..services.llm_service import llm_service

    try:
        max_length = max(50, min(500, request.max_length))

        prompt = (
            f"Summarize the following {request.content_type} content in {max_length} words or fewer. "
            f"Be concise and capture the key points:\n\n{request.content}"
        )

        response_text, usage_info = await llm_service.get_chat_response(
            history=[],
            user_message=prompt,
            context={"pageContext": "Content Summarization"},
        )

        return {
            "summary": response_text,
            "content_type": request.content_type,
            "original_length": len(request.content),
            "summary_length": len(response_text),
            "usage": usage_info,
        }

    except Exception as e:
        logger.error("Summarize endpoint error: %s", e, exc_info=True)
        return {
            "summary": "Failed to generate summary. Please try again.",
            "error": str(e),
        }


@router.post("/process")
async def process(request: ProcessRequest, current_user: CurrentUser):
    """
    Core AI processing endpoint — intent → action pipeline.

    Processes a user message through the full agentic pipeline:
    intent classification, context enrichment, tool execution, and response.
    Tracks AI usage for subscription quota enforcement.
    """
    AI_USAGE_COUNTER.inc()

    from ..services.llm_service import llm_service
    from ..services.memory_service import get_memory_context

    try:
        # Enrich context with memory
        context = request.context or {}
        try:
            memory_ctx = await get_memory_context(current_user.id, query=request.message)
            if memory_ctx:
                context["memory_context"] = memory_ctx
        except Exception:
            pass

        # Run through the full tool-calling pipeline
        response_text, usage_info, executed_actions, query_results = (
            await llm_service.get_chat_response_with_tools(
                history=[],
                user_message=request.message,
                context=context,
                user_id=current_user.id,
                user_name=current_user.name,
            )
        )

        # Evaluate actions using reflection service
        evaluations = []
        try:
            from ..services.reflection_service import evaluate_action_outcome

            for action in executed_actions:
                ev = await evaluate_action_outcome(
                    action_type=action.get("tool_name", "unknown"),
                    action_data=action.get("args", {}),
                    action_result=action.get("result", {}),
                    user_message=request.message,
                )
                evaluations.append(ev)
        except Exception:
            pass

        return {
            "response": response_text,
            "actions": executed_actions,
            "query_results": query_results,
            "evaluations": evaluations,
            "usage": usage_info,
            "conversation_id": request.conversation_id,
            "status": "completed",
        }

    except Exception as e:
        logger.error("Process endpoint error: %s", e, exc_info=True)
        return {
            "response": "Failed to process your request. Please try again.",
            "error": str(e),
            "status": "error",
        }


@router.post("/create-plan")
async def create_plan(request: PlanRequest, current_user: CurrentUser):
    """
    Create a multi-step study plan.

    Decomposes a study goal into courses, milestones, goals,
    and scheduled study sessions over the specified duration.
    """
    AI_USAGE_COUNTER.inc()

    from ..services.planning_service import create_study_plan

    try:
        duration_weeks = max(1, min(16, request.duration_weeks))

        result = await create_study_plan(
            user_id=current_user.id,
            goal=request.goal,
            duration_weeks=duration_weeks,
        )

        return result

    except Exception as e:
        logger.error("Create plan endpoint error: %s", e, exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to create study plan: {str(e)}",
            "goal": request.goal,
        }
