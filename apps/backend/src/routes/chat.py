"""
Chat Routes & WebSocket Endpoint.
Handles real-time messaging with Gemini AI and Action Execution.
"""

import json
import re

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from jose import JWTError, jwt

from prisma import Prisma
from src.config import settings
from src.services.action_service import action_service
from src.services.credit_service import (
    check_credit_availability,
    consume_credits,
    get_credit_usage,
)
from src.services.llm_service import llm_service
from src.services.rag_service import rag_service
from src.services.socket_manager import manager
from src.services.voice_service import voice_service
from src.utils.exceptions import SubscriptionLimitError

router = APIRouter()
db = Prisma()


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
        is_course_likely_title = action_course_id and (
            len(action_course_id) > 30
            or " " in action_course_id
            or not action_course_id.startswith("c")
        )

        if is_course_likely_title or not action_course_id:
            if enriched_context and enriched_context.get("courseId"):
                action_data["courseId"] = enriched_context["courseId"]
                print(f"📝 Set courseId from enriched_context: {enriched_context['courseId']}")
            elif context and context.get("courseId"):
                action_data["courseId"] = context["courseId"]
                print(f"📝 Set courseId from original context: {context['courseId']}")
            elif is_course_likely_title:
                print(
                    f"⚠️ AI provided courseId that looks like a title: {action_course_id}, removing it"
                )
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

            # 4. Save User Message to DB
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "USER",
                    "content": user_text,
                }
            )

            # 5. Build History for Context (Last 10 messages)
            history_records = await db.chatmessage.find_many(
                where={"sessionId": session.id}, order={"createdAt": "asc"}, take=10
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

            # 4.5. Check credits before processing AI response (after context/history are built)
            # Estimate tokens needed: user message + context + history (approximate 4 chars per token)
            estimated_input_tokens = (
                len(user_text) + len(str(enriched_context or "")) + len(str(formatted_history))
            ) // 4
            # Reserve credits for response (estimate max response size)
            estimated_output_tokens = 1000  # Conservative estimate for response
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

                    if (
                        tier == "FREE"
                        and daily_limit > 0
                        and (used_today + estimated_total_tokens > daily_limit)
                    ):
                        error_message = (
                            f"Daily credit limit exceeded. You've used {used_today:,} "
                            f"of {daily_limit:,} daily credits. "
                            f"Resets in: {credit_usage.get('next_daily_reset', 'midnight')}"
                        )
                    else:
                        error_message = (
                            f"Monthly credit limit exceeded. You've used {credit_usage['credits_used']:,} "
                            f"of {credit_usage['hard_cap']:,} credits. "
                            f"Period resets: {credit_usage['period_end']}"
                        )

                    await manager.send_personal_message(f"⚠️ **System:** {error_message}", user.id)
                    await websocket.close()
                    return
            except SubscriptionLimitError as e:
                await manager.send_personal_message(f"⚠️ **System:** {e.message}", user.id)
                await websocket.close()
                return

            # 6. Check if user is asking for a summary
            # Simple detection: check if message contains "summary" or "summarize"
            is_summary_request = (
                "summary" in user_text.lower()
                or "summarize" in user_text.lower()
                or "summarise" in user_text.lower()
            )

            # If summary requested and we have context with content, generate summary
            if is_summary_request:
                # Check for content in enriched context or original context
                # Priority: direct content > noteContent > topicContent
                content_to_summarize = None
                if enriched_context and enriched_context.get("content"):
                    content_to_summarize = enriched_context["content"]
                elif enriched_context and enriched_context.get("noteContent"):
                    content_to_summarize = enriched_context["noteContent"]
                elif enriched_context and enriched_context.get("topicContent"):
                    content_to_summarize = enriched_context["topicContent"]
                elif context and context.get("content"):
                    content_to_summarize = context["content"]
                elif context and context.get("noteContent"):
                    content_to_summarize = context["noteContent"]

                if content_to_summarize:
                    summary = await llm_service.generate_summary(content_to_summarize)
                    ai_response_text = f"I've generated a summary for you:\n\n{summary}"
                else:
                    # No content to summarize, ask user or provide general response
                    ai_response_text = await llm_service.get_chat_response(
                        history=formatted_history, user_message=user_text, context=enriched_context
                    )
            else:
                # 6. Get AI Response (with optional enriched context)
                ai_response_text = await llm_service.get_chat_response(
                    history=formatted_history, user_message=user_text, context=enriched_context
                )

            # --- NEW: Action Detection Logic ---
            # Regex to find content between <<<ACTION_START>>> and <<<ACTION_END>>>
            # Use more flexible regex to handle whitespace variations
            action_match = re.search(
                r"<<<ACTION_START>>>\s*(.*?)\s*<<<ACTION_END>>>", ai_response_text, re.DOTALL
            )

            clean_response = ai_response_text  # Default to full text
            action_results = []

            if action_match:
                try:
                    print(f"⚙️ Action Detected for User {user.id}")
                    # 1. Extract and Parse JSON
                    json_str = action_match.group(1).strip()
                    action_payload = json.loads(json_str)

                    # Check if this is a single action or multiple actions
                    if "actions" in action_payload:
                        # Multiple actions
                        actions_list = action_payload.get("actions", [])
                        print(f"📋 Processing {len(actions_list)} actions in batch")
                    else:
                        # Single action (backward compatibility)
                        actions_list = [action_payload]
                        print(f"📋 Processing single action")

                    # Track created IDs for dependency resolution
                    created_ids = {}

                    # Execute each action sequentially
                    for idx, action_payload_item in enumerate(actions_list):
                        action_type = action_payload_item.get("type")
                        action_data = action_payload_item.get("data", {}).copy()

                        print(
                            f"\n🔄 Processing action {idx + 1}/{len(actions_list)}: {action_type}"
                        )

                        # 2. Enrich action data with context and resolve dependencies
                        action_data = await enrich_action_data(
                            action_type=action_type,
                            action_data=action_data,
                            enriched_context=enriched_context,
                            context=context,
                            created_ids=created_ids,
                        )

                        # 3. Execute Action
                        print(f"🚀 Executing {action_type} with action_data: {action_data}")
                        action_result = await action_service.execute_action(
                            action_type=action_type,
                            action_data=action_data,
                            user_id=user.id,
                        )
                        print(f"📤 Action result: {action_result}")

                        # Store result
                        action_results.append(
                            {
                                "type": action_type,
                                "result": action_result,
                            }
                        )

                        # Extract created IDs for dependency resolution
                        if action_result and action_result.get("status") == "success":
                            # Extract IDs from result based on action type
                            # Handle both camelCase and snake_case formats
                            if action_type == "create_course":
                                course_id = action_result.get("courseId") or action_result.get(
                                    "course_id"
                                )
                                if course_id:
                                    created_ids["courseId"] = course_id
                                    print(f"💾 Stored courseId: {course_id}")
                            elif action_type == "create_goal":
                                goal_id = action_result.get("goalId") or action_result.get(
                                    "goal_id"
                                )
                                if goal_id:
                                    created_ids["goalId"] = goal_id
                                    print(f"💾 Stored goalId: {goal_id}")
                            elif action_type == "create_schedule":
                                schedule_id = action_result.get("scheduleId") or (
                                    action_result.get("schedule", {}) or {}
                                ).get("id")
                                if schedule_id:
                                    created_ids["scheduleId"] = schedule_id
                                    print(f"💾 Stored scheduleId: {schedule_id}")
                            elif action_type == "create_note":
                                note_id = action_result.get("noteId") or action_result.get(
                                    "note_id"
                                )
                                if note_id:
                                    created_ids["noteId"] = note_id
                                    print(f"💾 Stored noteId: {note_id}")

                    # 4. Clean the response (remove ALL instances of action markers and content)
                    clean_response = re.sub(
                        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
                        "",
                        ai_response_text,
                        flags=re.DOTALL,
                    ).strip()

                    # 5. Append confirmation messages for all actions
                    success_messages = []
                    error_messages = []

                    for action_result_item in action_results:
                        action_type_item = action_result_item["type"]
                        result = action_result_item["result"]

                        if result and result.get("status") == "success":
                            if action_type_item == "recommend_resources":
                                resources_count = result.get("count", 0)
                                if resources_count > 0:
                                    success_messages.append(
                                        f"✅ I've found {resources_count} resource{'s' if resources_count > 1 else ''} for you! They've been saved to your resources."
                                    )
                            else:
                                success_messages.append(
                                    f"✅ **System:** {result.get('message', 'Action completed successfully')}"
                                )
                        elif result and result.get("status") == "error":
                            error_messages.append(
                                f"⚠️ **System:** {result.get('message', 'Action failed')}"
                            )

                    # Append all messages
                    if success_messages:
                        clean_response += "\n\n" + "\n".join(success_messages)
                    if error_messages:
                        clean_response += "\n\n" + "\n".join(error_messages)

                except json.JSONDecodeError as e:
                    print(f"❌ Action JSON Parse Error: {e}")
                    print(f"   JSON string: {json_str[:200] if 'json_str' in locals() else 'N/A'}")
                    # Still remove the action block even if parsing failed
                    clean_response = re.sub(
                        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
                        "",
                        ai_response_text,
                        flags=re.DOTALL,
                    ).strip()
                except Exception as e:
                    print(f"❌ Action Execution Error: {e}")
                    import traceback

                    traceback.print_exc()
                    # Remove action block on error too
                    clean_response = re.sub(
                        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
                        "",
                        ai_response_text,
                        flags=re.DOTALL,
                    ).strip()
                    clean_response += (
                        "\n\n⚠️ **System:** I tried to execute the action, but an error occurred."
                    )

            # 7. Calculate actual token usage and consume credits
            # Estimate tokens: input (user message + context + history) + output (AI response)
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

            # 8. Save AI Message to DB (We save the CLEAN text with token count)
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "ASSISTANT",
                    "content": clean_response,
                    "tokenCount": actual_total_tokens,
                }
            )

            # 9. Send text back to Client
            await manager.send_personal_message(clean_response, user.id)

            # 10. (Optional) If actions happened, send separate events to refresh the Frontend
            # Send each action result separately so frontend can add buttons for each
            if action_results:
                for action_result_item in action_results:
                    result = action_result_item.get("result")
                    if result:
                        # Include the action type in the payload for frontend processing
                        await manager.send_json({
                            "type": "event",
                            "payload": {
                                **result,
                                "action": action_result_item.get("type")
                            }
                        }, user.id)

    except WebSocketDisconnect:
        manager.disconnect(user.id)
    except Exception as e:
        print(f"WS Error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


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
