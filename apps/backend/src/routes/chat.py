"""
Chat Routes & WebSocket Endpoint.
Handles real-time messaging with Gemini AI and Action Execution.
"""

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

from prisma import Prisma
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
from src.services.voice_service import voice_service
from src.utils.exceptions import SubscriptionLimitError

router = APIRouter()
db = Prisma()
logger = logging.getLogger(__name__)


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


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, user: dict = Depends(get_current_user_ws)):
    """
    Main WebSocket endpoint for AI Chat.
    """
    # 1. Connect
    await manager.connect(websocket, user.id)

    # 2. Find or Create an active Chat Session
    session = await db.chatsession.find_first(
        where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
    )

    if not session:
        session = await db.chatsession.create(data={"userId": user.id, "title": "New Chat"})

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
                    user_text = message_data.get("message", raw_message)
                    context = message_data.get("context")
                    print(f"📥 Received context from frontend: {context}")
            except (json.JSONDecodeError, AttributeError):
                # If not JSON, treat as plain text
                pass

            # 4. Extract fileUrls from context (if any)
            file_urls = context.get("fileUrls") if context else None

            # 4.1 Save User Message to DB (with imageUrl if provided)
            user_message_data = {
                "sessionId": session.id,
                "userId": user.id,
                "role": "USER",
                "content": user_text,
            }
            if file_urls:
                user_message_data["imageUrl"] = file_urls
                print(f"🖼️ Message includes image: {file_urls}")

            user_message = await db.chatmessage.create(data=user_message_data)

            # Fast-path: course generation via background worker
            # Goal: respond immediately, then generate/persist course in Celery and notify UI via events.
            if _looks_like_course_generation_intent(user_text):
                topic, difficulty = _extract_course_request(user_text)

                # Check & consume fixed credits for AI course generation
                user_obj = await db.user.find_unique(where={"id": user.id})
                if not user_obj:
                    await websocket.close()
                    return

                try:
                    await consume_credits(
                        user_obj,
                        CREDIT_COSTS["ai_course_generation"],
                        operation="ai_course_generation",
                        db_client=db,
                    )
                except SubscriptionLimitError as e:
                    await manager.send_json(
                        {
                            "type": "credit_limit_error",
                            "message": e.message,
                            "detail": e.detail,
                        },
                        user.id,
                    )
                    continue

                # Create placeholder course
                placeholder_course = await db.course.create(
                    data={
                        "userId": user.id,
                        "title": f"Learning {topic}",
                        "description": "Generating your course...",
                        "difficulty": difficulty,
                        "isAIGenerated": True,
                        "progress": 0.0,
                    }
                )

                # Send immediate acknowledgement (no LLM wait)
                ack_text = (
                    f"Got it — I’m generating a {difficulty.lower()} course on **{topic}** now. "
                    "I’ll let you know when it’s ready."
                )
                await manager.send_personal_message(ack_text, user.id)
                await db.chatmessage.create(
                    data={
                        "sessionId": session.id,
                        "userId": user.id,
                        "role": "ASSISTANT",
                        "content": ack_text,
                        "tokenCount": 0,
                    }
                )

                # Queue Celery job
                try:
                    celery_app.send_task(
                        "course.generate_from_chat",
                        kwargs={
                            "user_id": user.id,
                            "course_id": placeholder_course.id,
                            "user_message": user_text,
                            "topic": topic,
                            "difficulty": difficulty,
                        },
                        ignore_result=True,
                    )
                except Exception as e:
                    # Do not crash the websocket if Celery backend is unhealthy
                    print(f"⚠️ Failed to enqueue course generation task: {e}")
                    await manager.send_json(
                        {
                            "type": "event",
                            "payload": {
                                "status": "error",
                                "action": "ai_course_generation",
                                "course_id": placeholder_course.id,
                                "courseId": placeholder_course.id,
                                "message": "Failed to enqueue course generation. Please try again.",
                            },
                        },
                        user.id,
                    )
                    continue

                # Optional queued event (frontend mainly reacts to success)
                await manager.send_json(
                    {
                        "type": "event",
                        "payload": {
                            "status": "queued",
                            "action": "ai_course_generation",
                            "course_id": placeholder_course.id,
                            "courseId": placeholder_course.id,
                            "progress": 0,
                            "stage": "queued",
                            "message": "Course generation queued",
                        },
                    },
                    user.id,
                )

                continue

            # 5. Build History for Context (Last 6 messages to reduce token usage)
            history_records = await db.chatmessage.find_many(
                where={"sessionId": session.id}, order={"createdAt": "asc"}, take=6
            )

            # Format history for Gemini
            formatted_history = []
            for msg in history_records:
                # Map DB roles to Gemini roles ('user' or 'model')
                role = "user" if msg.role == "USER" else "model"
                formatted_history.append({"role": role, "parts": [msg.content]})

            # 5.5. Enrich context with topic/course/note details if IDs are provided
            enriched_context = None
            if context:
                enriched_context = context.copy()

                # Fetch note details if noteId is provided
                if context.get("noteId"):
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
                        print(f"⚠️ Note with ID {note_id} not found, checking if it's a topicId...")
                        topic = await db.topic.find_unique(
                            where={"id": note_id},
                            include={"note": True, "module": {"include": {"course": True}}},
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
                                    enriched_context["courseTitle"] = note.topic.module.course.title
                                    enriched_context["courseDescription"] = (
                                        note.topic.module.course.description or ""
                                    )
                        # If note is linked to a course (but not via topic)
                        elif note.course:
                            enriched_context["courseId"] = note.course.id
                            enriched_context["courseTitle"] = note.course.title
                            enriched_context["courseDescription"] = note.course.description or ""

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

                # Include direct content if provided (for summaries, etc.)
                if context.get("content"):
                    enriched_context["content"] = context["content"]

                # Include note content if provided directly (not via noteId)
                if context.get("noteContent") and not enriched_context.get("noteContent"):
                    enriched_context["noteContent"] = context["noteContent"]

            # 5.6. Perform Semantic Search (RAG) to find relevant items
            # This helps the LLM know about items the user might be referring to
            if len(user_text) > 3:  # Only search for meaningful queries
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

            # 6. Get AI response with tool calling support
            try:
                response_text, usage_info, executed_actions, query_results = (
                    await llm_service.get_chat_response_with_tools(
                        history=formatted_history,
                        user_message=user_text,
                        context=enriched_context,
                        user_id=user.id,
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
            query_component_responses = []
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
            component_responses = []
            for action_info in executed_actions:
                action_type = action_info["type"]
                action_data = action_info["data"]
                action_result = action_info["result"]

                # Log action to DB
                await db.aiactionlog.create(
                    data={
                        "messageId": user_message.id,  # Created earlier
                        "actionType": action_type,
                        "actionData": action_data,
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

                # Handle expensive actions in background (course generation, resource recommendations)
                if action_type == "create_course":
                    # Course generation is already handled by fast-path (lines 437-542)
                    # But if it comes through tool calling, queue background task
                    topic = action_data.get("title", "").replace("Learning ", "")
                    difficulty = action_data.get("difficulty", "BEGINNER")

                    # Create placeholder course
                    placeholder_course = await db.course.create(
                        data={
                            "userId": user.id,
                            "title": action_data.get("title", f"Learning {topic}"),
                            "description": action_data.get(
                                "description", "Generating your course..."
                            ),
                            "difficulty": difficulty,
                            "isAIGenerated": True,
                            "progress": 0.0,
                        }
                    )

                    # Queue Celery task
                    celery_app.send_task(
                        "course.generate_from_chat",
                        kwargs={
                            "user_id": user.id,
                            "course_id": placeholder_course.id,
                            "user_message": user_text,
                            "topic": topic,
                            "difficulty": difficulty,
                        },
                        ignore_result=True,
                    )

                    # Send component response
                    from src.services.component_response_service import (
                        format_component_response,
                    )

                    component_responses.append(
                        format_component_response(
                            component_type="CourseCreationMessage",
                            data={
                                "courseId": placeholder_course.id,
                                "status": "processing",
                            },
                            text="Generating your course...",
                        )
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

                elif action_type == "create_schedule":
                    # Queue background task for schedule creation
                    schedule_blocks = [action_data]  # Single block
                    celery_app.send_task(
                        "schedule.create_from_chat",
                        kwargs={
                            "user_id": user.id,
                            "schedule_blocks": schedule_blocks,
                        },
                        ignore_result=True,
                    )

                else:
                    # Other actions are already executed synchronously by tool handlers
                    # Format component response
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
                            f"Upgrade to Premium for more credits, or refer friends to earn bonus credits!"
                        )
                    else:
                        error_message = (
                            f"Monthly credit limit exceeded. You've used {credit_usage['credits_used']:,} "
                            f"of {credit_usage['hard_cap']:,} credits. "
                            f"Period resets: {credit_usage['period_end']}. "
                            f"Upgrade to Premium for unlimited usage, or refer friends to earn bonus credits!"
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
                    f"Upgrade to Premium for more credits, or refer friends to earn bonus credits!"
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

            # 12. Save AI Message to DB
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "ASSISTANT",
                    "content": clean_response,
                    "tokenCount": actual_total_tokens,
                    "inputTokens": actual_input_tokens,
                    "outputTokens": actual_output_tokens,
                    "modelName": model_name,
                    "costUsd": cost_usd,
                    "revenueUsd": revenue_usd,
                }
            )

            # 13. Send text response to client
            if clean_response:
                await manager.send_personal_message(clean_response, user.id)

            # 14. Send component responses (queries and actions)
            for component_response in query_component_responses + component_responses:
                # component_response already has the correct format from format_list_component_response
                # or format_action_component_response
                await manager.send_json(component_response, user.id)

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


# 👇 ENDPOINT: Upload image only (for eager upload)
@router.post("/image/upload", summary="Upload an image and return URL")
async def upload_chat_image(
    file: UploadFile = File(...),
    token: str = Query(...),
):
    """
    Upload an image to storage and return the URL.
    Used for eager upload - upload immediately when user selects image.
    """
    # Validate user
    user = await get_current_user_ws(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Validate Image Type
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed.",
        )

    try:
        # Upload to BunnyCDN
        upload_result = await storage_service.upload_file(file, path="chat-images")
        image_url = upload_result["url"]

        print(f"🔵 Image pre-uploaded: {image_url}")

        return {"url": image_url, "filename": upload_result["filename"]}

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
    text: str = Form(default="Explain this image"),
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
