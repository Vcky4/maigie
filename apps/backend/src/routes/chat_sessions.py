"""REST routes for chat sessions and message history (extracted from chat.py)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from src.dependencies import CurrentUser, DBDep
from src.routes.chat_helpers import (
    STUDIO_TOPIC_OPENER_INSTRUCTION,
    _is_circle_member,
    _map_db_role_to_client,
    _serialize_reply_preview,
    _static_studio_topic_opener,
)
from src.services.chat_session_service import (
    get_or_create_onboarding_session,
    merge_generic_sessions,
)
from src.services.llm_registry import LlmTask, default_model_for, gemini_api_key
from src.services.llm_service import llm_service

session_router = APIRouter()
logger = logging.getLogger(__name__)


@session_router.get("/my-messages", response_model=dict)
async def get_my_general_session(
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Get the general chat session for the current user.
    Creates one if it doesn't exist. Excludes onboarding sessions.
    """
    session = await merge_generic_sessions(current_user.id, db)

    if not session:
        # No generic session found - create one
        # Deactivate others first
        await db.chatsession.update_many(
            where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
            data={"isActive": False},
        )
        session = await db.chatsession.create(
            data={
                "userId": current_user.id,
                "title": "Chat",
                "isActive": True,
                "isCircleRoom": False,
                "sessionType": "general",
            }
        )
    else:
        # Mark it active if it wasn't
        if not session.isActive:
            await db.chatsession.update_many(
                where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
                data={"isActive": False},
            )
            session = await db.chatsession.update(where={"id": session.id}, data={"isActive": True})

    return {
        "id": session.id,
        "title": session.title,
        "isActive": bool(session.isActive),
        "sessionType": getattr(session, "sessionType", "general"),
        "createdAt": (
            session.createdAt.isoformat()
            if hasattr(session.createdAt, "isoformat")
            else str(session.createdAt)
        ),
        "updatedAt": (
            session.updatedAt.isoformat()
            if hasattr(session.updatedAt, "isoformat")
            else str(session.updatedAt)
        ),
    }


