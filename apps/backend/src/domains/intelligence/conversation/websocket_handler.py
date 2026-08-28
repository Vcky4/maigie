"""
WebSocket chat endpoint — Intelligence Domain.

Handles real-time streaming AI chat over WebSocket. This is the primary
interface for learners to converse with Intelligence.

Migrated from routes/chat_ws.py into domains/intelligence/conversation/.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from src.config import settings
from src.core.celery_app import celery_app
from src.domains.billing.services.cost_calculator import calculate_ai_cost, calculate_revenue
from src.domains.billing.services.credit_consumption_service import (
    PURCHASE_DEEP_LINK,
    check_credit_availability,
    consume_credits,
    get_credit_usage,
)
from src.domains.identity.db_models import ModelPreference
from src.domains.identity.repository import IdentityRepository
from src.domains.intelligence.conversation import ask_service, context_enrichment
from src.domains.intelligence.conversation.chat_greeting import (
    _build_greeting_components,
    _build_greeting_context,
    _build_greeting_prompt,
)
from src.domains.intelligence.conversation.chat_helpers import (
    MAIGIE_MENTION_PATTERN,
    _extract_suggestion,
    _get_circle_group_for_session,
    _is_circle_member,
    _map_db_role_to_client,
    _serialize_reply_preview,
    _strip_maigie_mention,
)
from src.domains.intelligence.conversation.component_response import (
    format_action_component_response,
    format_list_component_response,
)
from src.domains.intelligence.db_models import ChatMessage, ChatSession
from src.domains.intelligence.reasoning.llm.adapter_registry import (
    get_feature_flag_service,
    get_llm_router,
)
from src.domains.intelligence.reasoning.llm.errors import LLMProviderError
from src.domains.intelligence.reasoning.llm.feature_flags import (
    PERSONAL_SCOPE,
    circle_scope,
)
from src.domains.intelligence.reasoning.llm.llm_service import llm_service
from src.domains.intelligence.reasoning.llm.registry import LlmTask, default_model_for
from src.domains.intelligence.reasoning.rag_service import rag_service
from src.domains.intelligence.repository import intelligence_repo
from src.shared.database import get_session_factory
from src.shared.exceptions import SubscriptionLimitError
from src.shared.infrastructure.socket_manager import manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill badge mapping — maps tool/action names to user-facing skill labels
# ---------------------------------------------------------------------------

_TOOL_SKILL_MAP: dict[str, dict[str, str]] = {
    # Course Management
    "get_user_courses": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "create_course": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "update_course_outline": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "delete_course": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    # Note Taking
    "get_user_notes": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "create_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "retake_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "add_summary_to_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "add_tags_to_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    # Goal Management
    "get_user_goals": {"id": "goals", "name": "Goal Management", "icon": "target"},
    "create_goal": {"id": "goals", "name": "Goal Management", "icon": "target"},
    # Scheduling
    "get_user_schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "check_schedule_conflicts": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "create_schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    # Resources
    "get_user_resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
    "recommend_resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
    # Memory & Profile
    "get_my_profile": {"id": "memory", "name": "Memory", "icon": "user"},
    "save_user_fact": {"id": "memory", "name": "Memory", "icon": "user"},
    "complete_review": {"id": "memory", "name": "Spaced Repetition", "icon": "refresh-cw"},
    "email_user": {"id": "email", "name": "Email", "icon": "mail"},
    # Planning
    "create_study_plan": {"id": "planning", "name": "Study Planning", "icon": "map"},
    "get_learning_insights": {"id": "planning", "name": "Learning Insights", "icon": "bar-chart"},
    "get_pending_nudges": {"id": "planning", "name": "Smart Nudges", "icon": "bell"},
    # Document Generation
    "generate_document": {
        "id": "documents",
        "name": "Document Generation",
        "icon": "file-arrow-down",
    },
}

_QUERY_TYPE_SKILL_MAP: dict[str, dict[str, str]] = {
    "courses": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "goals": {"id": "goals", "name": "Goal Management", "icon": "target"},
    "schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "notes": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
}


def _tool_to_skill_badge(tool_name: str) -> dict[str, str] | None:
    """Map a tool/action name to a skill badge for the frontend."""
    return _TOOL_SKILL_MAP.get(tool_name)


def _query_type_to_skill_badge(query_type: str) -> dict[str, str] | None:
    """Map a query result type to a skill badge."""
    return _QUERY_TYPE_SKILL_MAP.get(query_type)


# ---------------------------------------------------------------------------
# LLMProviderError category → user-facing message mapping
# ---------------------------------------------------------------------------

_ERROR_CATEGORY_MESSAGES: dict[str, str] = {
    "rate_limit": "AI service is busy. Please try again in a moment.",
    "auth": "AI service configuration error.",
    "invalid_request": "Unable to process this request.",
    "server_error": "AI service temporarily unavailable.",
    "overloaded": "All AI services are currently busy. Please try again.",
    "unsupported_capability": "This model does not support the requested operation.",
    "unknown": "An unexpected error occurred.",
}


async def _get_user_model_preference(
    user_id: str, capability: str = "chat"
) -> tuple[str, str] | None:
    """Fetch the user's model preference for a given capability from the DB.

    Returns a (provider, model_id) tuple if a preference is set, else None.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            stmt = select(ModelPreference).where(
                ModelPreference.user_id == user_id,
                ModelPreference.capability == capability,
            )
            result = await session.execute(stmt)
            pref = result.scalar_one_or_none()
        if pref and pref.provider and pref.model_id:
            return (pref.provider, pref.model_id)
    except Exception as e:
        logger.debug("Failed to fetch model preference for user %s: %s", user_id, e)
    return None


