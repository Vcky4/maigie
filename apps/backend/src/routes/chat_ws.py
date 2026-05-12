"""WebSocket chat endpoint — extracted from chat.py."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from prisma import Json, Prisma

from src.config import settings
from src.core.cache import cache
from src.core.celery_app import celery_app
from src.routes.chat_greeting import (
    _build_greeting_components,
    _build_greeting_context,
    _build_greeting_prompt,
)
from src.routes.chat_helpers import (
    MAIGIE_MENTION_PATTERN,
    _attach_topic_resources_context,
    _extract_suggestion,
    _get_circle_group_for_session,
    _is_circle_member,
    _map_db_role_to_client,
    _serialize_reply_preview,
    _strip_maigie_mention,
)
from src.services import note_service
from src.services.component_response_service import (
    format_action_component_response,
    format_list_component_response,
)
from src.services.cost_calculator import calculate_ai_cost, calculate_revenue
from src.services.credit_service import (
    check_credit_availability,
    consume_credits,
    get_credit_usage,
)
from src.services.llm.adapter_registry import get_llm_router
from src.services.llm.errors import LLMProviderError
from src.services.llm_registry import LlmTask, default_model_for
from src.services.llm_service import llm_service
from src.services.rag_service import rag_service
from src.services.socket_manager import manager
from src.utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

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
    db: Prisma, user_id: str, capability: str = "chat"
) -> tuple[str, str] | None:
    """Fetch the user's model preference for a given capability from the DB.

    Returns a (provider, model_id) tuple if a preference is set, else None.
    """
    try:
        pref = await db.modelpreference.find_first(
            where={"userId": user_id, "capability": capability}
        )
        if pref and pref.provider and pref.modelId:
            return (pref.provider, pref.modelId)
    except Exception as e:
        logger.debug("Failed to fetch model preference for user %s: %s", user_id, e)
    return None


def register_chat_websocket_routes(router: APIRouter, db: Prisma):
    """Register ``/ws``; returns ``get_current_user_ws`` for the voice upload route."""

    async def get_current_user_ws(token: str = Query(...)):
        if not db.is_connected():
            await db.connect()

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            email: str = payload.get("sub")
            if not email:
                raise HTTPException(status_code=403, detail="Invalid token")

            user = await db.user.find_unique(where={"email": email})
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
        session = await db.chatsession.find_first(
            where={"userId": user.id, "isActive": True, "isCircleRoom": False},
            order={"updatedAt": "desc"},
        )

        if not session:
            session = await db.chatsession.create(
                data={"userId": user.id, "title": "New Chat", "isCircleRoom": False}
            )

        # 2b. Deliver pending AI nudges on connect
        try:
            from src.services.memory_service import get_pending_nudges

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
                                db, subscribe_session_id
                            )
                            if not subscribed_group or not _is_circle_member(
                                subscribed_group, user.id
                            ):
                                await manager.send_connection_json(
                                    {
                                        "type": "error",
                                        "payload": {"message": "Unable to join this circle room."},
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

                # 3.1 If client pins a sessionId, switch to it (per-message)
                if context and context.get("sessionId"):
                    requested_session_id = context.get("sessionId")
                    try:
                        pinned = await db.chatsession.find_unique(
                            where={"id": requested_session_id}
                        )
                        if pinned:
                            pinned_circle_group = await _get_circle_group_for_session(db, pinned.id)
                            if pinned_circle_group:
                                if _is_circle_member(pinned_circle_group, user.id):
                                    session = pinned
                                else:
                                    await manager.send_connection_json(
                                        {
                                            "type": "error",
                                            "payload": {
                                                "message": "You are not allowed to access this circle room."
                                            },
                                        },
                                        connection_id,
                                    )
                                    continue
                            elif pinned.userId == user.id:
                                session = pinned
                            else:
                                await manager.send_connection_json(
                                    {
                                        "type": "error",
                                        "payload": {
                                            "message": "You are not allowed to access this chat session."
                                        },
                                    },
                                    connection_id,
                                )
                                continue
                    except Exception:
                        # If anything goes wrong, fall back to the current session
                        pass

                circle_group = await _get_circle_group_for_session(db, session.id)
                is_circle_session = bool(circle_group)
                if is_circle_session:
                    if not _is_circle_member(circle_group, user.id):
                        await manager.send_connection_json(
                            {
                                "type": "error",
                                "payload": {"message": "You are not a member of this circle room."},
                            },
                            connection_id,
                        )
                        continue
                    manager.join_room(connection_id, session.id)

                should_reply_as_ai = True
                llm_user_text = user_text
                if is_circle_session:
                    should_reply_as_ai = bool(MAIGIE_MENTION_PATTERN.search(user_text or ""))
                    llm_user_text = _strip_maigie_mention(user_text)

                # 3.2.0 Check Retroactive Onboarding Need
                is_onboarded = getattr(user, "isOnboarded", False)
                if not is_onboarded:
                    try:
                        fresh = await db.user.find_unique(where={"id": user.id})
                        if fresh:
                            is_onboarded = getattr(fresh, "isOnboarded", False)
                    except Exception:
                        pass

                needs_retro_onboarding = False
                if (
                    not is_circle_session
                    and is_onboarded
                    and not (context and context.get("reviewItemId"))
                ):
                    try:
                        from src.services.onboarding_service import (
                            get_onboarding_state,
                            save_onboarding_state,
                        )

                        state = await get_onboarding_state(db, user.id)
                        profile = state.get("profile") or {}
                        if not profile.get("commitmentRaw"):
                            needs_retro_onboarding = True
                            if state.get("stage") == "done":
                                state["stage"] = "commitment"
                                await save_onboarding_state(db, user.id, state)
                    except Exception as e:
                        logger.warning("Retroactive onboarding check failed: %s", e)

                # 3.2 Handle AI-initiated greeting for new chats
                if not is_circle_session and user_text == "__greeting__":
                    if needs_retro_onboarding:
                        # Hijack greeting to start retro-onboarding
                        user_text = ""
                    elif is_onboarded:
                        try:
                            greeting_ctx = await _build_greeting_context(db, user)
                            greeting_prompt = _build_greeting_prompt(greeting_ctx)

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

                            # Route greeting through the multi-provider LLM router
                            greeting_tier = (
                                str(user.tier) if getattr(user, "tier", None) else "FREE"
                            )
                            greeting_preference = await _get_user_model_preference(
                                db, user.id, capability="chat"
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
                            )

                            clean_greeting = response_text.strip()
                            if clean_greeting:
                                # Build greeting components before creating message (for persistence)
                                greeting_components = []
                                try:
                                    greeting_components = _build_greeting_components(greeting_ctx)
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
                                    greeting_data["componentData"] = Json(greeting_components)
                                await db.chatmessage.create(data=greeting_data)

                                # Send final plain-text message (deduped by frontend)
                                await manager.send_personal_message(clean_greeting, user.id)

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
                            await manager.send_personal_message(fallback, user.id)
                            await db.chatmessage.create(
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
                    reply_target_message = await db.chatmessage.find_first(
                        where={"id": reply_to_message_id, "sessionId": session.id},
                        include={"user": True},
                    )
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

                user_message = await db.chatmessage.create(data=user_message_data)

                # 4.1a Send confirmation to client for ID correlation
                await manager.send_connection_json(
                    {
                        "type": "message_saved",
                        "payload": {
                            "id": user_message.id,
                            "tempId": temp_id,
                            "role": "user",
                            "sessionId": session.id,
                            "replyToMessageId": getattr(user_message, "replyToMessageId", None),
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
                                    user_message.createdAt.isoformat()
                                    if hasattr(user_message.createdAt, "isoformat")
                                    else str(user_message.createdAt)
                                ),
                                "userId": user.id,
                                "userName": getattr(user, "name", None),
                                "replyToMessageId": getattr(user_message, "replyToMessageId", None),
                                "replyToMessage": _serialize_reply_preview(reply_target_message),
                            },
                        },
                        session.id,
                        exclude_connection_id=connection_id,
                    )

                # Bump session updatedAt to move it to the top of history (Interaction based)
                await db.chatsession.update(
                    where={"id": session.id}, data={"updatedAt": datetime.now(UTC)}
                )

                # 4.1b Index uploaded images into knowledge base (fire-and-forget)
                if file_urls_list:
                    try:
                        from src.services.knowledge_base_service import index_user_uploads

                        asyncio.create_task(
                            index_user_uploads(
                                user_id=user.id,
                                image_urls=file_urls_list,
                                chat_message_id=user_message.id,
                            )
                        )
                    except Exception as e:
                        logger.warning("Failed to start KB indexing: %s", e)

                # Keep ChatSession title meaningful when the frontend relies on DB history.
                # Update it from the very first general-chat USER message (not review threads).
                try:
                    if (
                        (not context or not context.get("reviewItemId"))
                        and getattr(session, "title", None) in (None, "", "New Chat")
                        and (user_text or "").strip()
                    ):
                        user_msg_count = await db.chatmessage.count(
                            where={
                                "sessionId": session.id,
                                "userId": user.id,
                                "role": "USER",
                                "reviewItemId": None,
                            }
                        )
                        if user_msg_count == 1:
                            cleaned = " ".join((user_text or "").strip().split())
                            title = cleaned[:50] + ("..." if len(cleaned) > 50 else "")
                            session = await db.chatsession.update(
                                where={"id": session.id}, data={"title": title}
                            )
                except Exception as e:
                    logger.warning("Failed to update session title: %s", e)

                # 4.2 Onboarding router: for new users, run a guided flow instead of LLM chat.
                # Re-read `isOnboarded` from DB each iteration because the WS `user` object
                # was fetched at connection time and becomes stale after onboarding completes.
                is_onboarded = getattr(user, "isOnboarded", False)
                if not is_onboarded:
                    try:
                        fresh_user = await db.user.find_unique(where={"id": user.id})
                        if fresh_user:
                            is_onboarded = getattr(fresh_user, "isOnboarded", False)
                    except Exception:
                        pass

                # Skip onboarding in review threads (spaced repetition), and only run for general chat.
                if (
                    not is_circle_session
                    and (not is_onboarded or needs_retro_onboarding)
                    and not (context and context.get("reviewItemId"))
                ):
                    try:
                        from src.services.onboarding_service import (
                            ensure_onboarding_initialized,
                            handle_onboarding_message,
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

                        await ensure_onboarding_initialized(db, user.id)
                        onboarding_result = await handle_onboarding_message(
                            db,
                            user=user,
                            session_id=session.id,
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
                            "sessionId": session.id,
                            "userId": user.id,
                            "role": "ASSISTANT",
                            "content": onboarding_result.reply_text,
                            "tokenCount": 0,
                            "modelName": "onboarding",
                        }
                        if onboarding_components:
                            onboarding_data["componentData"] = Json(onboarding_components)
                        await db.chatmessage.create(data=onboarding_data)

                        # Send credit limit error first if present (triggers upgrade modal)
                        if onboarding_result.credit_limit_error:
                            await manager.send_personal_message(
                                json.dumps(onboarding_result.credit_limit_error), user.id
                            )

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
                            await manager.send_personal_message(reply_text, user.id)

                        # Send created courses as component for immediate UI rendering
                        for comp in onboarding_components:
                            await manager.send_json(comp, user.id)

                        continue
                    except Exception as e:
                        # If onboarding fails for any reason, fall back to normal LLM flow.
                        logger.error("Onboarding flow error: %s", e, exc_info=True)

                if is_circle_session and not should_reply_as_ai:
                    continue

                # 5. Build History for Context (latest messages to reduce token usage)
                # IMPORTANT: Use the *most recent* messages; ordering asc with take would grab the oldest.
                history_take = 12
                history_where = {"sessionId": session.id}
                # Keep review conversations isolated from general chat (and from other reviews)
                if context and context.get("reviewItemId"):
                    history_where["reviewItemId"] = context["reviewItemId"]
                else:
                    history_where["reviewItemId"] = None
                if not is_circle_session:
                    history_where["userId"] = user.id
                history_records = await db.chatmessage.find_many(
                    where=history_where,
                    order={"createdAt": "desc"},
                    take=history_take,
                )
                history_records = list(reversed(history_records))

                # Format history for Gemini (including images)
                formatted_history = []
                for msg in history_records:
                    # Map DB roles to Gemini roles ('user' or 'model')
                    role = "user" if msg.role == "USER" else "model"
                    parts = [msg.content]
                    # Include images if present (imageUrls preferred, fallback to imageUrl)
                    msg_images = getattr(msg, "imageUrls", None) or []
                    if not msg_images and getattr(msg, "imageUrl", None):
                        msg_images = [msg.imageUrl]
                    for img_url in msg_images:
                        parts.append(img_url)
                    formatted_history.append({"role": role, "parts": parts})

                # 5.5. Enrich context with topic/course/note details if IDs are provided
                enriched_context = None
                if context:
                    enriched_context = context.copy()
                    cache_key = None
                    cached_context = None
                    note_id = context.get("noteId")
                    topic_id = context.get("topicId")
                    course_id = context.get("courseId")
                    review_item_id = context.get("reviewItemId")
                    if note_id or topic_id or course_id or review_item_id:
                        cache_key = cache.make_key(
                            [
                                "chat",
                                "context",
                                user.id,
                                note_id or "-",
                                topic_id or "-",
                                course_id or "-",
                                review_item_id or "-",
                            ]
                        )
                        cached_context = await cache.get(cache_key)

                    if cached_context:
                        enriched_context = {**context, **cached_context}
                    else:
                        # Fetch review details if reviewItemId is provided (review mode in chat)
                        if context.get("reviewItemId"):
                            review_id = context["reviewItemId"]
                            review = await db.reviewitem.find_first(
                                where={"id": review_id, "userId": user.id},
                                include={
                                    "topic": {"include": {"module": {"include": {"course": True}}}},
                                },
                            )
                            if review and review.topic:
                                enriched_context["pageContext"] = (
                                    "Review mode (spaced repetition): You are conducting a review for the topic below. "
                                    "1) Start with a brief, engaging summary of what the topic is about (2–3 sentences). "
                                    "2) Then ask 3–5 short quiz questions ONE AT A TIME. Do not list all questions at once. "
                                    "3) After each answer, give a brief explanation or feedback before asking the next question. "
                                    "4) Internally keep track of how many questions the user gets right vs wrong and their confidence level. "
                                    "5) When the user has answered all questions and you have given your final explanation, "
                                    "call the complete_review tool with a quality rating (0-5) based on their performance: "
                                    "0 = total blackout (0% correct), 1 = mostly wrong but recognised answers (≤20%), "
                                    "2 = mostly wrong but answers seemed easy once shown (≤40%), "
                                    "3 = correct but with serious difficulty (≈60%), "
                                    "4 = correct with minor hesitation (≈80%), 5 = perfect instant recall (100%). "
                                    "Also provide a brief score_summary like '4/5 correct, struggled with X'. "
                                    "After calling complete_review, tell the user their score and briefly explain what the "
                                    "quality rating means for their next review schedule (e.g. 'Next review in X days'). "
                                    "Do not ask the user to click any button; completion is automatic when you call complete_review."
                                )
                                enriched_context["topicId"] = review.topicId
                                enriched_context["topicTitle"] = review.topic.title
                                enriched_context["topicContent"] = review.topic.content or ""
                                enriched_context["reviewItemId"] = review.id
                                enriched_context["nextReviewAt"] = (
                                    review.nextReviewAt.isoformat()
                                    if hasattr(review.nextReviewAt, "isoformat")
                                    else str(review.nextReviewAt)
                                )
                                if review.topic.module and review.topic.module.course:
                                    enriched_context["courseId"] = review.topic.module.course.id
                                    enriched_context["courseTitle"] = (
                                        review.topic.module.course.title
                                    )
                                    enriched_context["courseDescription"] = (
                                        review.topic.module.course.description or ""
                                    )
                                    enriched_context["moduleTitle"] = review.topic.module.title
                        # Fetch note details if noteId is provided
                        elif context.get("noteId"):
                            note_id = context["noteId"]
                            note = await db.note.find_unique(
                                where={"id": note_id},
                                include={
                                    "topic": {"include": {"module": {"include": {"course": True}}}},
                                    "course": True,
                                },
                            )

                            # If note not found, check if noteId is actually a topicId
                            if not note:
                                print(
                                    f"⚠️ Note with ID {note_id} not found, checking if it's a topicId..."
                                )
                                topic = await db.topic.find_unique(
                                    where={"id": note_id},
                                    include={"module": {"include": {"course": True}}},
                                )
                                if topic:
                                    ln = await note_service.latest_note_for_topic(
                                        db, topic.id, user.id
                                    )
                                    enriched_context["topicId"] = topic.id
                                    enriched_context["topicTitle"] = topic.title
                                    enriched_context["topicContent"] = topic.content or ""
                                    if topic.module:
                                        enriched_context["moduleTitle"] = topic.module.title
                                        if topic.module.course:
                                            enriched_context["courseId"] = topic.module.course.id
                                            enriched_context["courseTitle"] = (
                                                topic.module.course.title
                                            )
                                            enriched_context["courseDescription"] = (
                                                topic.module.course.description or ""
                                            )
                                    if ln:
                                        print(
                                            f"✅ Found topic with ID {note_id}, using latest note ID: {ln.id}"
                                        )
                                        note = await db.note.find_unique(
                                            where={"id": ln.id},
                                            include={
                                                "topic": {
                                                    "include": {
                                                        "module": {"include": {"course": True}}
                                                    }
                                                },
                                                "course": True,
                                            },
                                        )
                                        enriched_context["noteId"] = ln.id

                            if note:
                                enriched_context["noteTitle"] = note.title
                                enriched_context["noteContent"] = note.content or ""
                                enriched_context["noteSummary"] = note.summary or ""
                                # If note is linked to a topic, include topic details
                                if note.topic:
                                    enriched_context["topicId"] = note.topic.id
                                    enriched_context["topicTitle"] = note.topic.title
                                    enriched_context["topicContent"] = note.topic.content or ""
                                    if note.topic.module:
                                        enriched_context["moduleTitle"] = note.topic.module.title
                                        if note.topic.module.course:
                                            enriched_context["courseId"] = (
                                                note.topic.module.course.id
                                            )
                                            enriched_context["courseTitle"] = (
                                                note.topic.module.course.title
                                            )
                                            enriched_context["courseDescription"] = (
                                                note.topic.module.course.description or ""
                                            )
                                # If note is linked to a course (but not via topic)
                                elif note.course:
                                    enriched_context["courseId"] = note.course.id
                                    enriched_context["courseTitle"] = note.course.title
                                    enriched_context["courseDescription"] = (
                                        note.course.description or ""
                                    )

                        # Fetch topic details if topicId is provided (and not already fetched from note)
                        elif context.get("topicId") and not enriched_context.get("topicTitle"):
                            topic_id = context["topicId"]
                            # Always preserve topicId in enriched_context (it should already be there from copy(), but ensure it)
                            enriched_context["topicId"] = topic_id
                            topic = await db.topic.find_unique(
                                where={"id": topic_id},
                                include={"module": {"include": {"course": True}}},
                            )
                            if topic:
                                enriched_context["topicTitle"] = topic.title
                                enriched_context["topicContent"] = topic.content or ""
                                if topic.module:
                                    enriched_context["moduleTitle"] = topic.module.title
                                    if topic.module.course:
                                        enriched_context["courseId"] = topic.module.course.id
                                        enriched_context["courseTitle"] = topic.module.course.title
                                        enriched_context["courseDescription"] = (
                                            topic.module.course.description or ""
                                        )
                                topic_notes = await db.note.find_many(
                                    where={"topicId": topic_id, "userId": user.id},
                                    order={"updatedAt": "asc"},
                                )
                                if topic_notes:
                                    blocks = []
                                    for n in topic_notes:
                                        head = (n.title or "Note").strip()
                                        body = (n.content or "").strip()
                                        blocks.append(
                                            f"## {head}\n{body}" if body else f"## {head}"
                                        )
                                    enriched_context["topicUserNotes"] = "\n\n---\n\n".join(
                                        b for b in blocks if b.strip()
                                    )
                            else:
                                # Topic not found - log for debugging but keep topicId in context
                                print(
                                    f"⚠️ Topic with ID {topic_id} not found during context enrichment"
                                )
                                print(
                                    "⚠️ This topicId will still be passed to action service for validation"
                                )

                        # Fetch course details if courseId is provided (and not already fetched)
                        elif context.get("courseId") and not enriched_context.get("courseTitle"):
                            course = await db.course.find_unique(where={"id": context["courseId"]})
                            if course:
                                enriched_context["courseTitle"] = course.title
                                enriched_context["courseDescription"] = course.description or ""

                        # Always attach topic resources if topic context is available.
                        if enriched_context.get("topicId"):
                            await _attach_topic_resources_context(
                                db, user.id, enriched_context["topicId"], enriched_context
                            )

                        if cache_key:
                            cacheable_context = {
                                key: value
                                for key, value in enriched_context.items()
                                if key
                                not in {
                                    "pageContext",
                                    "content",
                                    "noteContent",
                                    "retrieved_items",
                                    "topicResources",
                                    "topicUploadedResources",
                                }
                            }
                            await cache.set(cache_key, cacheable_context, expire=300)

                    # Include direct content if provided (for summaries, etc.)
                    if context.get("content"):
                        enriched_context["content"] = context["content"]

                    # Include note content if provided directly (not via noteId)
                    if context.get("noteContent") and not enriched_context.get("noteContent"):
                        enriched_context["noteContent"] = context["noteContent"]

                if reply_target_message:
                    if not enriched_context:
                        enriched_context = {}
                    enriched_context["replyContext"] = {
                        "messageId": reply_target_message.id,
                        "role": _map_db_role_to_client(str(reply_target_message.role)),
                        "content": getattr(reply_target_message, "content", "") or "",
                        "userId": getattr(reply_target_message, "userId", None),
                        "userName": (
                            reply_target_message.user.name
                            if getattr(reply_target_message, "user", None)
                            else None
                        ),
                    }

                if is_circle_session:
                    if not enriched_context:
                        enriched_context = {}
                    enriched_context["circleId"] = circle_group.circleId
                    enriched_context["circleName"] = (
                        circle_group.circle.name if getattr(circle_group, "circle", None) else None
                    )
                    enriched_context["chatGroupId"] = circle_group.id
                    enriched_context["chatGroupName"] = circle_group.name
                    enriched_context["memberCount"] = (
                        len(circle_group.circle.members)
                        if getattr(circle_group, "circle", None) and circle_group.circle.members
                        else 0
                    )
                    enriched_context["pageContext"] = (
                        "You are participating in a shared circle chat room. "
                        "Respond with the circle's discussion in mind, not the user's private study history. "
                        "Keep responses collaborative and suitable for the whole room."
                    )
                    if reply_target_message:
                        enriched_context[
                            "pageContext"
                        ] += " When replyContext is present, respond to that specific room message."

                # 5.6. Perform Semantic Search (RAG) to find relevant items
                # This helps the LLM know about items the user might be referring to
                # Skip RAG for short/simple messages to improve response time
                simple_messages = {
                    "hi",
                    "hello",
                    "hey",
                    "thanks",
                    "thank you",
                    "ok",
                    "okay",
                    "yes",
                    "no",
                    "bye",
                    "goodbye",
                    "help",
                    "?",
                    "cool",
                    "great",
                    "nice",
                    "good",
                    "bad",
                    "sure",
                    "yep",
                    "nope",
                    "what",
                    "why",
                    "how",
                    "when",
                    "where",
                    "who",
                    "hm",
                    "hmm",
                    "ah",
                    "oh",
                }
                user_text_lower = user_text.lower().strip()
                should_run_rag = (
                    len(user_text) > 15
                    and user_text_lower not in simple_messages
                    and not user_text_lower.startswith(("hi ", "hello ", "hey "))
                )

                if is_circle_session:
                    print("⏭️ Skipping personal RAG for circle chat.")
                elif should_run_rag:
                    try:
                        # We use a broader limit to catch potential matches
                        rag_results = await rag_service.retrieve_relevant_context(
                            query=user_text, user_id=user.id, limit=3
                        )

                        if rag_results:
                            retrieved_items = []
                            for item in rag_results:
                                # Filter by score (heuristic) - keep only reasonably relevant items
                                # Note: exact matches usually have high scores
                                # embedding_service uses 'similarity', rag_service might use 'score' in some contexts
                                score = item.get("similarity") or item.get("score") or 0
                                if score < 0.65:
                                    continue

                                obj_data = item.get("data", {})
                                obj_type = item.get("objectType", "unknown")
                                obj_id = item.get("objectId")
                                obj_title = obj_data.get("title", "Untitled")

                                # Format for LLM context
                                retrieved_items.append(
                                    f"- {obj_type.upper()}: {obj_title} (ID: {obj_id})"
                                )

                            if retrieved_items:
                                if not enriched_context:
                                    enriched_context = {}
                                enriched_context["retrieved_items"] = retrieved_items
                                print(
                                    f"🔍 RAG found {len(retrieved_items)} relevant items for context"
                                )

                    except Exception as e:
                        print(f"⚠️ RAG context retrieval failed: {e}")
                        # Continue without RAG results
                else:
                    (
                        print(f"⏭️ Skipping RAG for simple message: '{user_text[:30]}...'")
                        if len(user_text) > 30
                        else print(f"⏭️ Skipping RAG for simple message: '{user_text}'")
                    )

                # 5b. Inject long-term memory context (conversation summaries + learning insights)
                if is_circle_session:
                    print("⏭️ Skipping personal memory injection for circle chat.")
                else:
                    try:
                        from src.services.memory_service import get_memory_context

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
                    """Stream text chunks to frontend via WebSocket"""
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
                    # Determine user tier and model preference for routing
                    user_tier = str(user.tier) if getattr(user, "tier", None) else "FREE"
                    model_preference = await _get_user_model_preference(
                        db, user.id, capability="chat"
                    )

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
                    )
                except LLMProviderError as e:
                    logger.error(
                        "LLM provider error: category=%s provider=%s model=%s msg=%s",
                        e.category,
                        e.provider,
                        e.model,
                        e.message,
                    )
                    user_facing_msg = _ERROR_CATEGORY_MESSAGES.get(
                        e.category, _ERROR_CATEGORY_MESSAGES["unknown"]
                    )
                    response_text = user_facing_msg
                    usage_info = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model_name": default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK),
                    }
                    executed_actions = []
                    query_results = []
                except Exception as e:
                    logger.error(f"LLM service error: {e}", exc_info=True)
                    response_text = "I'm sorry, I encountered an error. Please try again."
                    usage_info = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model_name": default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK),
                    }
                    executed_actions = []
                    query_results = []

                # 7. Process query tool results (if any)
                # NOTE: Only show query results as components when the user EXPLICITLY asked
                # to view their data. This prevents showing course cards when the LLM was
                # just checking context for other operations like creating a study plan.
                query_component_responses = []

                # Check if any "create" or "update" actions were executed
                has_create_or_update_actions = any(
                    action_info["type"].startswith(("create_", "update_"))
                    for action_info in executed_actions
                )

                # Check if user explicitly asked to VIEW their data (not just context lookup)
                user_text_lower = llm_user_text.lower()
                explicit_view_keywords = [
                    "show my",
                    "list my",
                    "view my",
                    "see my",
                    "what are my",
                    "show me my",
                    "display my",
                    "get my",
                    "fetch my",
                    "my courses",
                    "my goals",
                    "my schedule",
                    "my notes",
                    "my resources",
                    "what courses",
                    "what goals",
                    "what schedule",
                    "what notes",
                    "show courses",
                    "show goals",
                    "show schedule",
                    "show notes",
                    "list courses",
                    "list goals",
                    "list schedule",
                    "list notes",
                ]
                user_wants_to_view = any(kw in user_text_lower for kw in explicit_view_keywords)

                # Only show query results as components if:
                # 1. No create/update actions were executed, AND
                # 2. User explicitly asked to view their data
                if not has_create_or_update_actions and user_wants_to_view:
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
                    await db.aiactionlog.create(
                        data={
                            "messageId": user_message.id,
                            "actionType": action_type,
                            "actionData": Json(action_data) if action_data else Json({}),
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
                        }
                        await manager.send_personal_message(json.dumps(error_data), user.id)
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
                    component_response = await format_action_component_response(
                        action_type=action_type,
                        action_result=action_result,
                        action_data=action_data,
                        user_id=user.id,
                        db=db,
                    )
                    if component_response:
                        component_responses.append(component_response)

                # 9. Clean response text
                clean_response = response_text.strip()

                # 10. Calculate costs and consume credits
                # (Keep existing credit consumption logic)
                # Estimate tokens needed: user message + context + history (approximate 4 chars per token)
                estimated_input_tokens = (
                    len(llm_user_text)
                    + len(str(enriched_context or ""))
                    + len(str(formatted_history))
                ) // 4
                # Reserve credits for response (reduced estimate for cost savings)
                estimated_output_tokens = 500  # Reduced from 1000 for cost optimization
                estimated_total_tokens = estimated_input_tokens + estimated_output_tokens

                # Get user object for credit check
                user_obj = await db.user.find_unique(where={"id": user.id})
                if not user_obj:
                    await websocket.close()
                    return

                circle_credit_id = (
                    circle_group.circleId if is_circle_session and circle_group else None
                )

                try:
                    # Check if credits are available (will raise if hard cap reached)
                    is_available, warning_message = await check_credit_availability(
                        user_obj,
                        estimated_total_tokens,
                        db_client=db,
                        circle_id=circle_credit_id,
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
                            "sessionId": session.id,
                            "requestId": ai_request_id,
                            "replyToMessageId": ai_reply_target_id,
                        }
                        await manager.send_connection_json(error_data, connection_id)
                        continue
                except SubscriptionLimitError as e:
                    # Get user tier for error message
                    user_obj = await db.user.find_unique(where={"id": user.id})
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
                        "sessionId": session.id,
                        "requestId": ai_request_id,
                        "replyToMessageId": ai_reply_target_id,
                    }
                    await manager.send_connection_json(error_data, connection_id)
                    continue

                # 11. Calculate actual token usage and consume credits
                # Use actual token counts from API
                actual_input_tokens = usage_info.get("input_tokens", 0)
                actual_output_tokens = usage_info.get("output_tokens", 0)

                # Fallback to estimation if API didn't provide token counts
                if actual_input_tokens == 0 and actual_output_tokens == 0:
                    actual_input_tokens = (
                        len(llm_user_text)
                        + len(str(enriched_context or ""))
                        + len(str(formatted_history))
                    ) // 4
                    actual_output_tokens = len(clean_response) // 4

                actual_total_tokens = actual_input_tokens + actual_output_tokens

                # Consume credits based on actual token usage
                try:
                    await consume_credits(
                        user_obj,
                        actual_total_tokens,
                        operation="chat_message",
                        db_client=db,
                        circle_id=circle_credit_id,
                    )
                except SubscriptionLimitError as e:
                    # This shouldn't happen if check above worked, but handle gracefully
                    print(f"Warning: Credit consumption failed: {e}")

                # Calculate costs and revenue
                model_name = usage_info.get(
                    "model_name", default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK)
                )
                cost_usd = calculate_ai_cost(
                    input_tokens=actual_input_tokens,
                    output_tokens=actual_output_tokens,
                    model_name=model_name,
                )
                revenue_usd = calculate_revenue(
                    input_tokens=actual_input_tokens,
                    output_tokens=actual_output_tokens,
                    user_tier=str(user_obj.tier) if user_obj.tier else "FREE",
                )

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

                create_data: dict = {
                    "sessionId": session.id,
                    "userId": user.id,
                    "reviewItemId": assistant_review_item_id,
                    "role": "ASSISTANT",
                    "content": main_content,
                    "tokenCount": actual_total_tokens,
                    "inputTokens": actual_input_tokens,
                    "outputTokens": actual_output_tokens,
                    "modelName": model_name,
                    "costUsd": cost_usd,
                    "revenueUsd": revenue_usd,
                }
                if ai_reply_target_id:
                    create_data["replyToMessageId"] = ai_reply_target_id
                if all_components:
                    create_data["componentData"] = Json(all_components)
                if suggestion_text:
                    create_data["suggestionText"] = suggestion_text

                assistant_message = await db.chatmessage.create(data=create_data)
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
                                    "sessionId": session.id,
                                    "requestId": ai_request_id,
                                    "replyToMessageId": ai_reply_target_id,
                                    "replyToMessage": assistant_reply_preview,
                                },
                                session.id,
                            )
                        else:
                            await manager.send_personal_message(main_content, user.id)
                    # Send confirmation with ID
                    payload = {
                        "type": "message_saved",
                        "payload": {
                            "id": assistant_message.id,
                            "role": "assistant",
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
            manager.disconnect(connection_id)
        except Exception as e:
            print(f"WS Error: {e}")
            try:
                await websocket.close()
            except Exception:
                pass
            manager.disconnect(connection_id)
            raise

    return get_current_user_ws
