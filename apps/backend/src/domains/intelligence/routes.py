"""
Intelligence domain — API routes.

The cognitive layer: conversations, messages, memory, voice,
model preferences, and recommendations.

Mounted at: /api/v1/intelligence
"""

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from src.shared.auth import CurrentUser

from . import models
from .conversation import ask_service, context_enrichment, conversation_service
from .memory import memory_service
from .reasoning.llm.errors import LLMProviderError
from .repository import (
    ActiveGenerationAttemptError,
    AttemptFenceLostError,
    intelligence_repo,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["intelligence"])


# ===========================================================================
# Conversations
# ===========================================================================


@router.post(
    "/conversations", response_model=models.ConversationResponse, status_code=201
)
async def create_conversation(
    body: models.ConversationCreate, current_user: CurrentUser
):
    """Start a new conversation with Intelligence."""
    # `by_alias=True` because the request model is now a `CamelModel`, so its fields are snake_case,
    # while `conversation_service.create_conversation` reads camelCase keys on the way to the repo,
    # which names them after the columns. Dumping without the alias would silently drop every optional
    # context link — the conversation would be created unattached and the request would still 201.
    session = await conversation_service.create_conversation(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True, by_alias=True)
    )
    return session


@router.get(
    "/conversations",
    response_model=models.PaginatedResponse[models.ConversationResponse],
)
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
        None,
        description="A message id from a previous window. Returns the window before it.",
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


@router.get(
    "/memory/summaries", response_model=list[models.ConversationSummaryResponse]
)
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
async def update_model_preference(
    body: models.ModelPreferenceUpdate, current_user: CurrentUser
):
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


# ===========================================================================
# Ask — one turn over HTTP
# ===========================================================================


@router.post("/ask", response_model=models.AskResponse)
async def ask(body: models.AskRequest, current_user: CurrentUser):
    """Ask Maigie one question and get the whole answer.

    **The same pipeline the socket runs, with `on_chunk=None`** (Decision C). Nothing about the turn is
    decided here: this route resolves a session, saves the learner's message, calls
    `ask_service.answer()` and maps its refusals onto status codes. If a rule ever appears in this
    function that is not in `answer()`, the two transports have started to drift — which is what §5.4
    documents a year of.

    Refusals, and why each is the status code it is:

    - `400` for an unusable message. Not `422`: the request is well-formed, the *message* is not, and the
      body says which so a client can show it rather than guess.
    - `429` when the learner is sending turns faster than the limit. Carries `Retry-After`.
    - `402` when credits are exhausted. Not `403` — nothing is forbidden, something is owed, and the body
      carries the same words the socket's `credit_limit_error` frame does.
    - `503` when generation failed. **No assistant row is written and no credits are consumed**, which is
      `answer()`'s guarantee rather than this route's: a failed turn is never stored as an answer (§1).
    """
    from src.shared.infrastructure.rate_limit import check_rate_limit

    # Screened before anything is written: a refused turn must not leave a message row behind, or the
    # thread holds a question the learner will never see an answer to.
    rejection = await ask_service.screen_turn(
        message=body.message, user_id=current_user.id, check_rate_limit=check_rate_limit
    )
    if rejection:
        if rejection.code == ask_service.MESSAGE_REJECTED_RATE_LIMITED:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=rejection.message,
                headers={"Retry-After": str(ask_service.RATE_LIMIT_WINDOW_SECONDS)},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=rejection.message
        )

    session = await _resolve_ask_session(
        session_id=body.session_id, user_id=current_user.id, repo=intelligence_repo
    )

    # One turn at a time per conversation (§4.5.13). Acquired before the learner's message is written,
    # and `409` because that is what a conflicting concurrent request is — not a rate limit, which the
    # learner fixes by waiting a minute, and not a bad request, which they fix by changing it.
    try:
        async with ask_service.turn_in_flight(session.id):
            return await _answer_over_http(
                body=body, user=current_user, session=session
            )
    except ask_service.TurnAlreadyInFlight as busy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=busy.rejection.message
        ) from busy


async def _answer_over_http(
    *, body: models.AskRequest, user, session
) -> models.AskResponse:
    """Persist the USER row and active attempt atomically, then run it."""
    message_data = {
        "sessionId": session.id,
        "userId": user.id,
        "role": "USER",
        "content": body.message,
        **({"imageUrls": body.image_urls} if body.image_urls else {}),
        **(
            {"reviewItemId": (body.context or {}).get("reviewItemId")}
            if (body.context or {}).get("reviewItemId")
            else {}
        ),
    }
    try:
        user_message, attempt = await intelligence_repo.create_message_and_attempt(
            message_data=message_data,
            attempt_data={
                "sessionId": session.id,
                "userId": user.id,
                "status": "RUNNING",
                "retryable": False,
                "context": body.context,
                "askMode": ask_service.ASK_MODE_HTTP,
            },
        )
    except ActiveGenerationAttemptError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another answer is already being generated for this conversation.",
        ) from error

    return await _run_http_attempt(
        user=user,
        session=session,
        user_message=user_message,
        attempt=attempt,
        context=body.context,
    )


