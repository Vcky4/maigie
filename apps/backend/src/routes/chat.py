"""
Chat Routes & WebSocket Endpoint.
Handles real-time messaging with Gemini AI and Action Execution.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, UTC

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,  # <--- Added
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from jose import JWTError, jwt

from prisma import Json, Prisma
from src.core.cache import cache
from src.core.celery_app import celery_app
from src.config import settings
from src.services.action_service import action_service
from src.services.component_response_service import (
    format_action_component_response,
    format_list_component_response,
)
from src.services.credit_service import (
    CREDIT_COSTS,
    check_credit_availability,
    consume_credits,
    get_credit_usage,
)
from src.services.llm_service import llm_service
from src.services.rag_service import rag_service
from src.services.socket_manager import manager
from src.services.storage_service import storage_service  # <--- Added
from src.services.usage_tracking_service import increment_feature_usage
from src.services.voice_service import voice_service
from src.utils.exceptions import SubscriptionLimitError
from src.dependencies import CurrentUser, DBDep

router = APIRouter()
db = Prisma()
logger = logging.getLogger(__name__)


def _extract_suggestion(text: str) -> tuple[str, str | None]:
    """
    Extract suggestive follow-up (e.g. "Would you like me to...") from AI response.
    Returns (main_content, suggestion_text). Suggestion is displayed after components.
    """
    if not text or not text.strip():
        return (text, None)
    text = text.strip()
    # Phrases that start the suggestion part - find first occurrence
    suggestion_phrases = [
        "How does that look?",
        "How does that look",
        "All of these are now",
        "Would you like me to",
        "Would you like to",
        "Should I ",
        "Or should we ",
    ]
    idx = -1
    for phrase in suggestion_phrases:
        pos = text.lower().find(phrase.lower())
        if pos >= 0 and (idx < 0 or pos < idx):
            idx = pos
    if idx < 0:
        return (text, None)
    # Walk back to the start of the paragraph (double newline or start)
    para_start = text.rfind("\n\n", 0, idx)
    if para_start >= 0:
        split_at = para_start
    else:
        split_at = idx
    main_content = text[:split_at].strip()
    suggestion_text = text[split_at:].strip()
    if main_content and suggestion_text and len(suggestion_text) > 15:
        return (main_content, suggestion_text)
    return (text, None)


def _map_db_role_to_client(role: str) -> str:
    if role == "USER":
        return "user"
    if role == "ASSISTANT":
        return "assistant"
    return "system"


@router.get("/sessions", response_model=dict)
async def list_my_chat_sessions(
    current_user: CurrentUser,
    db: DBDep,
    take: int = Query(20, ge=1, le=100),
):
    """
    List the current user's chat sessions (for conversation history UI).
    """
    sessions = await db.chatsession.find_many(
        where={"userId": current_user.id},
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
            }
        )

    return {"sessions": result}


@router.post("/sessions", response_model=dict)
async def create_my_chat_session(current_user: CurrentUser, db: DBDep):
    """
    Create a new chat session for the current user.
    Only creates a new session if there is no existing session with no messages.
    """
    # Check for existing empty session (no messages) - reuse it instead of creating new
    existing_sessions = await db.chatsession.find_many(
        where={"userId": current_user.id},
        order={"updatedAt": "desc"},
        take=50,
    )
    for s in existing_sessions:
        msg_count = await db.chatmessage.count(where={"sessionId": s.id, "userId": current_user.id})
        if msg_count == 0:
            # Found empty session - mark it active and return it
            await db.chatsession.update_many(
                where={"userId": current_user.id, "isActive": True},
                data={"isActive": False},
            )
            session = await db.chatsession.update(
                where={"id": s.id},
                data={"isActive": True, "title": "New Chat", "updatedAt": datetime.now(UTC)},
            )
            # Seeding happens in get_messages only; avoids race when multiple createSession calls.
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

    # No empty session found - create new one
    await db.chatsession.update_many(
        where={"userId": current_user.id, "isActive": True},
        data={"isActive": False},
    )
    session = await db.chatsession.create(
        data={"userId": current_user.id, "title": "New Chat", "isActive": True}
    )

    # Seeding happens in get_messages only; avoids race when multiple createSession calls.
    if False:  # Removed seeding - now only in get_messages
        if False and not getattr(current_user, "isOnboarded", False):
            from src.services.onboarding_service import ensure_onboarding_initialized

            await ensure_onboarding_initialized(db, current_user.id)
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": current_user.id,
                    "role": "ASSISTANT",
                    "content": (
                        "Welcome! I’m Maigie.\n\n"
                        "Before we start: are you a **university student** or a **self‑paced learner**?\n"
                        "Reply with `university` or `self-paced`."
                    ),
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


@router.post("/sessions/{session_id}/activate", response_model=dict)
async def activate_my_chat_session(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Mark a session active (optional; WS can also be pinned per message via context.sessionId).
    """
    session = await db.chatsession.find_first(where={"id": session_id, "userId": current_user.id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.chatsession.update_many(
        where={"userId": current_user.id, "isActive": True},
        data={"isActive": False},
    )
    session = await db.chatsession.update(where={"id": session_id}, data={"isActive": True})
    return {"id": session.id, "isActive": bool(session.isActive)}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_chat_session(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Delete a chat session and all its messages.
    """
    session = await db.chatsession.find_first(where={"id": session_id, "userId": current_user.id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.chatsession.delete(where={"id": session_id})
    logger.info(f"Deleted chat session {session_id} for user {current_user.id}")


@router.get("/sessions/{session_id}/messages", response_model=dict)
async def get_my_chat_messages(
    session_id: str,
    current_user: CurrentUser,
    db: DBDep,
    reviewItemId: str | None = Query(default=None),
    take: int = Query(200, ge=1, le=500),
):
    """
    Fetch messages for a given session.

    If `reviewItemId` is provided, returns only that review thread.
    If `reviewItemId` is omitted, returns only general chat messages (reviewItemId is NULL).
    """
    session = await db.chatsession.find_first(where={"id": session_id, "userId": current_user.id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    where = {"sessionId": session_id, "userId": current_user.id}
    if reviewItemId:
        where["reviewItemId"] = reviewItemId
    else:
        where["reviewItemId"] = None

    # For non-onboarded users fetching general chat: if session is empty, seed welcome message.
    # This is the single source of truth - handles createSession reuse, stored session, etc.
    if not reviewItemId and not getattr(current_user, "isOnboarded", False):
        msg_count = await db.chatmessage.count(
            where={"sessionId": session_id, "userId": current_user.id, "reviewItemId": None}
        )
        if msg_count == 0:
            try:
                from src.services.onboarding_service import ensure_onboarding_initialized

                await ensure_onboarding_initialized(db, current_user.id)
                # Re-check count before insert to reduce race (another request may have just seeded)
                msg_count = await db.chatmessage.count(
                    where={"sessionId": session_id, "userId": current_user.id, "reviewItemId": None}
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

    # Get latest `take` messages then return in chronological order
    records = await db.chatmessage.find_many(
        where=where,
        order={"createdAt": "desc"},
        take=take,
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
            "imageUrl": getattr(m, "imageUrl", None),
            "imageUrls": getattr(m, "imageUrls", None) or [],
        }
        component_data = getattr(m, "componentData", None)
        if component_data is not None:
            msg["componentData"] = component_data
        suggestion_text = getattr(m, "suggestionText", None)
        if suggestion_text:
            msg["suggestionText"] = suggestion_text
        messages.append(msg)

    return {"sessionId": session_id, "messages": messages}


def _extract_course_request(user_text: str) -> tuple[str, str]:
    """
    Best-effort extraction of (topic, difficulty) from a free-form message.
    Kept intentionally cheap to avoid extra LLM calls before replying.
    """
    text = (user_text or "").strip()
    lower = text.lower()

    # Difficulty
    difficulty = "BEGINNER"
    if "intermediate" in lower:
        difficulty = "INTERMEDIATE"
    elif "advanced" in lower:
        difficulty = "ADVANCED"
    elif "expert" in lower:
        difficulty = "EXPERT"

    # Topic patterns
    patterns = [
        r"(?:create|make|build|generate)\s+(?:me\s+)?(?:a\s+)?course\s+(?:about|on)\s+(?P<topic>.+)",
        r"(?:i\s+want\s+to\s+learn|i\s+would\s+like\s+to\s+learn|help\s+me\s+learn)\s+(?P<topic>.+)",
        r"(?:i\s+want\s+to\s+study|help\s+me\s+study)\s+(?P<topic>.+)",
        r"(?:course\s+on)\s+(?P<topic>.+)",
    ]
    topic = ""
    for pat in patterns:
        m = re.search(pat, lower, re.IGNORECASE)
        if m:
            topic = (m.group("topic") or "").strip()
            break

    # Clean topic
    if topic:
        topic = re.split(r"[.?!]", topic)[0].strip()
        topic = re.sub(r"\b(for|please|thanks|thank you)\b", "", topic, flags=re.IGNORECASE).strip()

    if not topic:
        # Fallback: use first ~8 words from user text
        words = re.findall(r"[A-Za-z0-9#+\-]+", text)
        topic = " ".join(words[:8]).strip()

    return topic or "a new topic", difficulty


def _looks_like_course_generation_intent(user_text: str) -> bool:
    lower = (user_text or "").lower()
    if not lower.strip():
        return False

    # Exclude obvious non-generation queries
    if any(x in lower for x in ["what courses", "my courses", "list courses", "show my courses"]):
        return False

    triggers = [
        "create a course",
        "make a course",
        "generate a course",
        "build a course",
        "course on",
        "course about",
        "i want to learn",
        "help me learn",
        "i want to study",
        "help me study",
    ]
    return any(t in lower for t in triggers)


async def enrich_action_data(
    action_type: str,
    action_data: dict,
    enriched_context: dict = None,
    context: dict = None,
    created_ids: dict = None,
):
    """
    Enrich action data with context and resolve dependencies from previous actions.

    Args:
        action_type: Type of action (e.g., "create_course", "create_goal")
        action_data: The action data dictionary
        enriched_context: Enriched context from current page/view
        context: Original context from frontend
        created_ids: Dictionary of IDs created by previous actions in the batch
                     (e.g., {"courseId": "c123", "goalId": "c456"})

    Returns:
        Enriched action_data dictionary
    """
    # Resolve dependency placeholders ($courseId, $goalId, etc.)
    if created_ids:
        for key, value in action_data.items():
            if isinstance(value, str) and value.startswith("$"):
                placeholder = value[1:]  # Remove the $
                if placeholder in created_ids:
                    action_data[key] = created_ids[placeholder]
                    print(f"🔄 Resolved {value} to {created_ids[placeholder]}")

    # Handle note-related actions
    if action_type in ["retake_note", "add_summary", "add_tags"]:
        note_id = action_data.get("noteId")
        print(f"🔍 AI provided noteId: {note_id}")

        if enriched_context and enriched_context.get("noteId"):
            ai_note_id = action_data.get("noteId")
            enriched_topic_id = enriched_context.get("topicId")
            enriched_note_id = enriched_context.get("noteId")

            if ai_note_id == enriched_topic_id:
                print("⚠️ AI confused topicId with noteId. Using actual noteId from context.")
                note_id = enriched_note_id
            elif ai_note_id != enriched_note_id:
                print(
                    f"⚠️ AI provided noteId '{ai_note_id}' but context has '{enriched_note_id}'. Using context noteId."
                )
                note_id = enriched_note_id
            else:
                note_id = enriched_note_id
            print(f"📝 Using noteId from enriched_context: {note_id}")
        elif context and context.get("noteId"):
            note_id = context["noteId"]
            print(f"📝 Using noteId from original context: {note_id}")
        elif not note_id:
            if enriched_context and enriched_context.get("topicId"):
                topic_id = enriched_context["topicId"]
                print(f"🔍 No noteId found, checking topicId: {topic_id}")
                topic = await db.topic.find_unique(
                    where={"id": topic_id},
                    include={"note": True},
                )
                if topic and topic.note:
                    note_id = topic.note.id
                    print(f"✅ Found note from topic: {note_id}")
            elif context and context.get("topicId"):
                topic_id = context["topicId"]
                print(f"🔍 No noteId found, checking topicId from context: {topic_id}")
                topic = await db.topic.find_unique(
                    where={"id": topic_id},
                    include={"note": True},
                )
                if topic and topic.note:
                    note_id = topic.note.id
                    print(f"✅ Found note from topic: {note_id}")

        if note_id:
            print(f"🔍 Verifying noteId: {note_id}")
            note = await db.note.find_unique(where={"id": note_id})
            if not note:
                print(f"⚠️ Note with ID {note_id} not found, checking if it's a topicId...")
                topic = await db.topic.find_unique(
                    where={"id": note_id},
                    include={"note": True},
                )
                if topic:
                    if topic.note:
                        note_id = topic.note.id
                        print(f"✅ Resolved topicId to noteId: {note_id}")
                    else:
                        print(
                            f"⚠️ Topic '{topic.title}' exists but has no note. Cannot retake/summarize."
                        )
                        note_id = None
                else:
                    print(f"⚠️ ID {note_id} is neither a note nor a topic")
                    note_id = None

        if note_id:
            action_data["noteId"] = note_id
            print(f"✅ Final noteId set in action_data: {note_id}")
        else:
            print("⚠️ No noteId found in context for retake_note/add_summary action")

    # Handle create_note action
    if action_type == "create_note":
        action_topic_id = action_data.get("topicId")
        is_likely_title = action_topic_id and (
            len(action_topic_id) > 30
            or " " in action_topic_id
            or not action_topic_id.startswith("c")
        )

        if enriched_context and enriched_context.get("topicId"):
            action_data["topicId"] = enriched_context["topicId"]
            print(f"📝 Set topicId from enriched_context: {enriched_context['topicId']}")
        elif context and context.get("topicId"):
            action_data["topicId"] = context["topicId"]
            print(f"📝 Set topicId from original context: {context['topicId']}")
        elif is_likely_title:
            print(
                f"⚠️ AI provided topicId that looks like a title: {action_topic_id}, but no context available"
            )

        action_course_id = action_data.get("courseId")
        is_course_placeholder = action_course_id and (
            "course_id_from_context" in action_course_id.lower()
            or "placeholder" in action_course_id.lower()
        )
        is_course_likely_title = action_course_id and (
            len(action_course_id) > 30
            or " " in action_course_id
            or not action_course_id.startswith("c")
        )

        if is_course_placeholder or is_course_likely_title or not action_course_id:
            if enriched_context and enriched_context.get("courseId"):
                action_data["courseId"] = enriched_context["courseId"]
                print(f"📝 Set courseId from enriched_context: {enriched_context['courseId']}")
            elif context and context.get("courseId"):
                action_data["courseId"] = context["courseId"]
                print(f"📝 Set courseId from original context: {context['courseId']}")
            elif is_course_placeholder or is_course_likely_title:
                print(f"⚠️ AI provided invalid courseId: {action_course_id}, removing it")
                action_data.pop("courseId", None)

    # Handle create_goal action
    if action_type == "create_goal":
        action_course_id = action_data.get("courseId")
        is_course_placeholder = action_course_id and (
            "placeholder" in action_course_id.lower()
            or len(action_course_id) > 30
            or " " in action_course_id
            or not action_course_id.startswith("c")
        )

        if is_course_placeholder or not action_course_id:
            if enriched_context and enriched_context.get("courseId"):
                action_data["courseId"] = enriched_context["courseId"]
                print(
                    f"📝 Set courseId from enriched_context for goal: {enriched_context['courseId']}"
                )
            elif context and context.get("courseId"):
                action_data["courseId"] = context["courseId"]
                print(f"📝 Set courseId from original context for goal: {context['courseId']}")
            elif is_course_placeholder:
                print(f"⚠️ Removing invalid courseId placeholder: {action_course_id}")
                action_data.pop("courseId", None)

        action_topic_id = action_data.get("topicId")
        is_topic_placeholder = action_topic_id and (
            "placeholder" in action_topic_id.lower()
            or len(action_topic_id) > 30
            or " " in action_topic_id
            or not action_topic_id.startswith("c")
        )

        if is_topic_placeholder or not action_topic_id:
            if enriched_context and enriched_context.get("topicId"):
                action_data["topicId"] = enriched_context["topicId"]
                print(
                    f"📝 Set topicId from enriched_context for goal: {enriched_context['topicId']}"
                )
            elif context and context.get("topicId"):
                action_data["topicId"] = context["topicId"]
                print(f"📝 Set topicId from original context for goal: {context['topicId']}")
            elif is_topic_placeholder:
                print(f"⚠️ Removing invalid topicId placeholder: {action_topic_id}")
                action_data.pop("topicId", None)

    # Handle recommend_resources action
    if action_type == "recommend_resources":
        if enriched_context and enriched_context.get("topicId"):
            action_data["topicId"] = enriched_context["topicId"]
            print(
                f"📝 Set topicId from enriched_context for resource recommendation: {enriched_context['topicId']}"
            )
        elif context and context.get("topicId"):
            action_data["topicId"] = context["topicId"]
            print(
                f"📝 Set topicId from original context for resource recommendation: {context['topicId']}"
            )

        if enriched_context and enriched_context.get("courseId"):
            action_data["courseId"] = enriched_context["courseId"]
            print(
                f"📝 Set courseId from enriched_context for resource recommendation: {enriched_context['courseId']}"
            )
        elif context and context.get("courseId"):
            action_data["courseId"] = context["courseId"]
            print(
                f"📝 Set courseId from original context for resource recommendation: {context['courseId']}"
            )

    # Handle create_schedule action
    if action_type == "create_schedule":
        # Similar to create_goal, enrich courseId, topicId, goalId from context
        for id_field in ["courseId", "topicId", "goalId"]:
            action_id = action_data.get(id_field)
            is_placeholder = action_id and (
                "placeholder" in action_id.lower()
                or len(action_id) > 30
                or " " in action_id
                or (not action_id.startswith("c") and not action_id.startswith("$"))
            )

            if (is_placeholder or not action_id) and not (action_id and action_id.startswith("$")):
                if enriched_context and enriched_context.get(id_field):
                    action_data[id_field] = enriched_context[id_field]
                    print(
                        f"📝 Set {id_field} from enriched_context for schedule: {enriched_context[id_field]}"
                    )
                elif context and context.get(id_field):
                    action_data[id_field] = context[id_field]
                    print(
                        f"📝 Set {id_field} from original context for schedule: {context[id_field]}"
                    )
                elif is_placeholder:
                    print(f"⚠️ Removing invalid {id_field} placeholder: {action_id}")
                    action_data.pop(id_field, None)

    # Handle complete_review action (review_item_id from context)
    if action_type == "complete_review":
        if not action_data.get("review_item_id"):
            if enriched_context and enriched_context.get("reviewItemId"):
                action_data["review_item_id"] = enriched_context["reviewItemId"]
            elif context and context.get("reviewItemId"):
                action_data["review_item_id"] = context["reviewItemId"]

    return action_data


async def get_current_user_ws(token: str = Query(...)):
    """
    Authenticate WebSocket connection via query param.
    ws://localhost:8001/api/v1/chat/ws?token=eyJ...
    """
    # Ensure DB is connected (WebSockets can sometimes race the main app startup)
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


async def _build_greeting_context(db_client, user) -> dict:
    """Fetch user data for a personalized AI greeting on new chat."""
    now = datetime.now(UTC)
    ctx: dict = {
        "name": getattr(user, "name", "") or "",
        "current_time": now.strftime("%A, %B %d, %Y at %H:%M UTC"),
    }

    # Recent courses with progress (summary for LLM + full payload for components)
    try:
        courses = await db_client.course.find_many(
            where={"userId": user.id, "archived": False},
            order={"updatedAt": "desc"},
            take=5,
            include={"modules": {"include": {"topics": True}}},
        )
        ctx["courses"] = []
        ctx["courses_for_component"] = []
        for c in courses:
            total = sum(len(m.topics) for m in c.modules)
            completed = sum(1 for m in c.modules for t in m.topics if t.completed)
            progress = round((completed / total * 100) if total > 0 else 0)
            ctx["courses"].append(
                {
                    "title": c.title,
                    "progress": progress,
                    "totalTopics": total,
                    "completedTopics": completed,
                }
            )
            card = {
                "courseId": c.id,
                "id": c.id,
                "title": c.title,
                "description": (c.description or "")[:500],
                "progress": float(progress),
                "difficulty": getattr(c, "difficulty", None),
                "completedTopics": completed,
                "totalTopics": total,
            }
            # First incomplete topic for "pick up where you left off"
            next_topic = None
            if total > 0 and completed < total:
                for mod in c.modules or []:
                    for t in mod.topics or []:
                        if not getattr(t, "completed", False):
                            next_topic = {
                                "topicId": t.id,
                                "topicTitle": getattr(t, "title", "Topic"),
                                "moduleId": mod.id,
                                "moduleTitle": getattr(mod, "title", "Module"),
                            }
                            break
                    if next_topic:
                        break
            if next_topic:
                card["nextTopic"] = next_topic
            ctx["courses_for_component"].append(card)
    except Exception:
        ctx["courses"] = []
        ctx["courses_for_component"] = []

    # Active goals (summary + full for component)
    try:
        goals = await db_client.goal.find_many(
            where={"userId": user.id, "status": "ACTIVE"},
            take=5,
        )
        ctx["goals"] = [
            {
                "title": g.title,
                "progress": g.progress or 0,
                "targetDate": (g.targetDate.isoformat() if g.targetDate else None),
            }
            for g in goals
        ]
        ctx["goals_for_component"] = [
            {
                "goalId": g.id,
                "id": g.id,
                "title": g.title,
                "description": g.description or "",
                "targetDate": g.targetDate.isoformat() if g.targetDate else None,
                "progress": g.progress or 0,
                "status": g.status,
                "courseId": g.courseId,
                "topicId": g.topicId,
            }
            for g in goals
        ]
    except Exception:
        ctx["goals"] = []
        ctx["goals_for_component"] = []

    # Upcoming schedules (next 3 days) – summary + full for component
    try:
        schedules = await db_client.scheduleblock.find_many(
            where={
                "userId": user.id,
                "startAt": {"gte": now, "lte": now + timedelta(days=3)},
            },
            order={"startAt": "asc"},
            take=5,
        )
        ctx["schedules"] = [{"title": s.title, "startAt": s.startAt.isoformat()} for s in schedules]
        ctx["schedules_for_component"] = [
            {
                "scheduleId": s.id,
                "id": s.id,
                "title": s.title,
                "startAt": s.startAt.isoformat() if s.startAt else "",
                "endAt": s.endAt.isoformat() if s.endAt else "",
                "description": s.description or "",
                "courseId": getattr(s, "courseId", None),
                "topicId": getattr(s, "topicId", None),
                "goalId": getattr(s, "goalId", None),
                "reviewItemId": getattr(s, "reviewItemId", None),
            }
            for s in schedules
        ]
    except Exception:
        ctx["schedules"] = []
        ctx["schedules_for_component"] = []

    # Study streak
    try:
        streak = await db_client.userstreak.find_unique(where={"userId": user.id})
        ctx["streak"] = streak.currentStreak if streak else 0
    except Exception:
        ctx["streak"] = 0

    # Pending spaced-repetition reviews
    try:
        pending = await db_client.reviewitem.find_many(
            where={"userId": user.id, "nextReviewAt": {"lte": now}},
            order={"nextReviewAt": "asc"},
            include={"course": True, "topic": True},
            take=3,
        )
        ctx["pendingReviews"] = await db_client.reviewitem.count(
            where={"userId": user.id, "nextReviewAt": {"lte": now}}
        )
        ctx["reviews_for_component"] = [
            {
                "id": r.id,
                "reviewItemId": r.id,
                "topicId": r.topicId,
                "topicTitle": (
                    r.topic.title
                    if getattr(r, "topic", None)
                    else getattr(r, "topicTitle", "Topic")
                ),
                "courseId": r.courseId,
                "courseTitle": (
                    r.course.title
                    if getattr(r, "course", None)
                    else getattr(r, "courseTitle", "Course")
                ),
                "nextReviewAt": r.nextReviewAt.isoformat(),
                "strength": getattr(r, "strength", "moderate"),
            }
            for r in pending
        ]
    except Exception:
        ctx["pendingReviews"] = 0
        ctx["reviews_for_component"] = []

    return ctx


def _build_greeting_prompt(context: dict) -> str:
    """Build a personalized greeting prompt for the LLM."""
    name = context.get("name", "").split()[0] if context.get("name") else "there"
    current_time = context.get("current_time", "")

    parts = [
        f"Current Date & Time: {current_time}",
        f"User's first name: {name}",
    ]

    courses = context.get("courses", [])
    if courses:
        lines = [
            f"  - {c['title']}: {c['progress']}% complete "
            f"({c['completedTopics']}/{c['totalTopics']} topics)"
            for c in courses
        ]
        parts.append("User's courses:\n" + "\n".join(lines))
    else:
        parts.append("User has no courses yet.")

    goals = context.get("goals", [])
    if goals:
        lines = [
            f"  - {g['title']}: {g['progress']}% progress"
            + (f" (target: {g['targetDate']})" if g.get("targetDate") else "")
            for g in goals
        ]
        parts.append("Active goals:\n" + "\n".join(lines))

    schedules = context.get("schedules", [])
    if schedules:
        lines = [f"  - {s['title']} at {s['startAt']}" for s in schedules]
        parts.append("Upcoming schedule (next 3 days):\n" + "\n".join(lines))

    streak = context.get("streak", 0)
    if streak > 0:
        parts.append(f"Current study streak: {streak} days")

    pending_reviews = context.get("pendingReviews", 0)
    if pending_reviews > 0:
        parts.append(f"Pending spaced repetition reviews: {pending_reviews}")

    data_section = "\n".join(parts)

    return (
        "You are starting a new conversation with the user. Generate a warm, "
        "hyper-contextual, encouraging, and highly dynamic greeting as their study companion Maigie.\n\n"
        f"User Context:\n{data_section}\n\n"
        "Guidelines:\n"
        "- Keep it concise (2-4 sentences max)\n"
        "- Address the user by their first name\n"
        "- Celebrate any recent achievements (streaks, finished topics)\n"
        "- Offer a brief, powerful piece of encouragement (e.g. 'You showed up today, that matters', or 'Glad to see you!')\n"
        "- Reference specific things they're working on if available\n"
        "- Suggest ONE specific thing they could do next (continue a course, "
        "work on a goal, check their schedule, do their reviews, etc.)\n"
        "- Be encouraging but natural, not over-the-top\n"
        "- Consider the time of day (morning/afternoon/evening) for appropriate greetings\n"
        "- If they have a study streak going, briefly mention it to motivate them\n"
        "- If they have pending reviews, suggest they tackle those\n"
        "- If they have upcoming schedules, give them a heads up\n"
        "- If they have no courses/goals yet, encourage them to encourage them to create their first course\n"
        "- Vary your style — don't always structure the greeting the same way\n"
        "- Do NOT use any tools — just respond with the greeting text directly\n"
    )


def _build_greeting_components(greeting_ctx: dict) -> list[dict]:
    """
    Build at most **1** focused component payload for the greeting message.
    Keeps the first impression lean and relevant to what the AI text says.
    Priority: Reviews > Next session > Course pick-up > Goals (capped at 2)
    """
    from src.services.component_response_service import (
        format_component_response,
        format_list_component_response,
    )

    reviews = greeting_ctx.get("reviews_for_component") or []
    schedules = greeting_ctx.get("schedules_for_component") or []
    courses = greeting_ctx.get("courses_for_component") or []
    goals = greeting_ctx.get("goals_for_component") or []

    # 1) Pending Reviews — most time-sensitive; cap at 3
    if reviews:
        return [
            format_component_response(
                "ReviewListMessage",
                {"reviews": reviews[:3]},
                text=None,
            )
        ]

    # 2) The next upcoming session (not the whole calendar)
    if schedules:
        next_session = schedules[0]
        return [
            format_component_response(
                "ScheduleBlockMessage",
                next_session,
                text=None,
            )
        ]

    # 3) "Pick up where you left off" — a single course card
    if courses:
        pick_up = next((c for c in courses if c.get("nextTopic")), None)
        if pick_up:
            return [
                format_component_response(
                    "CourseCardMessage",
                    pick_up,
                    text=None,
                )
            ]

    # 4) Active goals — keep at most 2 to avoid visual overload
    if goals:
        return [
            format_list_component_response(
                "GoalListMessage",
                goals[:2],
                text=None,
            )
        ]

    return []


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: dict = Depends(get_current_user_ws)):
    """
    Main WebSocket endpoint for AI Chat.
    """
    # 1. Connect
    await manager.connect(websocket, user.id)

    # 2. Find or Create an active Chat Session
    # NOTE: The frontend can optionally pass `context.sessionId` per message to pin a conversation.
    session = await db.chatsession.find_first(
        where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
    )

    if not session:
        session = await db.chatsession.create(data={"userId": user.id, "title": "New Chat"})

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

            try:
                # Try to parse as JSON
                message_data = json.loads(raw_message)
                if isinstance(message_data, dict):
                    if message_data.get("type") == "ping":
                        await manager.send_json({"type": "pong"}, user.id)
                        continue
                    user_text = message_data.get("message", raw_message)
                    context = message_data.get("context")
                    if context:
                        print(f"📥 Received context from frontend: {context}")
            except (json.JSONDecodeError, AttributeError):
                # If not JSON, treat as plain text
                pass

            # 3.1 If client pins a sessionId, switch to it (per-message)
            if context and context.get("sessionId"):
                requested_session_id = context.get("sessionId")
                try:
                    pinned = await db.chatsession.find_first(
                        where={"id": requested_session_id, "userId": user.id}
                    )
                    if pinned:
                        session = pinned
                except Exception:
                    # If anything goes wrong, fall back to the current session
                    pass

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
            if is_onboarded and not (context and context.get("reviewItemId")):
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
            if user_text == "__greeting__":
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

                        response_text, usage_info, _, _ = (
                            await llm_service.get_chat_response_with_tools(
                                history=[],
                                user_message=greeting_prompt,
                                context=None,
                                user_id=user.id,
                                user_name=getattr(user, "name", None),
                                stream_callback=stream_greeting,
                            )
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
                            model_name = usage_info.get("model_name", "gemini-3-flash-preview")
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
                        fallback = f"Hey {first_name}! 👋 What would you like to " "work on today?"
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

            user_message = await db.chatmessage.create(data=user_message_data)

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
            if (not is_onboarded or needs_retro_onboarding) and not (
                context and context.get("reviewItemId")
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
                        image_url=file_urls,
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

            # 5. Build History for Context (latest messages to reduce token usage)
            # IMPORTANT: Use the *most recent* messages; ordering asc with take would grab the oldest.
            history_take = 12
            history_where = {"sessionId": session.id}
            # Keep review conversations isolated from general chat (and from other reviews)
            if context and context.get("reviewItemId"):
                history_where["reviewItemId"] = context["reviewItemId"]
            else:
                history_where["reviewItemId"] = None
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
                                enriched_context["courseTitle"] = review.topic.module.course.title
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
                                include={
                                    "note": True,
                                    "module": {"include": {"course": True}},
                                },
                            )
                            if topic and topic.note:
                                # It's a topicId, use the topic's note
                                print(
                                    f"✅ Found topic with ID {note_id}, using its note ID: {topic.note.id}"
                                )
                                note = topic.note
                                # Also include topic details
                                enriched_context["topicId"] = topic.id
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
                                # Update noteId in enriched_context to the actual note ID
                                enriched_context["noteId"] = note.id

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
                                        enriched_context["courseId"] = note.topic.module.course.id
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
                        else:
                            # Topic not found - log for debugging but keep topicId in context
                            print(f"⚠️ Topic with ID {topic_id} not found during context enrichment")
                            print(
                                "⚠️ This topicId will still be passed to action service for validation"
                            )

                    # Fetch course details if courseId is provided (and not already fetched)
                    elif context.get("courseId") and not enriched_context.get("courseTitle"):
                        course = await db.course.find_unique(where={"id": context["courseId"]})
                        if course:
                            enriched_context["courseTitle"] = course.title
                            enriched_context["courseDescription"] = course.description or ""

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
                            }
                        }
                        await cache.set(cache_key, cacheable_context, expire=300)

                # Include direct content if provided (for summaries, etc.)
                if context.get("content"):
                    enriched_context["content"] = context["content"]

                # Include note content if provided directly (not via noteId)
                if context.get("noteContent") and not enriched_context.get("noteContent"):
                    enriched_context["noteContent"] = context["noteContent"]

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

            if should_run_rag:
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
                            print(f"🔍 RAG found {len(retrieved_items)} relevant items for context")

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
            try:
                from src.services.memory_service import get_memory_context

                memory_ctx = await get_memory_context(user.id, query=user_text)
                if memory_ctx:
                    if not enriched_context:
                        enriched_context = {}
                    enriched_context["memory_context"] = memory_ctx
            except Exception as e:
                print(f"⚠️ Memory context retrieval failed: {e}")

            # 6. Get AI response with tool calling support
            # Define progress callback for tool execution updates
            async def send_progress(
                progress: int, stage: str, message: str, course_id: str = None, **kwargs
            ):
                """Send progress updates to frontend via WebSocket"""
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
                        },
                    },
                    user.id,
                )

            # Define stream callback for streaming text responses
            streamed_chunks = []

            async def stream_text(chunk: str, is_final: bool):
                """Stream text chunks to frontend via WebSocket"""
                streamed_chunks.append(chunk)
                await manager.send_json(
                    {
                        "type": "stream",
                        "payload": {
                            "chunk": chunk,
                            "is_final": is_final,
                        },
                    },
                    user.id,
                )

            try:
                response_text, usage_info, executed_actions, query_results = (
                    await llm_service.get_chat_response_with_tools(
                        history=formatted_history,
                        user_message=user_text,
                        context=enriched_context,
                        user_id=user.id,
                        user_name=getattr(user, "name", None),
                        image_url=file_urls,  # Pass image URL if present
                        progress_callback=send_progress,  # Pass progress callback
                        stream_callback=stream_text,  # Pass stream callback
                    )
                )
            except Exception as e:
                logger.error(f"LLM service error: {e}", exc_info=True)
                response_text = "I'm sorry, I encountered an error. Please try again."
                usage_info = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "model_name": "gemini-3-flash-preview",
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
            user_text_lower = user_text.lower()
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
                            message = f"Here is your {query_type[:-1]}:"  # Remove 's' for singular
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

                elif action_type == "complete_review" and action_result.get("status") == "success":
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
                                "message": action_result.get("message", "Course outline updated!"),
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
                len(user_text) + len(str(enriched_context or "")) + len(str(formatted_history))
            ) // 4
            # Reserve credits for response (reduced estimate for cost savings)
            estimated_output_tokens = 500  # Reduced from 1000 for cost optimization
            estimated_total_tokens = estimated_input_tokens + estimated_output_tokens

            # Get user object for credit check
            user_obj = await db.user.find_unique(where={"id": user.id})
            if not user_obj:
                await websocket.close()
                return

            try:
                # Check if credits are available (will raise if hard cap reached)
                is_available, warning_message = await check_credit_availability(
                    user_obj, estimated_total_tokens
                )
                if not is_available:
                    credit_usage = await get_credit_usage(user_obj)

                    # Determine if it's daily or monthly limit
                    tier = str(user_obj.tier) if user_obj.tier else "FREE"
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
                    }
                    await manager.send_personal_message(json.dumps(error_data), user.id)
                    await websocket.close()
                    return
            except SubscriptionLimitError as e:
                # Get user tier for error message
                user_obj = await db.user.find_unique(where={"id": user.id})
                tier = str(user_obj.tier) if user_obj and user_obj.tier else "FREE"

                # Enhance error message with referral option
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
                }
                await manager.send_personal_message(json.dumps(error_data), user.id)
                await websocket.close()
                return

            # 11. Calculate actual token usage and consume credits
            # Use actual token counts from API
            actual_input_tokens = usage_info.get("input_tokens", 0)
            actual_output_tokens = usage_info.get("output_tokens", 0)

            # Fallback to estimation if API didn't provide token counts
            if actual_input_tokens == 0 and actual_output_tokens == 0:
                actual_input_tokens = (
                    len(user_text) + len(str(enriched_context or "")) + len(str(formatted_history))
                ) // 4
                actual_output_tokens = len(clean_response) // 4

            actual_total_tokens = actual_input_tokens + actual_output_tokens

            # Consume credits based on actual token usage
            try:
                await consume_credits(
                    user_obj, actual_total_tokens, operation="chat_message", db_client=db
                )
            except SubscriptionLimitError as e:
                # This shouldn't happen if check above worked, but handle gracefully
                print(f"Warning: Credit consumption failed: {e}")

            # Calculate costs and revenue
            from ..services.cost_calculator import calculate_ai_cost, calculate_revenue

            model_name = usage_info.get("model_name", "gemini-3-flash-preview")
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
            if all_components:
                create_data["componentData"] = Json(all_components)
            if suggestion_text:
                create_data["suggestionText"] = suggestion_text

            await db.chatmessage.create(data=create_data)

            # 13. Send to client: main content, then components, then suggestion (so UI order is correct)
            if suggestion_text:
                # Split response: send structured payload so frontend updates last message
                await manager.send_json(
                    {
                        "type": "assistant_final",
                        "content": main_content,
                        "suggestionText": suggestion_text,
                    },
                    user.id,
                )
            elif main_content:
                await manager.send_personal_message(main_content, user.id)

            # 14. Send component responses (queries and actions)
            for component_response in query_component_responses + component_responses:
                await manager.send_json(component_response, user.id)

            # 15. When split, suggestion is in assistant_final; no separate send needed

            # 16. Background fact extraction from conversation (non-blocking)
            # Only run every 5+ user messages to avoid excessive LLM calls
            try:
                user_msg_count = sum(1 for m in formatted_history if m.get("role") == "user")
                if user_msg_count >= 5 and user_msg_count % 5 == 0:
                    conversation_for_extraction = [
                        {
                            "role": m.get("role", "user"),
                            "content": m.get("parts", [""])[0] if m.get("parts") else "",
                        }
                        for m in formatted_history
                    ]
                    conversation_for_extraction.append({"role": "user", "content": user_text})
                    asyncio.create_task(
                        llm_service.extract_user_facts_from_conversation(
                            conversation_for_extraction, user.id
                        )
                    )
            except Exception as fact_err:
                logger.debug(f"Background fact extraction error (non-critical): {fact_err}")

            continue  # Skip to next message

    except WebSocketDisconnect:
        manager.disconnect(user.id)
    except Exception as e:
        print(f"WS Error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
        manager.disconnect(user.id)
        raise


@router.post("/voice")
async def handle_voice_upload(file: UploadFile = File(...), token: str = Query(...)):
    """
    Upload an audio file, transcribe it, and return the text.
    """
    # Validate User
    user = await get_current_user_ws(token)

    # Transcribe
    transcript = await voice_service.transcribe_audio(file)

    return {"text": transcript}


# 👇 ENDPOINT: Upload image(s) (for eager upload — supports multiple files)
@router.post("/image/upload", summary="Upload one or more images and return URLs")
async def upload_chat_image(
    files: list[UploadFile] = File(None),
    file: UploadFile = File(None),
    token: str = Query(...),
):
    """
    Upload one or more images to storage and return URLs.
    Accepts either `files` (multiple) or `file` (single, backward compat).
    Returns: { urls: [{url, filename}] }
    """
    # Validate user
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Collect all files (support both single and multiple)
    all_files: list[UploadFile] = []
    if files:
        all_files.extend(files)
    if file:
        all_files.append(file)

    if not all_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided.",
        )

    # Cap at 5 images per upload
    if len(all_files) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 5 images per upload.",
        )

    # Validate all image types
    for f in all_files:
        if f.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only JPEG, PNG, or WebP images are allowed. Got: {f.content_type}",
            )

    try:
        # Check file upload limit for FREE tier users (count all files)
        from src.core.database import db

        user_obj = await db.user.find_unique(where={"id": user.id})
        if user_obj:
            for _ in all_files:
                await increment_feature_usage(user_obj, "file_uploads", db_client=db)

        # Upload each file to BunnyCDN
        results = []
        for f in all_files:
            upload_result = await storage_service.upload_file(f, path="chat-images")
            results.append({"url": upload_result["url"], "filename": upload_result["filename"]})
            print(f"🔵 Image pre-uploaded: {upload_result['url']}")

        # Backward compat: if single file, also return top-level url/filename
        response = {"urls": results}
        if len(results) == 1:
            response["url"] = results[0]["url"]
            response["filename"] = results[0]["filename"]

        return response

    except SubscriptionLimitError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    except Exception as e:
        print(f"❌ Error in /chat/image/upload: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# 👇 ENDPOINT: Delete uploaded image (if user cancels)
@router.delete("/image/delete", summary="Delete an uploaded image")
async def delete_chat_image(
    url: str = Query(...),
    token: str = Query(...),
):
    """
    Delete a previously uploaded image from storage.
    Used when user removes an image before sending.
    """
    # Validate user
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        success = await storage_service.delete_file(url)
        if success:
            print(f"🗑️ Image deleted: {url}")
            return {"status": "deleted"}
        else:
            return {"status": "not_found"}

    except Exception as e:
        print(f"❌ Error in /chat/image/delete: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# 👇 LEGACY ENDPOINT: Image Analysis Chat (upload + analyze in one call)
# NOTE: Deprecated - prefer using eager upload (/image/upload) + WebSocket with fileUrls context
@router.post("/image", summary="Upload an image and get AI analysis")
async def handle_image_chat(
    file: UploadFile = File(...),
    text: str = Form(default="Here"),
    token: str = Form(...),
):
    """
    Handles multimodal chat:
    1. Uploads image to Storage (BunnyCDN)
    2. Saves USER message with image URL to DB
    3. Sends Image + Text to Gemini AI via llm_service
    4. Saves ASSISTANT response to DB
    """
    # Validate user from token
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # 1. Validate Image Type
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed.",
        )

    # Ensure DB is connected
    if not db.is_connected():
        await db.connect()

    try:
        # Find or create an active chat session
        session = await db.chatsession.find_first(
            where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
        )
        if not session:
            session = await db.chatsession.create(data={"userId": user.id, "title": "New Chat"})

        # 2. Upload to BunnyCDN
        upload_result = await storage_service.upload_file(file, path="chat-images")
        image_url = upload_result["url"]

        print(f"🔵 Image uploaded: {image_url}")

        # 3. Save User Message (with Image URL)
        user_message = await db.chatmessage.create(
            data={
                "sessionId": session.id,
                "userId": user.id,
                "role": "USER",
                "content": text,
                "imageUrl": image_url,
                "modelName": "user-upload",
            }
        )

        # 4. Get AI Analysis (Gemini Vision)
        print("🔵 Asking Gemini to analyze...")
        ai_response_text = await llm_service.analyze_image(text, image_url)

        # 5. Save AI Response
        ai_message = await db.chatmessage.create(
            data={
                "sessionId": session.id,
                "userId": user.id,
                "role": "ASSISTANT",
                "content": ai_response_text,
                "modelName": "gemini-1.5-flash",
            }
        )

        return {"status": "success", "user_message": user_message, "ai_message": ai_message}

    except Exception as e:
        print(f"❌ Error in /chat/image: {e}")
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
