"""
Intelligence domain — API routes.

The cognitive layer: conversations, messages, memory, voice,
model preferences, and recommendations.

Mounted at: /api/v1/intelligence
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.shared.auth import CurrentUser, PremiumUser

from . import models
from .conversation import conversation_service
from .memory import memory_service
from .planning import planning_service
from .reasoning import reasoning_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["intelligence"])


# ===========================================================================
# Conversations
# ===========================================================================


@router.post("/conversations", response_model=models.ConversationResponse, status_code=201)
async def create_conversation(body: models.ConversationCreate, current_user: CurrentUser):
    """Start a new conversation with Intelligence."""
    session = await conversation_service.create_conversation(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return session


@router.get("/conversations", response_model=models.ConversationListResponse)
async def list_conversations(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sessionType: str | None = Query(None),
    circleId: str | None = Query(None),
):
    """List conversations."""
    sessions, total = await conversation_service.list_conversations(
        user_id=current_user.id,
        page=page,
        page_size=pageSize,
        session_type=sessionType,
        circle_id=circleId,
    )
    return models.ConversationListResponse(
        conversations=sessions, total=total, page=page, pageSize=pageSize
    )


@router.get("/conversations/{session_id}", response_model=models.ConversationResponse)
async def get_conversation(session_id: str, current_user: CurrentUser):
    """Get a conversation."""
    return await conversation_service.get_conversation(
        session_id=session_id, user_id=current_user.id
    )


@router.get("/conversations/{session_id}/messages", response_model=models.MessageListResponse)
async def get_messages(
    session_id: str,
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None),
):
    """Get messages in a conversation."""
    messages, total = await conversation_service.get_messages(
        session_id=session_id, user_id=current_user.id, limit=limit, before=before
    )
    return models.MessageListResponse(messages=messages, total=total)


@router.delete("/conversations/{session_id}", status_code=204)
async def delete_conversation(session_id: str, current_user: CurrentUser):
    """Delete a conversation and its messages."""
    await conversation_service.delete_conversation(
        session_id=session_id, user_id=current_user.id
    )


@router.post("/conversations/{session_id}/archive", status_code=204)
async def archive_conversation(session_id: str, current_user: CurrentUser):
    """Archive a conversation (soft delete)."""
    await conversation_service.archive_conversation(
        session_id=session_id, user_id=current_user.id
    )


# ===========================================================================
# Chat (non-streaming, HTTP)
# ===========================================================================


@router.post("/chat", response_model=models.ChatResponse)
async def chat(body: models.ChatRequest, current_user: CurrentUser):
    """Send a message and receive AI response (non-streaming).

    For real-time streaming, use the WebSocket endpoint.
    """
    # Get or create session
    session_id = body.sessionId
    if not session_id:
        session = await conversation_service.create_conversation(
            user_id=current_user.id,
            data={"courseId": body.courseId, "topicId": body.topicId},
        )
        session_id = session.id

    result = await reasoning_service.generate_response(
        user_id=current_user.id,
        session_id=session_id,
        message=body.message,
        image_urls=body.imageUrls or None,
    )

    return models.ChatResponse(
        sessionId=session_id,
        message=result.get("message", {}),
        actions=result.get("actions", []),
    )


# ===========================================================================
# Memory
# ===========================================================================


@router.get("/memory/facts", response_model=list[models.UserFactResponse])
async def get_user_facts(current_user: CurrentUser):
    """Get learned facts about the user."""
    return await memory_service.get_user_facts(current_user.id)


@router.get("/memory/summaries", response_model=list[models.ConversationSummaryResponse])
async def get_summaries(current_user: CurrentUser, limit: int = Query(10, ge=1, le=50)):
    """Get recent conversation summaries."""
    return await memory_service.get_conversation_summaries(current_user.id, limit=limit)


@router.get("/memory/context", response_model=models.MemoryContextResponse)
async def get_memory_context(current_user: CurrentUser):
    """Get the full memory context (for debugging/transparency)."""
    context = await memory_service.get_memory_context(current_user.id)
    return context


# ===========================================================================
# Recommendations
# ===========================================================================


@router.get("/recommendations", response_model=list[models.RecommendationResponse])
async def get_recommendations(
    current_user: CurrentUser, limit: int = Query(5, ge=1, le=20)
):
    """Get proactive learning recommendations."""
    recs = await planning_service.get_recommendations(user_id=current_user.id, limit=limit)
    return recs


# ===========================================================================
# Model Preferences
# ===========================================================================


@router.get("/models/preferences", response_model=list[models.ModelPreferenceResponse])
async def get_model_preferences(current_user: CurrentUser):
    """Get user's AI model preferences."""
    from sqlalchemy import select
    from src.domains.identity.db_models import ModelPreference
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(ModelPreference).where(ModelPreference.user_id == current_user.id)
        result = await session.execute(stmt)
        prefs = list(result.scalars().all())

    return [
        models.ModelPreferenceResponse(
            capability=p.capability, provider=p.provider, modelId=p.model_id
        )
        for p in prefs
    ]


@router.put("/models/preferences", response_model=models.ModelPreferenceResponse)
async def update_model_preference(body: models.ModelPreferenceUpdate, current_user: CurrentUser):
    """Update preferred AI model for a capability."""
    from sqlalchemy import select, update as sa_update
    from src.domains.identity.db_models import ModelPreference
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(ModelPreference).where(
            ModelPreference.user_id == current_user.id,
            ModelPreference.capability == body.capability,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            upd = (
                sa_update(ModelPreference)
                .where(ModelPreference.id == existing.id)
                .values(provider=body.provider, model_id=body.modelId)
            )
            await session.execute(upd)
        else:
            new_pref = ModelPreference(
                user_id=current_user.id,
                capability=body.capability,
                provider=body.provider,
                model_id=body.modelId,
            )
            session.add(new_pref)
        await session.commit()

    # Fetch the final state
    async with factory() as session:
        stmt = select(ModelPreference).where(
            ModelPreference.user_id == current_user.id,
            ModelPreference.capability == body.capability,
        )
        result = await session.execute(stmt)
        pref = result.scalar_one_or_none()

    return models.ModelPreferenceResponse(
        capability=pref.capability, provider=pref.provider, modelId=pref.model_id
    )


# ===========================================================================
# WebSocket (Streaming Chat)
# ===========================================================================


def register_websocket(app):
    """Register the WebSocket chat endpoint on the FastAPI app.

    Called from src/app.py after the intelligence router is included.
    The WebSocket needs direct app access (not a sub-router) because
    WebSocket routes have different lifecycle requirements.

    Usage in app.py:
        from src.domains.intelligence.routes import register_websocket
        register_websocket(app)
    """
    from src.shared.database import db

    from .conversation.websocket_handler import register_chat_websocket_routes

    # Create a dedicated router for the WebSocket
    ws_router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])
    register_chat_websocket_routes(ws_router, db)
    app.include_router(ws_router)