async def _run_http_attempt(
    *, user, session, user_message, attempt, context
) -> models.AskResponse:
    """Run one already-persisted attempt, recording every terminal outcome."""
    try:
        async with ask_service.maintain_attempt_lease(
            attempt.id, intelligence_repo.heartbeat_attempt
        ):
            turn = await ask_service.answer(
                message=user_message.content,
                user=user,
                user_obj=user,
                session=session,
                user_message=user_message,
                context=context,
                ask_mode=ask_service.ASK_MODE_HTTP,
                readers=context_enrichment.production_readers(),
                effects=ask_service.production_effects(),
                cache=context_enrichment.production_cache(),
                image_url=(user_message.image_urls or [None])[0],
                on_chunk=None,
                attempt_id=attempt.id,
                update_attempt=intelligence_repo.update_running_attempt,
            )
    except ask_service.TurnRefused as refused:
        await intelligence_repo.update_running_attempt(
            attempt.id,
            {"status": "FAILED", "retryable": False, "failureCode": "CREDIT_REFUSED"},
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=refused.refusal.message,
        ) from refused
    except asyncio.CancelledError:
        try:
            await intelligence_repo.update_running_attempt(
                attempt.id,
                {"status": "CANCELLED", "retryable": True, "failureCode": "CANCELLED"},
            )
        except AttemptFenceLostError:
            pass
        raise
    except AttemptFenceLostError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This generation attempt was replaced by a newer worker.",
        ) from error
    except ask_service.TurnFailed as error:
        await intelligence_repo.update_running_attempt(
            attempt.id,
            {
                "status": "FAILED",
                "retryable": bool(error.retryable),
                "failureCode": "GENERATION_FAILED",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Maigie could not answer that just now. Please try again.",
        ) from error
    except LLMProviderError as error:
        await intelligence_repo.update_running_attempt(
            attempt.id,
            {
                "status": "FAILED",
                "retryable": bool(error.retriable),
                "failureCode": error.category,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Maigie could not answer that just now. Please try again.",
        ) from error
    except Exception as error:
        # Unknown failures before a committed assistant are conservatively retryable. The
        # repository's durable tool-intent marker forces this back to false when necessary.
        await intelligence_repo.update_running_attempt(
            attempt.id,
            {
                "status": "FAILED",
                "retryable": True,
                "failureCode": "GENERATION_FAILED",
            },
        )
        logger.error("Ask turn failed for user %s: %s", user.id, error, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Maigie could not answer that just now. Please try again.",
        ) from error

    return models.AskResponse(
        id=turn.assistant_message.id,
        attempt_id=attempt.id,
        session_id=session.id,
        content=turn.content,
        suggestion_text=turn.suggestion_text,
        components=turn.outcomes.components,
        skills_used=[models.AskSkillBadge(**badge) for badge in turn.skills_used],
        scope=models.AskScope(
            sources=turn.scope.sources, library_recall=turn.scope.library_recall
        ),
        actions=[
            models.AskAction(
                type=row["actionType"],
                status=row["status"],
                course_id=(row.get("actionData") or {}).get("courseId"),
            )
            for row in turn.outcomes.action_logs
        ],
    )


@router.post(
    "/conversations/{session_id}/messages/{message_id}/retry",
    response_model=models.AskResponse,
)
async def retry_message(session_id: str, message_id: str, current_user: CurrentUser):
    """Retry an explicitly retryable, unanswered attempt without duplicating its USER row."""
    session = await _resolve_ask_session(
        session_id=session_id, user_id=current_user.id, repo=intelligence_repo
    )
    prior = await intelligence_repo.find_attempt_for_retry(
        session_id=session_id, message_id=message_id, user_id=current_user.id
    )
    if prior is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Message has no retryable attempt",
        )
    if prior.status not in {"FAILED", "CANCELLED"} or not prior.retryable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This attempt cannot be retried",
        )
    if prior.tool_side_effects:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This attempt may have changed data and cannot be retried safely",
        )
    if await intelligence_repo.user_message_has_answer(message_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This message already has an answer",
        )
    user_message = await intelligence_repo.find_message(message_id)
    if user_message is None or user_message.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    from src.shared.infrastructure.rate_limit import check_rate_limit

    rejection = await ask_service.screen_turn(
        message=user_message.content,
        user_id=current_user.id,
        check_rate_limit=check_rate_limit,
    )
    if rejection:
        if rejection.code == ask_service.MESSAGE_REJECTED_RATE_LIMITED:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=rejection.message,
                headers={"Retry-After": str(ask_service.RATE_LIMIT_WINDOW_SECONDS)},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=rejection.message
        )

    try:
        async with ask_service.turn_in_flight(session.id):
            try:
                attempt = await intelligence_repo.create_attempt(
                    {
                        "sessionId": session.id,
                        "userMessageId": user_message.id,
                        "userId": current_user.id,
                        "status": "RUNNING",
                        "retryable": False,
                        "context": prior.context,
                        "askMode": ask_service.ASK_MODE_HTTP,
                    }
                )
            except ActiveGenerationAttemptError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another answer is already being generated for this conversation.",
                ) from error
            return await _run_http_attempt(
                user=current_user,
                session=session,
                user_message=user_message,
                attempt=attempt,
                context=prior.context,
            )
    except ask_service.TurnAlreadyInFlight as busy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=busy.rejection.message
        ) from busy


