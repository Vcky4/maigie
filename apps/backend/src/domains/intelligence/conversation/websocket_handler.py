"""
WebSocket chat endpoint — Intelligence Domain.

Handles real-time streaming AI chat over WebSocket. This is the primary
interface for learners to converse with Intelligence.

Migrated from routes/chat_ws.py into domains/intelligence/conversation/.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import func, select

from src.config import settings
from src.domains.billing.services.credit_consumption_service import (
    PURCHASE_DEEP_LINK,
)
from src.domains.identity.repository import IdentityRepository
from src.domains.intelligence.conversation import (
    ask_service,
    context_enrichment,
)
from src.domains.intelligence.conversation.chat_helpers import (
    _serialize_reply_preview,
)
from src.domains.intelligence.db_models import ChatMessage, ChatSession
from src.domains.intelligence.reasoning.llm.errors import LLMProviderError
from src.domains.intelligence.reasoning.llm.llm_service import llm_service
from src.domains.intelligence.repository import intelligence_repo
from src.shared.database import get_session_factory
from src.shared.exceptions import SubscriptionLimitError
from src.shared.infrastructure.rate_limit import check_rate_limit
from src.shared.infrastructure.socket_manager import manager

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

        # One reader bundle for the connection. Memoized in `context_enrichment`, so this is a
        # lookup rather than nine imports, but binding it here says once that every stage of a turn
        # reads through the same set — which is the property that keeps the owner filters in one
        # place (plan §5.5.14).
        readers = context_enrichment.production_readers()

        # And one effects bundle. Not memoized: `generate` closes over the router instance, and
        # whether a router exists is the thing that changes when the LLM layer is reconfigured.
        effects = ask_service.production_effects()

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

                # 3.2 Is this worth starting a turn for?
                #
                # Checked before the learner's own message is written, which is the whole reason it is
                # not inside `answer()`: a rejection after the write leaves the thread holding a row for
                # a turn that never happened, and on reload the learner sees their question with no
                # reply and no explanation.
                # `screen_turn` is validation *and* the rate limit in one call, so this transport cannot
                # apply half of it. The socket had no rate limiting at all before, and since both
                # clients send turns over the socket, a limit on `/ask` alone would have guarded the path
                # nobody uses (plan §4.5.9).
                rejection = await ask_service.screen_turn(
                    message=user_text, user_id=user.id, check_rate_limit=check_rate_limit
                )
                if rejection:
                    await manager.send_connection_json(
                        {
                            "type": "error",
                            "payload": {
                                "message": rejection.message,
                                "code": rejection.code,
                                # Not retryable as sent: the same message will be rejected again. The
                                # learner has to change it, which the message tells them how to do.
                                "retryable": False,
                                "sessionId": session.id,
                            },
                        },
                        connection_id,
                    )
                    continue

                # One turn at a time per conversation (plan §4.5.13). Acquired before the
                # learner's message is written, because a refused turn must leave no row, and
                # released in a `finally`, so a turn that fails frees the slot rather than locking
                # the learner out of their own conversation until the process restarts.
                try:
                    inflight = ask_service.turn_in_flight(session.id)
                    await inflight.__aenter__()
                except ask_service.TurnAlreadyInFlight as busy:
                    await manager.send_connection_json(
                        {
                            "type": "error",
                            "payload": {
                                "message": busy.rejection.message,
                                "code": busy.rejection.code,
                                # Retryable, unlike the validation refusals: the same message will
                                # work once the turn in flight finishes.
                                "retryable": True,
                                "sessionId": session.id,
                            },
                        },
                        connection_id,
                    )
                    continue

                try:
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
                                    "payload": {
                                        "message": "Reply target was not found in this room."
                                    },
                                },
                                connection_id,
                            )
                            continue

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
                        print(
                            f"🖼️ Message includes {len(file_urls_list)} image(s): {file_urls_list}"
                        )
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
                                "replyToMessageId": getattr(
                                    user_message, "reply_to_message_id", None
                                ),
                                "replyToMessage": _serialize_reply_preview(reply_target_message),
                            },
                        },
                        connection_id,
                    )

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
                                user_msg_count = (
                                    await sa_session.execute(count_stmt)
                                ).scalar() or 0
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

                    # 5. Answer the turn.
                    #
                    # Everything above the transport is `ask_service.answer()` — Decision C. History,
                    # enrichment, recall, the credit check, generation, the tool outcomes, pricing,
                    # consumption and the assistant row, in that order, with the reasons for the order in
                    # its docstring. `on_chunk` is how this transport streams; HTTP will pass `None` and
                    # must get the same answer, which is why streaming is a callback and not a branch.
                    #
                    # What stays here is what is genuinely the socket's: the frames.
                    ai_request_id = user_message.id
                    ai_reply_target_id = user_message.id

                    async def send_progress(
                        progress: int, stage: str, message: str, course_id: str = None, **kwargs
                    ):
                        """Tool-execution progress, for long-running work like course generation."""
                        await manager.send_json(
                            {
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
                            },
                            user.id,
                        )

                    async def stream_text(chunk: str, is_final: bool):
                        """Stream text chunks to the client.

                        **`is_final` stays snake_case on the wire. This is a decision, not an oversight.**
                        Every other key here is camelCase, so the inconsistency is real. All four consumers
                        across the two clients read it as `payload.is_final ?? payload.isFinal ?? false`, so
                        snake_case is the spelling that works everywhere today. The failure mode if one
                        consumer were missed on a rename is why not to do it casually: `?? false` reads a
                        missing flag as *not final*, so the streaming buffer never commits and the answer
                        hangs half-rendered rather than erroring. A silent hang is worse than an
                        inconsistent key. Normalise when all three repositories can drop the fallbacks in
                        one release — Phase 8 is the moment, and it is recorded there.
                        """
                        await manager.send_json(
                            {
                                "type": "stream",
                                "payload": {
                                    "chunk": chunk,
                                    "is_final": is_final,
                                    "sessionId": session.id,
                                    "requestId": ai_request_id,
                                    "replyToMessageId": ai_reply_target_id,
                                },
                            },
                            user.id,
                        )

                    identity_repo = IdentityRepository()
                    user_obj = await identity_repo.find_by_id(user.id)
                    if not user_obj:
                        await websocket.close()
                        return

                    try:
                        turn = await ask_service.answer(
                            message=user_text,
                            user=user,
                            user_obj=user_obj,
                            session=session,
                            user_message=user_message,
                            context=context,
                            ask_mode=ask_service.ASK_MODE_WEBSOCKET,
                            readers=readers,
                            effects=effects,
                            cache=context_enrichment.production_cache(),
                            image_url=file_urls_list[0] if file_urls_list else None,
                            on_chunk=stream_text,
                            on_progress=send_progress,
                        )
                    except ask_service.TurnRefused as refused:
                        await manager.send_connection_json(
                            {
                                "type": "credit_limit_error",
                                "message": refused.refusal.message,
                                "tier": refused.refusal.tier,
                                "is_daily_limit": refused.refusal.is_daily_limit,
                                "show_referral_option": True,
                                "blocked": True,
                                "purchaseDeepLink": PURCHASE_DEEP_LINK,
                                "sessionId": session.id,
                                "requestId": ai_request_id,
                                "replyToMessageId": ai_reply_target_id,
                            },
                            connection_id,
                        )
                        continue
                    except SubscriptionLimitError as e:
                        # A hard cap raised from inside the credit check rather than reported as
                        # unavailable. Same refusal to the learner; the message is the billing layer's.
                        await manager.send_connection_json(
                            {
                                "type": "credit_limit_error",
                                "message": (
                                    f"{e.message} Start a free trial for more credits, or refer "
                                    f"friends to earn bonus credits!"
                                ),
                                "tier": str(user_obj.tier) if user_obj.tier else "FREE",
                                "is_daily_limit": False,
                                "show_referral_option": True,
                                "blocked": True,
                                "purchaseDeepLink": PURCHASE_DEEP_LINK,
                                "sessionId": session.id,
                                "requestId": ai_request_id,
                                "replyToMessageId": ai_reply_target_id,
                            },
                            connection_id,
                        )
                        continue
                    # A failed generation is reported as a failure and **not persisted as an answer**.
                    #
                    # Both branches used to assign their error text to `response_text` and fall through to
                    # the persistence write, so a provider outage became a message from Maigie, stored in
                    # the learner's history and indistinguishable on reload from something the model said.
                    # §1 forbids exactly that. `answer()` now raises instead of returning, so there is no
                    # path from a failure to the write — and no credits are consumed, because consumption
                    # happens after the write that is skipped.
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

                    outcomes = turn.outcomes
                    main_content = turn.content
                    suggestion_text = turn.suggestion_text
                    skills_used = turn.skills_used
                    credit_result = turn.credit_result
                    assistant_message = turn.assistant_message
                    assistant_reply_preview = _serialize_reply_preview(
                        user_message,
                        fallback_user_name=getattr(user, "name", None),
                    )

                    for refusal in outcomes.connection_errors:
                        # On the connection, not to the user: this refusal belongs to the socket that asked.
                        await manager.send_connection_json(refusal, connection_id)

                    for event in outcomes.events:
                        await manager.send_json(event, user.id)

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
                        await manager.send_json(payload, user.id)
                    else:
                        if main_content:
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
                        await manager.send_json(
                            {
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
                            },
                            user.id,
                        )

                    # 14. Send component responses (query results first, then what changed)
                    for component_response in outcomes.components:
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
                            await manager.send_json(credit_info, user.id)

                    # 15. When split, suggestion is in assistant_final; no separate send needed

                    # 16. Background fact extraction from conversation (non-blocking)
                    # Only run every 5+ user messages to avoid excessive LLM calls
                    try:
                        user_msg_count = sum(1 for m in turn.history if m.get("role") == "user")
                        if user_msg_count >= 5 and user_msg_count % 5 == 0:
                            conversation_for_extraction = [
                                {
                                    "role": m.get("role", "user"),
                                    "content": m.get("parts", [""])[0] if m.get("parts") else "",
                                }
                                for m in turn.history
                            ]
                            conversation_for_extraction.append(
                                {"role": "user", "content": user_text}
                            )
                            asyncio.create_task(
                                llm_service.extract_user_facts_from_conversation(
                                    conversation_for_extraction, user.id
                                )
                            )
                    except Exception as fact_err:
                        logger.debug(f"Background fact extraction error (non-critical): {fact_err}")

                    continue  # Skip to next message
                finally:
                    await inflight.__aexit__(None, None, None)

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