@session_router.get("/onboarding-session", response_model=dict)
async def get_my_onboarding_session(
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Get the dedicated onboarding session for the current user.
    Creates one if it doesn't exist. Onboarding messages are kept
    separate from the general conversation session.
    """
    session = await get_or_create_onboarding_session(current_user.id, db)

    return {
        "id": session.id,
        "title": session.title or "Onboarding",
        "isActive": bool(session.isActive),
        "sessionType": "onboarding",
        "createdAt": (
            session.createdAt.isoformat()
            if hasattr(session.createdAt, "isoformat")
            else str(session.createdAt)
        ),
        "updatedAt": (
            session.updatedAt.isoformat()
            if hasattr(session.updatedAt, "isoformat")
            else str(session.updatedAt)
        ),
    }


@session_router.get("/sessions", response_model=dict)
async def list_my_chat_sessions(
    current_user: CurrentUser,
    db: DBDep,
    take: int = Query(20, ge=1, le=100),
):
    """
    List the current user's chat sessions (for conversation history UI).
    Excludes onboarding sessions — those are accessed via /onboarding-session.
    """
    # Trigger JIT merge for generic sessions before listing
    await merge_generic_sessions(current_user.id, db)

    sessions = await db.chatsession.find_many(
        where={
            "userId": current_user.id,
            "isCircleRoom": False,
            "sessionType": {"not": "onboarding"},
        },
        include={"topic": {"include": {"module": True}}},
        order={"updatedAt": "desc"},
        take=take,
    )

    # Attach a lightweight "last message" preview
    result = []
    for s in sessions:
        last_msg = await db.chatmessage.find_first(
            where={"sessionId": s.id, "userId": current_user.id},
            order={"createdAt": "desc"},
        )
        result.append(
            {
                "id": s.id,
                "title": s.title or "Chat",
                "isActive": bool(s.isActive),
                "createdAt": (
                    s.createdAt.isoformat()
                    if hasattr(s.createdAt, "isoformat")
                    else str(s.createdAt)
                ),
                "updatedAt": (
                    s.updatedAt.isoformat()
                    if hasattr(s.updatedAt, "isoformat")
                    else str(s.updatedAt)
                ),
                "lastMessageAt": (
                    last_msg.createdAt.isoformat()
                    if last_msg and hasattr(last_msg.createdAt, "isoformat")
                    else None
                ),
                "lastMessagePreview": (
                    last_msg.content[:140] if last_msg and last_msg.content else None
                ),
                "courseId": getattr(s, "courseId", None)
                or (s.topic.module.courseId if s.topic and s.topic.module else None),
                "topicId": getattr(s, "topicId", None),
                "moduleId": (s.topic.moduleId if s.topic else None),
                "examPrepId": getattr(s, "examPrepId", None),
                "noteId": getattr(s, "noteId", None),
            }
        )

    return {"sessions": result}


@session_router.post("/sessions", response_model=dict)
async def create_my_chat_session(
    current_user: CurrentUser,
    db: DBDep,
    courseId: str | None = Query(None),
    topicId: str | None = Query(None),
    examPrepId: str | None = Query(None),
    noteId: str | None = Query(None),
):
    """
    Create a new chat session for the current user.
    If resource IDs are provided, acts as a "get or create": returns the existing session for that resource if found.
    Otherwise, creates a generic session, reusing empty ones if possible.
    """
    course_id = courseId
    topic_id = topicId
    exam_prep_id = examPrepId
    note_id = noteId

    is_resource_scoped = any([course_id, topic_id, exam_prep_id, note_id])

    # 1. Resource-scoped session logic
    if is_resource_scoped:
        where_res = {"userId": current_user.id}
        where_res["isCircleRoom"] = False
        if course_id:
            where_res["courseId"] = course_id
        if topic_id:
            where_res["topicId"] = topic_id
        if exam_prep_id:
            where_res["examPrepId"] = exam_prep_id
        if note_id:
            where_res["noteId"] = note_id

        existing_res_session = await db.chatsession.find_first(
            where=where_res, order={"updatedAt": "desc"}
        )

        if existing_res_session:
            # Bump updatedAt to make it appear recent in history
            # and mark as active (deactivating others)
            await db.chatsession.update_many(
                where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
                data={"isActive": False},
            )
            # Fetch full session with relations to get moduleId/courseId
            session = await db.chatsession.find_unique(
                where={"id": existing_res_session.id},
                include={"topic": {"include": {"module": True}}},
            )
            return {
                "id": session.id,
                "title": session.title,
                "isActive": bool(session.isActive),
                "courseId": getattr(session, "courseId", None)
                or (
                    session.topic.module.courseId
                    if session.topic and session.topic.module
                    else None
                ),
                "topicId": getattr(session, "topicId", None),
                "moduleId": session.topic.moduleId if session.topic else None,
                "examPrepId": getattr(session, "examPrepId", None),
                "noteId": getattr(session, "noteId", None),
                "createdAt": (
                    session.createdAt.isoformat()
                    if hasattr(session.createdAt, "isoformat")
                    else str(session.createdAt)
                ),
                "updatedAt": (
                    session.updatedAt.isoformat()
                    if hasattr(session.updatedAt, "isoformat")
                    else str(session.updatedAt)
                ),
            }

        # If not found, fetch resource title for naming
        title = "Chat"
        actual_module_id = None
        if course_id:
            res = await db.course.find_unique(where={"id": course_id})
            if res:
                title = res.title
            else:
                course_id = None
        elif topic_id:
            res = await db.topic.find_unique(where={"id": topic_id}, include={"module": True})
            if res:
                title = res.title
                actual_module_id = res.moduleId
                if res.module and not course_id:
                    course_id = res.module.courseId
            else:
                topic_id = None
        elif exam_prep_id:
            res = await db.examprep.find_unique(where={"id": exam_prep_id})
            if res:
                title = res.subject
            else:
                exam_prep_id = None
        elif note_id:
            res = await db.note.find_unique(where={"id": note_id})
            if res:
                title = res.title
            else:
                note_id = None

        # Create a new resource-scoped session
        await db.chatsession.update_many(
            where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
            data={"isActive": False},
        )
        data = {
            "userId": current_user.id,
            "title": title,
            "isActive": True,
            "isCircleRoom": False,
        }
        if course_id:
            data["courseId"] = course_id
        if topic_id:
            data["topicId"] = topic_id
        if exam_prep_id:
            data["examPrepId"] = exam_prep_id
        if note_id:
            data["noteId"] = note_id

        session = await db.chatsession.create(data=data)
        return {
            "id": session.id,
            "title": session.title,
            "isActive": bool(session.isActive),
            "courseId": getattr(session, "courseId", None),
            "topicId": getattr(session, "topicId", None),
            "moduleId": actual_module_id,
            "examPrepId": getattr(session, "examPrepId", None),
            "noteId": getattr(session, "noteId", None),
            "createdAt": (
                session.createdAt.isoformat()
                if hasattr(session.createdAt, "isoformat")
                else str(session.createdAt)
            ),
            "updatedAt": (
                session.updatedAt.isoformat()
                if hasattr(session.updatedAt, "isoformat")
                else str(session.updatedAt)
            ),
        }

    # 2. Generic session logic
    # Find all generic sessions. If multiple exist (legacy), merge them JIT.
    existing_sessions = await db.chatsession.find_many(
        where={
            "userId": current_user.id,
            "isCircleRoom": False,
            "sessionType": "general",
            "courseId": None,
            "topicId": None,
            "examPrepId": None,
            "noteId": None,
        },
        order={"createdAt": "asc"},
    )

    if existing_sessions:
        master_session = existing_sessions[0]

        # JIT Migration: If multiple generic sessions exist, merge them into the master
        if len(existing_sessions) > 1:
            sessions_to_merge_from = existing_sessions[1:]
            for session in sessions_to_merge_from:
                # Move all messages to the master session
                await db.chatmessage.update_many(
                    where={"sessionId": session.id}, data={"sessionId": master_session.id}
                )
                # Delete the now-empty session
                await db.chatsession.delete(where={"id": session.id})

        # Mark it active and return it
        await db.chatsession.update_many(
            where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
            data={"isActive": False},
        )
        session = await db.chatsession.update(
            where={"id": master_session.id},
            data={"isActive": True, "title": "Chat"},
        )
        return {
            "id": session.id,
            "title": session.title,
            "isActive": bool(session.isActive),
            "createdAt": (
                session.createdAt.isoformat()
                if hasattr(session.createdAt, "isoformat")
                else str(session.createdAt)
            ),
            "updatedAt": (
                session.updatedAt.isoformat()
                if hasattr(session.updatedAt, "isoformat")
                else str(session.updatedAt)
            ),
        }

    # No generic session found - create one
    await db.chatsession.update_many(
        where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
        data={"isActive": False},
    )
    session = await db.chatsession.create(
        data={
            "userId": current_user.id,
            "title": "Chat",
            "isActive": True,
            "isCircleRoom": False,
            "sessionType": "general",
        }
    )

    return {
        "id": session.id,
        "title": session.title,
        "isActive": bool(session.isActive),
        "createdAt": (
            session.createdAt.isoformat()
            if hasattr(session.createdAt, "isoformat")
            else str(session.createdAt)
        ),
        "updatedAt": (
            session.updatedAt.isoformat()
            if hasattr(session.updatedAt, "isoformat")
            else str(session.updatedAt)
        ),
    }


@session_router.post("/sessions/{session_id}/activate", response_model=dict)
async def activate_my_chat_session(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Mark a session active (optional; WS can also be pinned per message via context.sessionId).
    """
    session = await db.chatsession.find_first(
        where={"id": session_id, "userId": current_user.id, "isCircleRoom": False}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.chatsession.update_many(
        where={"userId": current_user.id, "isActive": True, "isCircleRoom": False},
        data={"isActive": False},
    )
    session = await db.chatsession.update(where={"id": session_id}, data={"isActive": True})
    return {"id": session.id, "isActive": bool(session.isActive)}


@session_router.post("/sessions/{session_id}/studio-topic-opener", response_model=dict)
async def ensure_studio_topic_opener(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Idempotent: if the topic-scoped session has no general-thread messages yet, generate and persist
    a proactive ASSISTANT opener (Gemini text). Clients call after loading empty history so the user
    does not need to send the first message.
    """
    session = await db.chatsession.find_first(
        where={
            "id": session_id,
            "userId": current_user.id,
            "isCircleRoom": False,
            "topicId": {"not": None},
        },
        include={"topic": {"include": {"module": {"include": {"course": True}}}}},
    )
    if not session or not getattr(session, "topicId", None):
        raise HTTPException(status_code=404, detail="Session not found")

    where_thread = {
        "sessionId": session_id,
        "userId": current_user.id,
        "reviewItemId": None,
    }
    msg_count = await db.chatmessage.count(where=where_thread)
    if msg_count > 0:
        return {"created": False, "message": None}

    topic = getattr(session, "topic", None)
    topic_title = (getattr(topic, "title", None) or "This topic").strip() or "This topic"
    module_title = ""
    course_title = "this course"
    if topic and getattr(topic, "module", None):
        module_title = (getattr(topic.module, "title", None) or "").strip()
        course = getattr(topic.module, "course", None)
        if course and getattr(course, "title", None):
            course_title = str(course.title).strip() or course_title

    opener_body: str | None = None
    usage_info: dict = {"input_tokens": 0, "output_tokens": 0, "model_name": "static"}
    if gemini_api_key():
        prompt = (
            f"{STUDIO_TOPIC_OPENER_INSTRUCTION}\n\n"
            f"Course title: {course_title}\n"
            f"Module title: {module_title or '(none)'}\n"
            f"Topic title: {topic_title}\n\n"
            "Output only the assistant message body."
        )
        try:
            opener_body, usage_info = await llm_service.get_chat_response([], prompt, None)
        except Exception as e:
            logger.warning("studio-topic-opener LLM failed: %s", e, exc_info=True)
            opener_body = None

    text = (opener_body or "").strip()
    if not text:
        text = _static_studio_topic_opener(
            course_title=course_title, module_title=module_title, topic_title=topic_title
        )

    msg_count2 = await db.chatmessage.count(where=where_thread)
    if msg_count2 > 0:
        return {"created": False, "message": None}

    input_tokens = int(usage_info.get("input_tokens") or 0)
    output_tokens = int(usage_info.get("output_tokens") or 0)
    model_name = str(usage_info.get("model_name") or default_model_for(LlmTask.CHAT_DEFAULT))
    row = await db.chatmessage.create(
        data={
            "sessionId": session_id,
            "userId": current_user.id,
            "role": "ASSISTANT",
            "content": text,
            "tokenCount": input_tokens + output_tokens,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "modelName": model_name,
        }
    )
    ts = row.createdAt.isoformat() if hasattr(row.createdAt, "isoformat") else str(row.createdAt)
    return {
        "created": True,
        "message": {
            "id": row.id,
            "role": _map_db_role_to_client(str(row.role)),
            "content": row.content,
            "timestamp": ts,
            "userId": current_user.id,
            "userName": getattr(current_user, "name", None),
            "reviewItemId": None,
            "imageUrl": None,
            "imageUrls": [],
        },
    }


@session_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_chat_session(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Delete a chat session and all its messages.
    """
    session = await db.chatsession.find_first(
        where={"id": session_id, "userId": current_user.id, "isCircleRoom": False}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.chatsession.delete(where={"id": session_id})
    logger.info(f"Deleted chat session {session_id} for user {current_user.id}")


@session_router.get("/sessions/{session_id}/messages", response_model=dict)
async def get_my_chat_messages(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
    reviewItemId: str | None = Query(default=None),
    take: int = Query(20, ge=1, le=500),
    skip: int = Query(0, ge=0),
):
    """
    Fetch messages for a given session.

    If `reviewItemId` is provided, returns only that review thread.
    If `reviewItemId` is omitted, returns only general chat messages (reviewItemId is NULL).
    """
    session = await db.chatsession.find_unique(
        where={"id": session_id},
        include={
            "circleChatGroup": {
                "include": {
                    "circle": {
                        "include": {
                            "members": True,
                        }
                    }
                }
            }
        },
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    circle_group = getattr(session, "circleChatGroup", None)
    is_circle_session = bool(circle_group)

    if is_circle_session:
        if not _is_circle_member(circle_group, current_user.id):
            raise HTTPException(status_code=403, detail="You are not a member of this circle.")
    elif session.userId != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    where = {"sessionId": session_id}
    if not is_circle_session:
        where["userId"] = current_user.id
    if reviewItemId:
        where["reviewItemId"] = reviewItemId
    else:
        where["reviewItemId"] = None

    is_resource_scoped = any(
        [
            getattr(session, "courseId", None),
            getattr(session, "topicId", None),
            getattr(session, "examPrepId", None),
            getattr(session, "noteId", None),
        ]
    )

    # For non-onboarded users fetching general chat: if session is empty, seed welcome message.
    # This is the single source of truth - handles createSession reuse, stored session, etc.
    # Onboarding messages go into the dedicated onboarding session.
    if (
        not is_circle_session
        and not reviewItemId
        and not is_resource_scoped
        and not getattr(current_user, "isOnboarded", False)
    ):
        # Check if this is the onboarding session or general session
        session_type = getattr(session, "sessionType", "general")
        if session_type == "onboarding":
            # Seed welcome message into the onboarding session
            msg_count = await db.chatmessage.count(
                where={"sessionId": session_id, "userId": current_user.id, "reviewItemId": None}
            )
            if msg_count == 0:
                try:
                    from src.services.onboarding_service import ensure_onboarding_initialized

                    await ensure_onboarding_initialized(db, current_user.id)
                    # Re-check count before insert to reduce race
                    msg_count = await db.chatmessage.count(
                        where={
                            "sessionId": session_id,
                            "userId": current_user.id,
                            "reviewItemId": None,
                        }
                    )
                    if msg_count == 0:
                        await db.chatmessage.create(
                            data={
                                "sessionId": session_id,
                                "userId": current_user.id,
                                "role": "ASSISTANT",
                                "content": (
                                    "Welcome! I'm Maigie.\n\n"
                                    "Before we start: are you a **university student** or a **self‑paced learner**?\n"
                                    "Reply with `university` or `self-paced`."
                                ),
                            }
                        )
                except Exception as e:
                    logger.warning("Failed to seed onboarding message in get_messages: %s", e)

    # Get latest `take` messages with optional skip for pagination
    records = await db.chatmessage.find_many(
        where=where,
        order={"createdAt": "desc"},
        take=take,
        skip=skip,
        include={"user": True, "replyToMessage": {"include": {"user": True}}},
    )
    records = list(reversed(records))

    messages = []
    for m in records:
        msg = {
            "id": m.id,
            "role": _map_db_role_to_client(str(m.role)),
            "content": m.content,
            "reviewItemId": getattr(m, "reviewItemId", None),
            "timestamp": (
                m.createdAt.isoformat() if hasattr(m.createdAt, "isoformat") else str(m.createdAt)
            ),
            "userId": getattr(m, "userId", None),
            "userName": (m.user.name if getattr(m, "user", None) else None),
            "imageUrl": getattr(m, "imageUrl", None),
            "imageUrls": getattr(m, "imageUrls", None) or [],
            "replyToMessageId": getattr(m, "replyToMessageId", None),
        }
        reply_to_message = getattr(m, "replyToMessage", None)
        if reply_to_message is not None:
            msg["replyToMessage"] = _serialize_reply_preview(reply_to_message)
        component_data = getattr(m, "componentData", None)
        if component_data is not None:
            msg["componentData"] = component_data
        suggestion_text = getattr(m, "suggestionText", None)
        if suggestion_text:
            msg["suggestionText"] = suggestion_text
        messages.append(msg)

    return {"sessionId": session_id, "messages": messages}
