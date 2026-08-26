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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["intelligence"])


# ===========================================================================
# Conversations
# ===========================================================================


@router.post("/conversations", response_model=models.ConversationResponse, status_code=201)
async def create_conversation(body: models.ConversationCreate, current_user: CurrentUser):
    """Start a new conversation with Intelligence."""
    # `by_alias=True` because the request model is now a `CamelModel`, so its fields are snake_case,
    # while `conversation_service.create_conversation` reads camelCase keys on the way to the repo,
    # which names them after the columns. Dumping without the alias would silently drop every optional
    # context link — the conversation would be created unattached and the request would still 201.
    session = await conversation_service.create_conversation(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True, by_alias=True)
    )
    return session


@router.get("/conversations", response_model=models.PaginatedResponse[models.ConversationResponse])
async def list_conversations(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sessionType: str | None = Query(None),
    spaceId: str | None = Query(None),
):
    """List conversations, newest activity first."""
    sessions, total = await conversation_service.list_conversations(
        user_id=current_user.id,
        page=page,
        page_size=pageSize,
        session_type=sessionType,
        space_id=spaceId,
    )
    pages = (total + pageSize - 1) // pageSize if total else 0
    return models.PaginatedResponse[models.ConversationResponse](
        items=sessions,
        total=total,
        page=page,
        page_size=pageSize,
        pages=pages,
    )


@router.get("/conversations/{session_id}", response_model=models.ConversationResponse)
async def get_conversation(session_id: str, current_user: CurrentUser):
    """Get a conversation."""
    return await conversation_service.get_conversation(
        session_id=session_id, user_id=current_user.id
    )


@router.get(
    "/conversations/{session_id}/messages",
    response_model=models.CursorPage[models.ChatMessageResponse],
)
async def get_messages(
    session_id: str,
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(
        None, description="A message id from a previous window. Returns the window before it."
    ),
):
    """One window of a conversation, oldest-first.

    A thread pages backwards by cursor rather than by number, so this returns `CursorPage` rather than
    `PaginatedResponse`: `page` and `pages` would have to be invented to fit the other envelope, and an
    invented page number is indistinguishable from a real one.
    """
    messages, total, has_more, next_cursor = await conversation_service.get_messages(
        session_id=session_id, user_id=current_user.id, limit=limit, before=before
    )
    return models.CursorPage[models.ChatMessageResponse](
        items=messages, total=total, has_more=has_more, next_cursor=next_cursor
    )


@router.delete("/conversations/{session_id}", status_code=204)
async def delete_conversation(session_id: str, current_user: CurrentUser):
    """Delete a conversation and its messages."""
    await conversation_service.delete_conversation(session_id=session_id, user_id=current_user.id)


@router.post("/conversations/{session_id}/archive", status_code=204)
async def archive_conversation(session_id: str, current_user: CurrentUser):
    """Archive a conversation (soft delete)."""
    await conversation_service.archive_conversation(session_id=session_id, user_id=current_user.id)


# ===========================================================================
# Chat (non-streaming, HTTP)
# ===========================================================================
#
# `POST /chat` is gone. It called `reasoning_service.generate_response`, which imported
# `reasoning.chat_impl.process_chat_message` — a function that was never written. The only two
# references to that name in the repository were the import and the call, so the route was dead on
# arrival and mounting the router with it in place would have published a `500`.
#
# `POST /ask` replaces it, backed by the pipeline extracted out of the WebSocket handler. Until then
# streaming over `WS /ws` is the only way to send a turn, which is what both clients already do.
# Nothing is published broken in the meantime.


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
    """What Intelligence remembers about the learner, as data (for transparency and debugging).

    Reads `get_memory_snapshot`, not `get_memory_context`. The latter returns the same memory rendered
    as a prompt block — a plain string — and this route used to return it under a structured response
    model, which would have `500`d on the first request.
    """
    return await memory_service.get_memory_snapshot(current_user.id)


# ===========================================================================
# Recommendations
# ===========================================================================
#
# `GET /recommendations` is gone, for the same reason as `POST /chat`:
# `planning_service.get_recommendations` imported `get_learning_insights` from
# `planning/reflection_impl.py`, and that module defines only `evaluate_action_outcome` and
# `build_reflection_context`. The name exists elsewhere as `action/skills/handlers`'
# `handle_get_learning_insights`, which is a tool handler with a different signature and return shape,
# so the import could not have been satisfied by it. The route was a `500`.
#
# Not resurrected here: proactive recommendations are outside Ask Maigie's scope (§4.1), and there is
# no recommendation engine to point a route at — the deleted service body was a comment reading
# "Future: build recommendation engine" over a call to a function that did not exist. It returns when
# something computes recommendations.


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
    from sqlalchemy import select
    from sqlalchemy import update as sa_update

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
    from .conversation.websocket_handler import register_chat_websocket_routes

    # Create a dedicated router for the WebSocket
    ws_router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])
    register_chat_websocket_routes(ws_router, None)
    app.include_router(ws_router)