def register_chat_websocket_routes(router: APIRouter, db: Any):
    """Register ``/ws``; returns ``get_current_user_ws`` for the voice upload route."""

    async def get_current_user_ws(token: str = Query(...)):
        identity_repo = IdentityRepository()

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            email: str = payload.get("sub")
            if not email:
                raise HTTPException(status_code=403, detail="Invalid token")

            user = await identity_repo.find_by_email(email)
            if not user:
                raise HTTPException(status_code=403, detail="User not found")

            return user
        except JWTError:
            raise HTTPException(status_code=403, detail="Could not validate credentials")

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, user: dict = Depends(get_current_user_ws)):
        """
        Main WebSocket endpoint for AI Chat.
        """
        # 1. Connect
        connection_id = await manager.connect(websocket, user.id)

        # 2. Find or Create an active Chat Session
        # NOTE: The frontend can optionally pass `context.sessionId` per message to pin a conversation.
        factory = get_session_factory()
        async with factory() as sa_session:
            stmt = (
                select(ChatSession)
                .where(
                    ChatSession.user_id == user.id,
                    ChatSession.is_active == True,  # noqa: E712
                    ChatSession.is_space_room == False,  # noqa: E712
                )
                .order_by(ChatSession.updated_at.desc())
                .limit(1)
            )
            result = await sa_session.execute(stmt)
            session = result.scalar_one_or_none()

        if not session:
            session = await intelligence_repo.create_chat_session(
                ask_service.new_session_row(user.id)
            )

        # 2b. Deliver pending AI nudges on connect
        try:
            from src.domains.intelligence.memory.memory_service import get_pending_nudges

            pending = await get_pending_nudges(user.id)
            if pending:
                await manager.send_json(
                    {"type": "nudge", "nudges": pending},
                    user.id,
                )
        except Exception as e:
            print(f"⚠️ Failed to deliver nudges: {e}")

        try:
            while True:
                # 3. Receive Message (Text or JSON with context)
                raw_message = await websocket.receive_text()

                # Parse message - can be plain text or JSON with context
                user_text = raw_message
                context = None
                temp_id = None
                message_type = None

                try:
                    # Try to parse as JSON
                    message_data = json.loads(raw_message)
                    if isinstance(message_data, dict):
                        message_type = message_data.get("type")
                        if message_type == "ping":
                            await manager.send_connection_json({"type": "pong"}, connection_id)
                            continue
                        if message_type == "subscribe":
                            subscribe_context = message_data.get("context") or {}
                            subscribe_session_id = subscribe_context.get("sessionId")
                            if not subscribe_session_id:
                                await manager.send_connection_json(
                                    {
                                        "type": "error",
                                        "payload": {
                                            "message": "Missing sessionId for subscription."
                                        },
                                    },
                                    connection_id,
                                )
                                continue

                            subscribed_group = await _get_circle_group_for_session(
                                None, subscribe_session_id
                            )
                            if not subscribed_group or not await _is_circle_member(
                                subscribed_group, user.id
                            ):
                                await manager.send_connection_json(
                                    {
                                        "type": "error",
                                        "payload": {"message": "Unable to join this space room."},
                                    },
                                    connection_id,
                                )
                                continue

                            manager.join_room(connection_id, subscribe_session_id)
                            await manager.send_connection_json(
                                {
                                    "type": "subscribed",
                                    "payload": {"sessionId": subscribe_session_id},
                                },
                                connection_id,
                            )
                            continue
                        if message_type == "unsubscribe":
                            unsubscribe_context = message_data.get("context") or {}
                            unsubscribe_session_id = unsubscribe_context.get("sessionId")
                            if unsubscribe_session_id:
                                manager.leave_room(connection_id, unsubscribe_session_id)
                            await manager.send_connection_json(
                                {
                                    "type": "unsubscribed",
                                    "payload": {"sessionId": unsubscribe_session_id},
                                },
                                connection_id,
                            )
                            continue
                        user_text = message_data.get("message", raw_message)
                        context = message_data.get("context")
                        temp_id = message_data.get("tempId")
                        if context:
                            print(f"📥 Received context from frontend: {context}")
                except (json.JSONDecodeError, AttributeError):
                    # If not JSON, treat as plain text
                    pass

                # 3.1 Resolve the session this turn belongs to and authorise the learner for it.
                # The client pins `context.sessionId` per message to switch conversations without
                # reconnecting, so this runs on every turn and not just at connect. The rules live in
                # `ask_service.resolve_session_for_turn` — an unchecked session id is a write into
                # someone else's thread, and the decision is worth testing without a socket.
                resolution = await ask_service.resolve_session_for_turn(
                    requested_session_id=(context or {}).get("sessionId"),
                    current_session=session,
                    user_id=user.id,
                    find_session=intelligence_repo.find_chat_session,
                    space_group_for_session=lambda session_id: _get_circle_group_for_session(
                        None, session_id
                    ),
                    is_space_member=_is_circle_member,
                )
                if not resolution.allowed:
                    # `retryable` distinguishes a permission refusal, which will refuse again, from a
                    # transient read failure, which will not. Both clients already read this flag off
                    # `error` frames — the failed-generation path put it there — so refusing a turn
                    # whose session could not be read needs no new frame.
                    await manager.send_connection_json(
                        {
                            "type": "error",
                            "payload": {
                                "message": ask_service.SESSION_DENIAL_MESSAGES[resolution.denial],
                                "retryable": resolution.retryable,
                            },
                        },
                        connection_id,
                    )
                    continue

                session = resolution.session
                circle_group = resolution.space_group
                is_circle_session = resolution.is_space_room
                if is_circle_session:
                    manager.join_room(connection_id, session.id)

                should_reply_as_ai = True
                llm_user_text = user_text
                if is_circle_session:
                    should_reply_as_ai = bool(MAIGIE_MENTION_PATTERN.search(user_text or ""))
                    llm_user_text = _strip_maigie_mention(user_text)

                # 3.2.0 Check Retroactive Onboarding Need
                is_onboarded = getattr(user, "isOnboarded", False) or getattr(
                    user, "is_onboarded", False
                )
                if not is_onboarded:
                    try:
                        identity_repo = IdentityRepository()
                        fresh = await identity_repo.find_by_id(user.id)
                        if fresh:
                            is_onboarded = getattr(fresh, "is_onboarded", False)
                    except Exception:
                        pass

                needs_retro_onboarding = False
                if (
                    not is_circle_session
                    and is_onboarded
                    and not (context and context.get("reviewItemId"))
                ):
                    try:
                        from src.domains.identity.onboarding import (
                            get_onboarding_state,
                            save_onboarding_state,
                        )

                        state = await get_onboarding_state(None, user.id)
                        profile = state.get("profile") or {}
                        if not profile.get("commitmentRaw"):
                            needs_retro_onboarding = True
                            if state.get("stage") == "done":
                                state["stage"] = "commitment"
                                await save_onboarding_state(None, user.id, state)
                    except Exception as e:
                        logger.warning("Retroactive onboarding check failed: %s", e)

                # 3.2 Handle AI-initiated greeting for new chats
                if not is_circle_session and user_text == "__greeting__":
                    if needs_retro_onboarding:
                        # Hijack greeting to start retro-onboarding
                        user_text = ""
                    elif is_onboarded:
                        try:
                            greeting_ctx = await _build_greeting_context(None, user)
                            greeting_prompt = await _build_greeting_prompt(greeting_ctx)

                            # Stream callback
                            streamed_greeting_chunks: list[str] = []

                            async def stream_greeting(chunk: str, is_final: bool):
                                streamed_greeting_chunks.append(chunk)
                                await manager.send_json(
                                    {
                                        "type": "stream",
                                        "payload": {"chunk": chunk, "is_final": is_final},
                                    },
                                    user.id,
                                )

                            # Route greeting through the multi-provider LLM router.
                            # Resolve effective tier per the request's Usage_Scope:
                            # greetings only fire on Personal sessions, so the
                            # scope is always personal here.
                            feature_flags = get_feature_flag_service()
                            greeting_tier = await feature_flags.effective_tier_for_request(
                                user_id=user.id,
                                scope=PERSONAL_SCOPE,
                                personal_tier=(
                                    str(user.tier) if getattr(user, "tier", None) else None
                                ),
                            )
                            greeting_preference = await _get_user_model_preference(
                                user.id, capability="chat"
                            )
                            greeting_router = get_llm_router()
                            (
                                response_text,
                                usage_info,
                                _,
                                _,
                            ) = await greeting_router.route_request(
                                task=LlmTask.CHAT_DEFAULT,
                                user_id=user.id,
                                user_tier=greeting_tier,
                                model_preference=greeting_preference,
                                history=[],
                                user_message=greeting_prompt,
                                context=None,
                                user_name=getattr(user, "name", None),
                                stream_callback=stream_greeting,
                                usage_scope=PERSONAL_SCOPE,
                                space_id=None,
                            )

                            clean_greeting = response_text.strip()
                            if clean_greeting:
                                # Build greeting components before creating message (for persistence)
                                greeting_components = []
                                try:
                                    greeting_components = await _build_greeting_components(
                                        greeting_ctx
                                    )
                                except Exception as comp_err:
                                    logger.warning("Greeting components error: %s", comp_err)

                                # Save greeting as assistant message (with component data)
                                model_name = usage_info.get(
                                    "model_name",
                                    default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK),
                                )
                                input_tokens = usage_info.get("input_tokens", 0)
                                output_tokens = usage_info.get("output_tokens", 0)
                                greeting_data: dict = {
                                    "sessionId": session.id,
                                    "userId": user.id,
                                    "role": "ASSISTANT",
                                    "content": clean_greeting,
                                    "tokenCount": input_tokens + output_tokens,
                                    "inputTokens": input_tokens,
                                    "outputTokens": output_tokens,
                                    "modelName": model_name,
                                }
                                if greeting_components:
                                    greeting_data["componentData"] = greeting_components
                                await intelligence_repo.create_message(data=greeting_data)

                                # Send final plain-text message (deduped by frontend)
                                await manager.send_text_to_user(clean_greeting, user.id)

                                # Send optional components (e.g. pick-up course, schedule, goals)
                                for comp in greeting_components:
                                    await manager.send_json(comp, user.id)
                        except Exception as e:
                            logger.error("Greeting generation error: %s", e, exc_info=True)
                            # Fallback: send a simple greeting
                            first_name = (
                                getattr(user, "name", "").split()[0]
                                if getattr(user, "name", "")
                                else "there"
                            )
                            fallback = (
                                f"Hey {first_name}! 👋 What would you like to " "work on today?"
                            )
                            await manager.send_text_to_user(fallback, user.id)
                            await intelligence_repo.create_message(
                                data={
                                    "sessionId": session.id,
                                    "userId": user.id,
                                    "role": "ASSISTANT",
                                    "content": fallback,
                                }
                            )
                    # Skip the rest of the loop for greeting messages
                    continue

                # 4. Extract fileUrls from context (if any) — may be a JSON array or single string
                raw_file_urls = context.get("fileUrls") if context else None
                file_urls_list: list[str] = []
                if raw_file_urls:
                    if isinstance(raw_file_urls, list):
                        file_urls_list = raw_file_urls
                    elif isinstance(raw_file_urls, str):
                        # Try to parse as JSON array, otherwise treat as single URL
                        try:
                            import json as _json

                            parsed = _json.loads(raw_file_urls)
                            if isinstance(parsed, list):
                                file_urls_list = parsed
                            else:
                                file_urls_list = [raw_file_urls]
                        except (ValueError, TypeError):
                            file_urls_list = [raw_file_urls]

                # 4.1 Save User Message to DB (with imageUrl + imageUrls)
                reply_to_message_id = context.get("replyToMessageId") if context else None
                reply_target_message = None
                if reply_to_message_id:
                    factory = get_session_factory()
                    async with factory() as sa_session:
                        stmt = select(ChatMessage).where(
                            ChatMessage.id == reply_to_message_id,
                            ChatMessage.session_id == session.id,
                        )
                        result = await sa_session.execute(stmt)
                        reply_target_message = result.scalar_one_or_none()
                    # Fetch the user for the reply target if needed
                    if reply_target_message:
                        reply_target_user = await IdentityRepository().find_by_id(
                            reply_target_message.user_id
                        )
                        # Attach user as attribute for downstream access
                        reply_target_message.user = reply_target_user
                    if not reply_target_message:
                        await manager.send_connection_json(
                            {
                                "type": "error",
                                "payload": {"message": "Reply target was not found in this room."},
                            },
                            connection_id,
                        )
                        continue

                if (
                    is_circle_session
                    and not should_reply_as_ai
                    and reply_target_message
                    and str(reply_target_message.role) == "ASSISTANT"
                ):
                    should_reply_as_ai = True

                user_message_data = {
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "USER",
                    "content": user_text,
                }
                # If this message was sent from a review, persist the review thread ID
                if context and context.get("reviewItemId"):
                    user_message_data["reviewItemId"] = context["reviewItemId"]
                if file_urls_list:
                    user_message_data["imageUrl"] = file_urls_list[0]  # backward compat
                    user_message_data["imageUrls"] = file_urls_list
                    print(f"🖼️ Message includes {len(file_urls_list)} image(s): {file_urls_list}")
                if reply_target_message:
                    user_message_data["replyToMessageId"] = reply_target_message.id

                user_message = await intelligence_repo.create_message(data=user_message_data)

                # Track activity (streak + lastSeenAt)
                try:
                    from src.domains.intelligence.observation.tracker import record_activity

                    await record_activity(user.id)
                except Exception:
                    pass  # Non-blocking

                # 4.1a Send confirmation to client for ID correlation
                await manager.send_connection_json(
                    {
                        "type": "message_saved",
                        "payload": {
                            "id": user_message.id,
                            "tempId": temp_id,
                            "role": "user",
                            "sessionId": session.id,
                            "replyToMessageId": getattr(user_message, "reply_to_message_id", None),
                            "replyToMessage": _serialize_reply_preview(reply_target_message),
                        },
                    },
                    connection_id,
                )

                if is_circle_session:
                    await manager.send_room_json(
                        {
                            "type": "circle_message",
                            "payload": {
                                "id": user_message.id,
                                "sessionId": session.id,
                                "role": "user",
                                "content": user_text,
                                "timestamp": (
                                    user_message.created_at.isoformat()
                                    if hasattr(user_message.created_at, "isoformat")
                                    else str(user_message.created_at)
                                ),
                                "userId": user.id,
                                "userName": getattr(user, "name", None),
                                "replyToMessageId": getattr(
                                    user_message, "reply_to_message_id", None
                                ),
                                "replyToMessage": _serialize_reply_preview(reply_target_message),
                            },
                        },
                        session.id,
                        exclude_connection_id=connection_id,
                    )

                # Bump session updatedAt to move it to the top of history (Interaction based)
                factory = get_session_factory()
                async with factory() as sa_session:
                    stmt = (
                        sa_update(ChatSession)
                        .where(ChatSession.id == session.id)
                        .values(updated_at=datetime.now(UTC))
                    )
                    await sa_session.execute(stmt)
                    await sa_session.commit()

                # 4.1b Index uploaded images into knowledge base (fire-and-forget)
                if file_urls_list:
                    try:
                        from src.domains.knowledge.services.knowledge_base_service import (
                            index_user_uploads,
                        )

                        asyncio.create_task(
                            index_user_uploads(
                                user_id=user.id,
                                image_urls=file_urls_list,
                                chat_message_id=user_message.id,
                            )
                        )
                    except Exception as e:
                        logger.warning("Failed to start KB indexing: %s", e)

                # Name the conversation after the message that started it, so the history panel can
                # tell two threads apart. The gate and the wording are `ask_service`'s; the `count(*)`
                # stays here because it is a query, and it runs only when the cheap checks pass — a
                # conversation that already has a name does not pay for a count on every turn.
                try:
                    is_review_thread = bool(context and context.get("reviewItemId"))
                    if ask_service.session_needs_a_title(
                        current_title=getattr(session, "title", None),
                        message=user_text,
                        is_review_thread=is_review_thread,
                    ):
                        factory = get_session_factory()
                        async with factory() as sa_session:
                            count_stmt = (
                                select(func.count())
                                .select_from(ChatMessage)
                                .where(
                                    ChatMessage.session_id == session.id,
                                    ChatMessage.user_id == user.id,
                                    ChatMessage.role == "USER",
                                    ChatMessage.review_item_id.is_(None),
                                )
                            )
                            user_msg_count = (await sa_session.execute(count_stmt)).scalar() or 0
                        if ask_service.should_retitle_session(
                            current_title=getattr(session, "title", None),
                            user_message_count=user_msg_count,
                            message=user_text,
                            is_review_thread=is_review_thread,
                        ):
                            await intelligence_repo.update_chat_session(
                                session.id,
                                {"title": ask_service.derive_session_title(user_text)},
                            )
                            # Refresh session object with new title
                            session = await intelligence_repo.find_chat_session(session.id)
                except Exception as e:
                    logger.warning("Failed to update session title: %s", e)

                # 4.2 Onboarding router: for new users, run a guided flow instead of LLM chat.
                # Re-read `isOnboarded` from DB each iteration because the WS `user` object
                # was fetched at connection time and becomes stale after onboarding completes.
                is_onboarded = getattr(user, "isOnboarded", False) or getattr(
                    user, "is_onboarded", False
                )
                if not is_onboarded:
                    try:
                        identity_repo = IdentityRepository()
                        fresh_user = await identity_repo.find_by_id(user.id)
                        if fresh_user:
                            is_onboarded = getattr(fresh_user, "is_onboarded", False)
                    except Exception:
                        pass

                # Skip onboarding in review threads (spaced repetition), and only run for general chat.
                if (
                    not is_circle_session
                    and (not is_onboarded or needs_retro_onboarding)
                    and not (context and context.get("reviewItemId"))
                ):
                    try:
                        from src.domains.identity.onboarding import (
                            ensure_onboarding_initialized,
                            handle_onboarding_message,
                        )
                        from src.domains.intelligence.conversation.session_service import (
                            get_or_create_onboarding_session,
                        )

                        async def send_onboarding_progress(message: str) -> None:
                            await manager.send_json(
                                {
                                    "type": "event",
                                    "payload": {
                                        "status": "processing",
                                        "action": "onboarding",
                                        "message": message,
                                    },
                                },
                                user.id,
                            )

                        await ensure_onboarding_initialized(None, user.id)

                        # Use a dedicated onboarding session instead of the general session
                        onboarding_session = await get_or_create_onboarding_session(user.id, None)
                        onboarding_session_id = onboarding_session.id

                        # Move the user message we just saved to the onboarding session
                        # if it was saved to the general session
                        if user_message and session.id != onboarding_session_id:
                            factory = get_session_factory()
                            async with factory() as sa_session:
                                stmt = (
                                    sa_update(ChatMessage)
                                    .where(ChatMessage.id == user_message.id)
                                    .values(session_id=onboarding_session_id)
                                )
                                await sa_session.execute(stmt)
                                await sa_session.commit()
                            # Notify client that the message belongs to the onboarding session
                            # (sent as "event" type so existing WS handlers ignore unknown actions gracefully)
                            await manager.send_connection_json(
                                {
                                    "type": "event",
                                    "payload": {
                                        "status": "info",
                                        "action": "message_relocated",
                                        "messageId": user_message.id,
                                        "fromSessionId": session.id,
                                        "toSessionId": onboarding_session_id,
                                        "sessionType": "onboarding",
                                    },
                                },
                                connection_id,
                            )

                        onboarding_result = await handle_onboarding_message(
                            None,
                            user=user,
                            session_id=onboarding_session_id,
                            user_text=user_text,
                            image_url=file_urls_list[0] if file_urls_list else None,
                            progress_callback=send_onboarding_progress,
                        )

                        # Build onboarding component (for persistence)
                        onboarding_components = []
                        if onboarding_result.created_courses:
                            component = format_list_component_response(
                                component_type="CourseListMessage",
                                items=onboarding_result.created_courses,
                                text="Here are your courses:",
                            )
                            onboarding_components = [component]
                        onboarding_data: dict = {
                            "sessionId": onboarding_session_id,
                            "userId": user.id,
                            "role": "ASSISTANT",
                            "content": onboarding_result.reply_text,
                            "tokenCount": 0,
                            "modelName": "onboarding",
                        }
                        if onboarding_components:
                            onboarding_data["componentData"] = onboarding_components
                        await intelligence_repo.create_message(data=onboarding_data)

                        # Send credit limit error first if present (triggers upgrade modal)
                        if onboarding_result.credit_limit_error:
                            await manager.send_json(onboarding_result.credit_limit_error, user.id)

                        # Deep-link payload must reach the client before stream ends so the web app
                        # can store firstTopic before user refetch / redirect (avoids race with is_final).
                        if onboarding_result.first_topic:
                            await manager.send_json(
                                {
                                    "type": "event",
                                    "payload": {
                                        "status": "complete",
                                        "action": "onboarding_complete",
                                        "firstTopic": onboarding_result.first_topic,
                                        "onboardingSessionId": onboarding_session_id,
                                    },
                                },
                                user.id,
                            )

                        # Stream reply to the client so the user sees progress (word-by-word)
                        reply_text = onboarding_result.reply_text or ""
                        words = reply_text.split()
                        for i, word in enumerate(words):
                            chunk = word + (" " if i < len(words) - 1 else "")
                            await manager.send_json(
                                {
                                    "type": "stream",
                                    "payload": {
                                        "chunk": chunk,
                                        "is_final": i == len(words) - 1,
                                    },
                                },
                                user.id,
                            )
                        if not words:
                            await manager.send_text_to_user(reply_text, user.id)

                        # Send created courses as component for immediate UI rendering
                        for comp in onboarding_components:
                            await manager.send_json(comp, user.id)

                        continue
                    except Exception as e:
                        # If onboarding fails for any reason, fall back to normal LLM flow.
                        logger.error("Onboarding flow error: %s", e, exc_info=True)

                if is_circle_session and not should_reply_as_ai:
                    continue

                # 5. Build history for the prompt — the most recent turns, oldest-first.
                #
                # Ordering descending and then reversing is deliberate: ordering ascending with a limit
                # would take the *oldest* twelve messages of the conversation, so a long thread would
                # send the model the beginning of a conversation the learner left hours ago.
                #
                # Review threads are isolated from general chat and from each other, so a spaced-
                # repetition review does not inherit the learner's unrelated questions. In a space room
                # the whole room's messages are history; in a personal chat only the learner's are.
                review_item_id = context.get("reviewItemId") if context else None
                factory = get_session_factory()
                async with factory() as sa_session:
                    conditions = [ChatMessage.session_id == session.id]
                    if review_item_id:
                        conditions.append(ChatMessage.review_item_id == review_item_id)
                    else:
                        conditions.append(ChatMessage.review_item_id.is_(None))
                    if not is_circle_session:
                        conditions.append(ChatMessage.user_id == user.id)
                    stmt = (
                        select(ChatMessage)
                        .where(*conditions)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(ask_service.HISTORY_LIMIT)
                    )
                    result = await sa_session.execute(stmt)
                    history_records = list(reversed(result.scalars().all()))

                formatted_history = ask_service.format_history(history_records)

                # 5.5. Enrich the client's context with the rows its ids refer to.
                #
                # The whole block — the four mutually exclusive branches, their reads, the cache and
                # the direct-content overlay — is `context_enrichment.enrich_context`. It lives there
                # rather than here for two reasons. It is where both disclosures were (plan §5.5.11 and
                # §5.5.14): every read is keyed on an id the client supplied, so the ownership rule is
                # the whole job, and it is now stated once with the readers injected rather than
                # open-coded four times. And it is the half of the turn `answer()` needs on the HTTP
                # path, which cannot reach a body nested inside a WebSocket receive loop.
                enriched_context = await context_enrichment.enrich_context(
                    context=context,
                    user_id=user.id,
                    readers=context_enrichment.production_readers(),
                    cache=context_enrichment.production_cache(),
                )

                if reply_target_message:
                    if not enriched_context:
                        enriched_context = {}
                    enriched_context["replyContext"] = {
                        "messageId": reply_target_message.id,
                        "role": _map_db_role_to_client(str(reply_target_message.role)),
                        "content": getattr(reply_target_message, "content", "") or "",
                        "userId": getattr(reply_target_message, "user_id", None),
                        "userName": (
                            reply_target_message.user.name
                            if getattr(reply_target_message, "user", None)
                            else None
                        ),
                    }

                if is_circle_session:
                    if not enriched_context:
                        enriched_context = {}
                    enriched_context["spaceId"] = circle_group.space_id
                    enriched_context["spaceName"] = (
                        circle_group.space.name if getattr(circle_group, "space", None) else None
                    )
                    enriched_context["chatGroupId"] = circle_group.id
                    enriched_context["chatGroupName"] = circle_group.name
                    enriched_context["memberCount"] = (
                        len(circle_group.space.members)
                        if getattr(circle_group, "space", None) and circle_group.space.members
                        else 0
                    )
                    enriched_context["pageContext"] = ask_service.space_room_page_context(
                        has_reply_target=bool(reply_target_message)
                    )

                    # Inject knowledge base context for this chat group
                    try:
                        from src.domains.learning_spaces.services.kb_context_service import (
                            get_knowledge_context_for_chat_group,
                        )

                        kb_context = await get_knowledge_context_for_chat_group(
                            None, circle_group.space_id, circle_group.id
                        )
                        if kb_context:
                            enriched_context["knowledgeBaseContext"] = kb_context
                    except Exception as kb_err:
                        logger.warning(
                            "Failed to load KB context for group %s: %s", circle_group.id, kb_err
                        )

                # 5.6. Retrieve the learner's own material that this question may be about.
                #
                # The gate and the score filter are `ask_service.should_retrieve` and
                # `relevant_retrieved_items`. Both are pure, both were previously reachable only by
                # driving a live socket, and both are the kind of heuristic that gets edited without
                # anyone noticing — so they now have tests. Retrieval is skipped for space rooms because
                # Ask Maigie's retrieval is over the learner's *private* material, which must not reach
                # a shared room.
                if is_circle_session:
                    logger.debug("Skipping personal retrieval for a space room.")
                elif ask_service.should_retrieve(user_text):
                    try:
                        rag_results = await rag_service.retrieve_relevant_context(
                            query=user_text, user_id=user.id, limit=3
                        )
                        retrieved_items = ask_service.relevant_retrieved_items(rag_results)
                        if retrieved_items:
                            if not enriched_context:
                                enriched_context = {}
                            enriched_context["retrieved_items"] = retrieved_items
                            logger.debug(
                                "Retrieval contributed %d items to the prompt.",
                                len(retrieved_items),
                            )
                    except Exception as e:
                        # Retrieval is an enrichment, not a precondition. A turn without it is a worse
                        # answer; a turn that fails because of it is no answer.
                        logger.warning("Retrieval failed, continuing without it: %s", e)
                else:
                    logger.debug("Skipping retrieval for a trivial message.")

                # 5b. Inject long-term memory context (conversation summaries + learning insights)
                if is_circle_session:
                    print("⏭️ Skipping personal memory injection for circle chat.")
                else:
                    try:
                        from src.domains.intelligence.memory.memory_impl import get_memory_context

                        memory_ctx = await get_memory_context(user.id, query=llm_user_text)
                        if memory_ctx:
                            if not enriched_context:
                                enriched_context = {}
                            enriched_context["memory_context"] = memory_ctx
                    except Exception as e:
                        print(f"⚠️ Memory context retrieval failed: {e}")

                # 6. Get AI response with tool calling support
                ai_request_id = user_message.id if should_reply_as_ai else None
                ai_reply_target_id = user_message.id if should_reply_as_ai else None

                # 6a. Check credits BEFORE generating. This block used to sit at step 10,
                # roughly 280 lines below `route_request` — so a learner over their cap was charged a
                # live model call, had the whole answer streamed to them frame by frame, and was only
                # then told they had no credits. Nothing was persisted and `consume_credits` was never
                # reached, so the turn cost real money and recorded neither the spend nor the answer.
                # Consumption still happens after generation, at step 11, because only then are the
                # real token counts known. Check first, charge after.
                #
                estimated_total_tokens = ask_service.estimate_turn_tokens(
                    message=llm_user_text,
                    context=enriched_context,
                    history=formatted_history,
                )

                # Get user object for credit check
                identity_repo = IdentityRepository()
                user_obj = await identity_repo.find_by_id(user.id)
                if not user_obj:
                    await websocket.close()
                    return

                circle_credit_id = (
                    circle_group.space_id if is_circle_session and circle_group else None
                )

                try:
                    # Check if credits are available (will raise if hard cap reached)
                    is_available, warning_message = await check_credit_availability(
                        user_obj,
                        estimated_total_tokens,
                        db_client=None,
                        space_id=circle_credit_id,
                    )
                    if not is_available:
                        tier = str(user_obj.tier) if user_obj.tier else "FREE"
                        is_daily = False
                        if circle_credit_id:
                            error_message = (
                                "This circle has reached its shared credit limit. "
                                "Top up the circle credits or try again later."
                            )
                        else:
                            credit_usage = await get_credit_usage(user_obj)
                            daily_limit = credit_usage.get("daily_limit", 0)
                            used_today = credit_usage.get("credits_used_today", 0)
                            is_daily = (
                                tier == "FREE"
                                and daily_limit > 0
                                and (used_today + estimated_total_tokens > daily_limit)
                            )

                            if is_daily:
                                error_message = (
                                    f"Daily credit limit exceeded. You've used {used_today:,} "
                                    f"of {daily_limit:,} daily credits. "
                                    f"Resets in: {credit_usage.get('next_daily_reset', 'midnight')}. "
                                    f"Start a free trial for more credits, or refer friends to earn bonus credits!"
                                )
                            else:
                                error_message = (
                                    f"Monthly credit limit exceeded. You've used {credit_usage['credits_used']:,} "
                                    f"of {credit_usage['hard_cap']:,} credits. "
                                    f"Period resets: {credit_usage['period_end']}. "
                                    f"Start a free trial for unlimited usage, or refer friends to earn bonus credits!"
                                )

                        # Send error message with tier information as JSON for frontend handling
                        error_data = {
                            "type": "credit_limit_error",
                            "message": error_message,
                            "tier": tier,
                            "is_daily_limit": is_daily,
                            "show_referral_option": True,
                            "blocked": True,
                            "purchaseDeepLink": PURCHASE_DEEP_LINK,
                            "sessionId": session.id,
                            "requestId": ai_request_id,
                            "replyToMessageId": ai_reply_target_id,
                        }
                        await manager.send_connection_json(error_data, connection_id)
                        continue
                except SubscriptionLimitError as e:
                    # Get user tier for error message
                    identity_repo = IdentityRepository()
                    user_obj = await identity_repo.find_by_id(user.id)
                    tier = str(user_obj.tier) if user_obj and user_obj.tier else "FREE"

                    # Enhance error message with referral option
                    if circle_credit_id:
                        enhanced_message = "This circle has reached its shared credit limit."
                    else:
                        enhanced_message = (
                            f"{e.message} "
                            f"Start a free trial for more credits, or refer friends to earn bonus credits!"
                        )

                    error_data = {
                        "type": "credit_limit_error",
                        "message": enhanced_message,
                        "tier": tier,
                        "is_daily_limit": False,
                        "show_referral_option": True,
                        "blocked": True,
                        "purchaseDeepLink": PURCHASE_DEEP_LINK,
                        "sessionId": session.id,
                        "requestId": ai_request_id,
                        "replyToMessageId": ai_reply_target_id,
                    }
                    await manager.send_connection_json(error_data, connection_id)
                    continue

                # Define progress callback for tool execution updates
                async def send_progress(
                    progress: int, stage: str, message: str, course_id: str = None, **kwargs
                ):
                    """Send progress updates to frontend via WebSocket"""
                    payload = {
                        "type": "event",
                        "payload": {
                            "status": "processing",
                            "action": "ai_course_generation",
                            "course_id": course_id,
                            "courseId": course_id,
                            "progress": progress,
                            "stage": stage,
                            "message": message,
                            "sessionId": session.id,
                        },
                    }
                    if is_circle_session:
                        await manager.send_room_json(payload, session.id)
                    else:
                        await manager.send_json(payload, user.id)

                # Define stream callback for streaming text responses
                streamed_chunks = []

                async def stream_text(chunk: str, is_final: bool):
                    """Stream text chunks to frontend via WebSocket.

                    **`is_final` stays snake_case on the wire. This is a decision, not an oversight.**

                    Every other key in this payload is camelCase — `sessionId`, `requestId`,
                    `replyToMessageId` — so the inconsistency is real. It is left alone because all four
                    consumers across the two clients read it with the same defensive expression:

                        payload.is_final ?? payload.isFinal ?? false

                    (web `chatApi.ts:338`; mobile `useChatWebSocket.ts:77`, `circles/chat.tsx:390`,
                    `circles/topic-detail.tsx:226`.)

                    So `is_final` is the spelling that works everywhere today. Renaming to `isFinal`
                    would also work — every consumer falls back — but it would buy nothing until the
                    fallbacks are removed, and removing them means a coordinated change across three
                    repositories. The failure mode if one were missed is the reason not to do it
                    casually: `?? false` makes a missing flag read as *not final*, so the client's
                    streaming buffer would never commit and the answer would hang half-rendered rather
                    than error. A silent hang is a worse outcome than an inconsistent key.

                    Normalise it when all three repositories can drop the fallbacks in one release —
                    the plan's Phase 8 handoff is the moment for it, and it is recorded there.
                    """
                    streamed_chunks.append(chunk)
                    payload = {
                        "type": "stream",
                        "payload": {
                            "chunk": chunk,
                            "is_final": is_final,
                            "sessionId": session.id,
                            "requestId": ai_request_id,
                            "replyToMessageId": ai_reply_target_id,
                        },
                    }
                    if is_circle_session:
                        await manager.send_room_json(payload, session.id)
                    else:
                        await manager.send_json(payload, user.id)

                try:
                    # Determine usage scope (personal vs circle) and resolve
                    # the effective tier (free | plus) via the feature flag
                    # service. For Circle sessions, tier comes from the
                    # member's Seat_Tier in that Circle, not the user's
                    # Personal_Tier (Requirements 7.2, 7.3, 7.4).
                    feature_flags = get_feature_flag_service()
                    if is_circle_session and circle_group is not None:
                        request_scope = circle_scope(circle_group.space_id)
                        user_tier = await feature_flags.effective_tier_for_request(
                            user_id=user.id,
                            scope=request_scope,
                        )
                    else:
                        request_scope = PERSONAL_SCOPE
                        user_tier = await feature_flags.effective_tier_for_request(
                            user_id=user.id,
                            scope=PERSONAL_SCOPE,
                            personal_tier=(str(user.tier) if getattr(user, "tier", None) else None),
                        )
                    model_preference = await _get_user_model_preference(user.id, capability="chat")

                    # Route through the multi-provider LLM router
                    llm_router = get_llm_router()
                    (
                        response_text,
                        usage_info,
                        executed_actions,
                        query_results,
                    ) = await llm_router.route_request(
                        task=LlmTask.CHAT_TOOLS_SESSION,
                        user_id=user.id,
                        user_tier=user_tier,
                        model_preference=model_preference,
                        history=formatted_history,
                        user_message=llm_user_text,
                        context=enriched_context,
                        user_name=getattr(user, "name", None),
                        image_url=file_urls_list[0] if file_urls_list else None,
                        progress_callback=send_progress,
                        stream_callback=stream_text,
                        usage_scope=request_scope,
                        space_id=(
                            circle_group.space_id
                            if is_circle_session and circle_group is not None
                            else None
                        ),
                    )
                # A failed generation is reported as a failure and **not persisted as an answer**.
                #
                # Both branches below used to assign their error text to `response_text` and fall
                # through to step 12, which wrote it into a `ChatMessage` as the assistant's reply and
                # sent it as `assistant_final`. So a provider outage became a message from Maigie, stored
                # in the learner's history, indistinguishable on reload from something the model actually
                # said. The plan's §1 forbids exactly this: a failed turn is never rendered as an answer.
                #
                # It also disguised the real state of this surface. The LLM routing layer is unmigrated —
                # `get_llm_router()` raises `UnmigratedSubsystemError` unconditionally — so *every* turn
                # took the second branch and every learner was told "I'm sorry, I encountered an error"
                # by a Maigie that had never been asked. Failing visibly is what makes that legible.
                #
                # `error` rather than a new frame type: both clients already surface it, and the retry is
                # the learner sending again. No credits are consumed, because consumption happens at step
                # 11 which this skips.
                except LLMProviderError as e:
                    logger.error(
                        "LLM provider error: category=%s provider=%s model=%s msg=%s",
                        e.category,
                        e.provider,
                        e.model,
                        e.message,
                    )
                    await manager.send_connection_json(
                        {
                            "type": "error",
                            "payload": {
                                "message": _ERROR_CATEGORY_MESSAGES.get(
                                    e.category, _ERROR_CATEGORY_MESSAGES["unknown"]
                                ),
                                "retryable": True,
                                "sessionId": session.id,
                                "requestId": ai_request_id,
                            },
                        },
                        connection_id,
                    )
                    continue
                except Exception as e:
                    logger.error("Generation failed: %s", e, exc_info=True)
                    await manager.send_connection_json(
                        {
                            "type": "error",
                            "payload": {
                                "message": (
                                    "Maigie could not answer that just now. Please try again."
                                ),
                                "retryable": True,
                                "sessionId": session.id,
                                "requestId": ai_request_id,
                            },
                        },
                        connection_id,
                    )
                    continue

                # 7. Process query tool results (if any)
                # NOTE: Only show query results as components when the user EXPLICITLY asked
                # to view their data. This prevents showing course cards when the LLM was
                # just checking context for other operations like creating a study plan.
                query_component_responses = []

                # Both halves of this gate are `ask_service.should_render_query_components`: the learner
                # must have asked to *see* the data, and the turn must not also have created or updated
                # something. See its docstring for why the second half is not redundant.
                if ask_service.should_render_query_components(
                    message=llm_user_text, executed_actions=executed_actions
                ):
                    for query_result in query_results:
                        query_type = query_result.get("query_type", "")
                        component_type = query_result.get("component_type", "")
                        data = query_result.get("data", [])

                        if data and component_type:
                            # Format message based on count
                            count = len(data)
                            if count == 0:
                                message = f"You don't have any {query_type} yet."
                            elif count == 1:
                                message = (
                                    f"Here is your {query_type[:-1]}:"  # Remove 's' for singular
                                )
                            else:
                                message = f"Here are your {count} {query_type}:"

                            # Format as component response
                            component_response = format_list_component_response(
                                component_type=component_type,
                                items=data,
                                text=message,
                            )
                            if component_response:
                                query_component_responses.append(component_response)

                # 8. Process executed actions (from tool calls)
                # NOTE: Actions are already executed by tool handlers in llm_service
                # Here we only: log to DB, send success events, format component responses
                component_responses = []
                for action_info in executed_actions:
                    action_type = action_info["type"]
                    action_data = action_info["data"]
                    action_result = action_info["result"]

                    # Log action to DB
                    await intelligence_repo.create_action_log(
                        data={
                            "messageId": user_message.id,
                            "actionType": action_type,
                            "actionData": action_data if action_data else {},
                            "status": (
                                "SUCCESS" if action_result.get("status") == "success" else "FAILED"
                            ),
                            "error": (
                                None
                                if action_result.get("status") == "success"
                                else action_result.get("message")
                            ),
                        }
                    )

                    # Send credit limit error to client (for create_course failures from chat/onboarding)
                    if action_type == "create_course" and action_result.get("credit_limit_error"):
                        error_data = {
                            "type": "credit_limit_error",
                            "message": action_result.get("message", "Credit limit exceeded."),
                            "tier": action_result.get("tier", "FREE"),
                            "is_daily_limit": action_result.get("is_daily_limit", False),
                            "show_referral_option": action_result.get("show_referral_option", True),
                            "blocked": True,
                            "purchaseDeepLink": PURCHASE_DEEP_LINK,
                        }
                        await manager.send_connection_json(error_data, connection_id)
                        continue

                    # Send success event for create actions
                    if action_type == "create_course" and action_result.get("status") == "success":
                        course_id = action_result.get("course_id")
                        await manager.send_json(
                            {
                                "type": "event",
                                "payload": {
                                    "status": "success",
                                    "action": "create_course",
                                    "course_id": course_id,
                                    "courseId": course_id,
                                    "message": action_result.get(
                                        "message", "Course created successfully!"
                                    ),
                                },
                            },
                            user.id,
                        )

                    elif (
                        action_type == "complete_review"
                        and action_result.get("status") == "success"
                    ):
                        await manager.send_json(
                            {
                                "type": "event",
                                "payload": {
                                    "status": "success",
                                    "action": "complete_review",
                                    "message": action_result.get("message", "Review completed!"),
                                },
                            },
                            user.id,
                        )

                    elif (
                        action_type == "update_course_outline"
                        and action_result.get("status") == "success"
                    ):
                        course_id = action_result.get("course_id") or action_result.get("courseId")
                        await manager.send_json(
                            {
                                "type": "event",
                                "payload": {
                                    "status": "success",
                                    "action": "update_course_outline",
                                    "course_id": course_id,
                                    "courseId": course_id,
                                    "message": action_result.get(
                                        "message", "Course outline updated!"
                                    ),
                                },
                            },
                            user.id,
                        )

                    elif action_type == "recommend_resources":
                        # Queue background task for resource recommendations
                        celery_app.send_task(
                            "resources.recommend_from_chat",
                            kwargs={
                                "user_id": user.id,
                                "query": action_data.get("query", ""),
                                "topic_id": action_data.get("topicId"),
                                "course_id": action_data.get("courseId"),
                                "limit": action_data.get("limit", 10),
                            },
                            ignore_result=True,
                        )

                    # Format component response for all actions
                    component_response = format_action_component_response(
                        action_type=action_type,
                        action_result=action_result,
                        action_data=action_data,
                        user_id=user.id,
                        db=None,
                    )
                    if component_response:
                        component_responses.append(component_response)

                # 9. Clean response text
                clean_response = response_text.strip()

                # 11. Reconcile token usage, price it, and consume credits.
                #
                # Reconciliation and pricing are `ask_service.resolve_usage`; the estimate fallback is
                # the same function the pre-flight check used, so a learner cannot be checked against
                # one number and charged on another. See its docstring for why the fallback triggers
                # only when *both* counts are zero.
                usage = ask_service.resolve_usage(
                    usage_info=usage_info,
                    message=llm_user_text,
                    response=clean_response,
                    context=enriched_context,
                    history=formatted_history,
                    model_name=usage_info.get(
                        "model_name", default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK)
                    ),
                    user_tier=str(user_obj.tier) if user_obj.tier else "FREE",
                    cost_calculator=calculate_ai_cost,
                    revenue_calculator=calculate_revenue,
                )

                # Consume credits based on actual token usage
                credit_result = None
                try:
                    credit_result = await consume_credits(
                        user_obj,
                        usage.total_tokens,
                        operation="chat_message",
                        db_client=None,
                        space_id=circle_credit_id,
                    )
                except SubscriptionLimitError as e:
                    # This shouldn't happen if check above worked, but handle gracefully
                    logger.warning("Credit consumption failed after a completed turn: %s", e)

                # 12. Save AI Message to DB (with component data for persistence)
                assistant_review_item_id = None
                if enriched_context and enriched_context.get("reviewItemId"):
                    assistant_review_item_id = enriched_context["reviewItemId"]
                elif context and context.get("reviewItemId"):
                    assistant_review_item_id = context["reviewItemId"]

                all_components = query_component_responses + component_responses
                # When we have components, extract suggestion so it displays after them
                main_content = clean_response
                suggestion_text = None
                if all_components and clean_response:
                    main_content, suggestion_text = _extract_suggestion(clean_response)

                # Build skill badges from executed actions and query results. The badge *maps* still live
                # in this module and are injected, so `ask_service` stays importable without a cycle
                # until the generation stage moves too.
                skills_used = ask_service.build_skill_badges(
                    executed_actions=executed_actions,
                    query_results=query_results,
                    tool_badge=_tool_to_skill_badge,
                    query_badge=_query_type_to_skill_badge,
                )

                # `askMode` gets its first writer here. The column landed with migration 049 and nothing
                # set it, so per-surface metering was still impossible — which is the gap it exists to
                # close. `citations` is deliberately not passed: on this path generation goes through the
                # router (plan Decision F, amended), and no adapter returns grounding sources, so
                # "grounding was not attempted" is the truthful state and an absent key is how the column
                # says it.
                create_data = ask_service.build_assistant_row(
                    session_id=session.id,
                    user_id=user.id,
                    content=main_content,
                    usage=usage,
                    ask_mode=ask_service.ASK_MODE_WEBSOCKET,
                    review_item_id=assistant_review_item_id,
                    reply_to_message_id=ai_reply_target_id,
                    components=all_components,
                    suggestion_text=suggestion_text,
                )

                assistant_message = await intelligence_repo.create_message(data=create_data)
                assistant_reply_preview = _serialize_reply_preview(
                    user_message,
                    fallback_user_name=getattr(user, "name", None),
                )

                # 13. Send to client: main content, then components, then suggestion (so UI order is correct)
                if suggestion_text:
                    # Split response: send structured payload so frontend updates last message
                    payload = {
                        "type": "assistant_final",
                        "id": assistant_message.id,
                        "content": main_content,
                        "suggestionText": suggestion_text,
                        "skillsUsed": skills_used if skills_used else None,
                        "sessionId": session.id,
                        "requestId": ai_request_id,
                        "replyToMessageId": ai_reply_target_id,
                        "replyToMessage": assistant_reply_preview,
                    }
                    if is_circle_session:
                        await manager.send_room_json(payload, session.id)
                    else:
                        await manager.send_json(payload, user.id)
                else:
                    if main_content:
                        if is_circle_session:
                            await manager.send_room_json(
                                {
                                    "type": "assistant_final",
                                    "id": assistant_message.id,
                                    "content": main_content,
                                    "skillsUsed": skills_used if skills_used else None,
                                    "sessionId": session.id,
                                    "requestId": ai_request_id,
                                    "replyToMessageId": ai_reply_target_id,
                                    "replyToMessage": assistant_reply_preview,
                                },
                                session.id,
                            )
                        else:
                            await manager.send_json(
                                {
                                    "type": "assistant_final",
                                    "id": assistant_message.id,
                                    "content": main_content,
                                    "skillsUsed": skills_used if skills_used else None,
                                    "sessionId": session.id,
                                    "requestId": ai_request_id,
                                    "replyToMessageId": ai_reply_target_id,
                                    "replyToMessage": assistant_reply_preview,
                                },
                                user.id,
                            )
                    # Send confirmation with ID
                    payload = {
                        "type": "message_saved",
                        "payload": {
                            "id": assistant_message.id,
                            "role": "assistant",
                            "skillsUsed": skills_used if skills_used else None,
                            "sessionId": session.id,
                            "requestId": ai_request_id,
                            "replyToMessageId": ai_reply_target_id,
                            "replyToMessage": assistant_reply_preview,
                        },
                    }
                    if is_circle_session:
                        await manager.send_room_json(payload, session.id)
                    else:
                        await manager.send_json(payload, user.id)

                # 14. Send component responses (queries and actions)
                for component_response in query_component_responses + component_responses:
                    if is_circle_session:
                        await manager.send_room_json(component_response, session.id)
                    else:
                        await manager.send_json(component_response, user.id)

                # 14b. Send credit info (warning/notice) if applicable
                if credit_result:
                    credit_info = {}
                    if credit_result.warning:
                        credit_info["warning"] = credit_result.warning
                    if credit_result.notice:
                        credit_info["notice"] = credit_result.notice
                    if credit_info:
                        credit_info["type"] = "credit_info"
                        credit_info["purchaseDeepLink"] = PURCHASE_DEEP_LINK
                        credit_info["purchasedCreditsRemaining"] = (
                            credit_result.purchased_balance_remaining
                        )
                        credit_info["sessionId"] = session.id
                        credit_info["requestId"] = ai_request_id
                        if is_circle_session:
                            await manager.send_room_json(credit_info, session.id)
                        else:
                            await manager.send_json(credit_info, user.id)

                # 15. When split, suggestion is in assistant_final; no separate send needed

                # 16. Background fact extraction from conversation (non-blocking)
                # Only run every 5+ user messages to avoid excessive LLM calls
                try:
                    user_msg_count = sum(1 for m in formatted_history if m.get("role") == "user")
                    if not is_circle_session and user_msg_count >= 5 and user_msg_count % 5 == 0:
                        conversation_for_extraction = [
                            {
                                "role": m.get("role", "user"),
                                "content": m.get("parts", [""])[0] if m.get("parts") else "",
                            }
                            for m in formatted_history
                        ]
                        conversation_for_extraction.append(
                            {"role": "user", "content": llm_user_text}
                        )
                        asyncio.create_task(
                            llm_service.extract_user_facts_from_conversation(
                                conversation_for_extraction, user.id
                            )
                        )
                except Exception as fact_err:
                    logger.debug(f"Background fact extraction error (non-critical): {fact_err}")

                continue  # Skip to next message

        except WebSocketDisconnect:
            await manager.disconnect(connection_id)
        except Exception as e:
            print(f"WS Error: {e}")
            try:
                await websocket.close()
            except Exception:
                pass
            await manager.disconnect(connection_id)
            raise

    return get_current_user_ws
