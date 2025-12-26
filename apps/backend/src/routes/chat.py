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
from src.services.llm_service import llm_service
from src.services.socket_manager import manager
from src.services.voice_service import voice_service

router = APIRouter()
db = Prisma()


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
            action_result = None

            if action_match:
                try:
                    print(f"⚙️ Action Detected for User {user.id}")
                    # 1. Extract and Parse JSON
                    json_str = action_match.group(1).strip()
                    action_payload = json.loads(json_str)
                    action_data = action_payload.get("data", {})

                    # 2. Enrich action data with context if missing (e.g., topicId, noteId)
                    action_type = action_payload.get("type")

                    # For retake_note and add_summary, ensure noteId is populated from context
                    if action_type in ["retake_note", "add_summary"]:
                        note_id = action_data.get("noteId")

                        # If noteId is missing, try to get it from context
                        if not note_id:
                            if enriched_context and enriched_context.get("noteId"):
                                note_id = enriched_context["noteId"]
                                print(f"📝 Set noteId from enriched_context: {note_id}")
                            elif context and context.get("noteId"):
                                note_id = context["noteId"]
                                print(f"📝 Set noteId from original context: {note_id}")
                            elif enriched_context and enriched_context.get("topicId"):
                                # If we have topicId but no noteId, try to get note from topic
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
                                # Try original context topicId
                                topic_id = context["topicId"]
                                print(
                                    f"🔍 No noteId found, checking topicId from context: {topic_id}"
                                )
                                topic = await db.topic.find_unique(
                                    where={"id": topic_id},
                                    include={"note": True},
                                )
                                if topic and topic.note:
                                    note_id = topic.note.id
                                    print(f"✅ Found note from topic: {note_id}")

                        # If we still have a noteId, check if it's actually a topicId
                        if note_id:
                            print(f"🔍 Verifying noteId: {note_id}")
                            note = await db.note.find_unique(where={"id": note_id})
                            if not note:
                                print(
                                    f"⚠️ Note with ID {note_id} not found, checking if it's a topicId..."
                                )
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
                                        note_id = None  # Clear note_id so error is returned
                                else:
                                    print(f"⚠️ ID {note_id} is neither a note nor a topic")
                                    note_id = None  # Clear note_id so error is returned

                        if note_id:
                            action_data["noteId"] = note_id
                            print(f"✅ Final noteId set in action_data: {note_id}")
                        else:
                            print("⚠️ No noteId found in context for retake_note/add_summary action")
                            print(f"   enriched_context: {enriched_context}")
                            print(f"   original context: {context}")

                    if action_type == "create_note":
                        # Debug: Log what we have
                        print(f"🔍 Action data topicId: {action_data.get('topicId')}")
                        print(
                            f"🔍 Enriched context topicId: {enriched_context.get('topicId') if enriched_context else None}"
                        )
                        print(
                            f"🔍 Original context topicId: {context.get('topicId') if context else None}"
                        )

                        # ALWAYS override topicId from context if available (AI might use title instead of ID)
                        # Check if action_data has a topicId that looks like a title (not an ID format)
                        action_topic_id = action_data.get("topicId")
                        is_likely_title = action_topic_id and (
                            len(action_topic_id)
                            > 30  # IDs are typically shorter (CUID format ~25 chars)
                            or " " in action_topic_id  # Titles have spaces, IDs don't
                            or not action_topic_id.startswith("c")  # CUIDs start with 'c'
                        )

                        # ALWAYS override topicId with actual ID from context (AI might use title)
                        # Priority: enriched_context > original context > action_data (only if valid ID)
                        if enriched_context and enriched_context.get("topicId"):
                            # Always use enriched context topicId (it's the real ID from DB)
                            action_data["topicId"] = enriched_context["topicId"]
                            print(
                                f"📝 Set topicId from enriched_context: {enriched_context['topicId']} (was: {action_topic_id})"
                            )
                        elif context and context.get("topicId"):
                            # Fallback to original context topicId
                            action_data["topicId"] = context["topicId"]
                            print(
                                f"📝 Set topicId from original context: {context['topicId']} (was: {action_topic_id})"
                            )
                        elif is_likely_title:
                            # If AI provided a title but we don't have context, that's an error
                            print(
                                f"⚠️ AI provided topicId that looks like a title: {action_topic_id}, but no context available"
                            )

                        # Same for courseId - check if AI provided a title instead of ID
                        action_course_id = action_data.get("courseId")
                        is_course_likely_title = action_course_id and (
                            len(action_course_id) > 30  # CUIDs are ~25 chars
                            or " " in action_course_id  # Titles have spaces, IDs don't
                            or not action_course_id.startswith("c")  # CUIDs start with 'c'
                        )

                        # ALWAYS override courseId if it looks like a title or if missing
                        if is_course_likely_title or not action_course_id:
                            if enriched_context and enriched_context.get("courseId"):
                                action_data["courseId"] = enriched_context["courseId"]
                                print(
                                    f"📝 Set courseId from enriched_context: {enriched_context['courseId']} (was: {action_course_id})"
                                )
                            elif context and context.get("courseId"):
                                action_data["courseId"] = context["courseId"]
                                print(
                                    f"📝 Set courseId from original context: {context['courseId']} (was: {action_course_id})"
                                )
                            elif is_course_likely_title:
                                # If AI provided a title but we don't have context, remove it (courseId is optional)
                                print(
                                    f"⚠️ AI provided courseId that looks like a title: {action_course_id}, removing it (courseId is optional)"
                                )
                                action_data.pop("courseId", None)

                        print(f"🔍 Final action_data before execution: {action_data}")

                    # 3. Execute Action
                    print(f"🚀 Executing {action_type} with action_data: {action_data}")
                    action_result = await action_service.execute_action(
                        action_type=action_payload.get("type"),
                        action_data=action_data,
                        user_id=user.id,
                    )
                    print(f"📤 Action result: {action_result}")

                    # 4. Clean the response (remove ALL instances of action markers and content)
                    # Remove the entire action block including any surrounding whitespace/newlines
                    action_block = action_match.group(0)
                    clean_response = re.sub(
                        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
                        "",
                        ai_response_text,
                        flags=re.DOTALL,
                    ).strip()

                    # 5. Append a confirmation message if successful
                    if action_result and action_result.get("status") == "success":
                        clean_response += f"\n\n✅ **System:** {action_result['message']}"
                    elif action_result and action_result.get("status") == "error":
                        clean_response += (
                            f"\n\n⚠️ **System:** {action_result.get('message', 'Action failed')}"
                        )

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

            # 7. Save AI Message to DB (We save the CLEAN text)
            await db.chatmessage.create(
                data={
                    "sessionId": session.id,
                    "userId": user.id,
                    "role": "ASSISTANT",
                    "content": clean_response,
                }
            )

            # 8. Send text back to Client
            await manager.send_personal_message(clean_response, user.id)

            # 9. (Optional) If an action happened, send a separate event to refresh the Frontend
            if action_result:
                await manager.send_json({"type": "event", "payload": action_result}, user.id)

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