async def _resolve_ask_session(*, session_id: str | None, user_id: str, repo):
    """The session an HTTP turn belongs to: the one asked for, or a new one.

    **Authorised, not trusted.** A `sessionId` in a request body is a claim; `resolve_session_for_turn`
    is what turns it into a permission, and it is the same function the socket uses so the two cannot
    disagree about who owns a conversation.

    A refusal here is a `404` rather than a `403`, deliberately: telling a caller "that conversation
    exists but is not yours" confirms the id, which makes conversation ids probeable. The socket answers
    differently — it has an established connection and an `error` frame rather than a status code — and
    that asymmetry is recorded rather than resolved, because §14.2's "always 404" and the twelve shipped
    topic routes that answer `403` are a domain-wide inconsistency this route should not settle alone.
    """
    if not session_id:
        return await repo.create_chat_session(ask_service.new_session_row(user_id))

    resolution = await ask_service.resolve_session_for_turn(
        requested_session_id=session_id,
        current_session=None,
        user_id=user_id,
        find_session=repo.find_chat_session,
    )
    if not resolution.allowed or resolution.session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return resolution.session


# ===========================================================================
# Attachments and transcription
# ===========================================================================


@router.post(
    "/ask/attachments", response_model=models.AskAttachmentResponse, status_code=201
)
async def upload_ask_attachment(
    current_user: CurrentUser, file: UploadFile = File(...)
):
    """Store an image to send with a turn.

    **This closes §5.2.2, where the affordance existed and the capability did not.** Web's composer
    collected images and silently dropped them — no endpoint existed — and mobile's hook targeted a prefix
    that has never existed. A button that asserts a capability the product lacks is §1's territory.

    Validated before it is stored, on rules shared with the audio path so the two cannot drift. Type
    checking reads the declared content type, which is a real limitation and is documented at
    `attachments.validate_attachment`: it stops honest clients sending what the model cannot read and is
    not a defence against a hostile one.
    """
    from .conversation import attachments

    content = await file.read()
    rejection = attachments.validate_attachment(
        content_type=file.content_type,
        size=len(content),
        allowed=attachments.ALLOWED_IMAGE_TYPES,
    )
    if rejection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=rejection.message
        )

    from src.shared.infrastructure.storage import StorageError, storage_service

    await file.seek(0)
    try:
        stored = await storage_service.upload_upload_file(
            file,
            path_prefix=attachments.upload_path(user_id=current_user.id, kind="images"),
        )
    except StorageError as error:
        logger.error("Attachment upload failed for user %s: %s", current_user.id, error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="That upload did not go through. Please try again.",
        ) from error

    # The row is what makes the attachment attributable: without it an image in storage has no owner and
    # no turn. `chatMessageId` stays absent until a turn carries it — see `build_upload_row`.
    upload = await intelligence_repo.create_upload(
        attachments.build_upload_row(
            user_id=current_user.id,
            url=stored["url"],
            filename=stored["filename"],
            mime_type=file.content_type,
            size=stored.get("size") or len(content),
        )
    )
    return models.AskAttachmentResponse(
        id=upload.id,
        url=upload.url,
        filename=upload.filename,
        mime_type=upload.mime_type,
        size=upload.size,
    )


@router.delete("/ask/attachments/{upload_id}", status_code=204)
async def delete_ask_attachment(upload_id: str, current_user: CurrentUser):
    """Remove an attachment the learner took back out of the composer.

    **Without this every attached-then-removed image is orphaned** — stored, billed and never referenced
    — which is §6.1's reason for asking for the route. Mobile already declares `chat.imageDelete` against
    a prefix that does not exist.

    `404` for another learner's upload rather than `403`, matching `/ask`: "exists but is not yours"
    confirms the id. The row is deleted whether or not storage cooperates, and the reason is which failure
    is worse — a row pointing at a file that is gone renders as a broken image in a thread, where a file
    with no row is invisible and merely costs storage. Prefer the invisible one, and log it.
    """
    upload = await intelligence_repo.find_upload(upload_id, current_user.id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )

    from src.shared.infrastructure.storage import storage_service

    try:
        await storage_service.delete(upload.url)
    except (
        Exception
    ) as error:  # noqa: BLE001 — the row goes either way; see the docstring
        logger.warning("Attachment %s left behind in storage: %s", upload_id, error)

    await intelligence_repo.delete_upload(upload_id, current_user.id)
