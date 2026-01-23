"""
Gemini Live API Routes.
Handles real-time voice conversations using Gemini Live API via WebSocket.
"""

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from src.core.database import db
from src.dependencies import CurrentUser
from src.routes.chat import get_current_user_ws
from src.services.gemini_live_service import get_gemini_live_service
from src.services.llm_service import SYSTEM_INSTRUCTION, GeminiService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gemini-live", tags=["Gemini Live"])


class StartConversationRequest(BaseModel):
    """Request model for starting a Gemini Live conversation."""

    session_id: Optional[str] = None
    system_instruction: Optional[str] = None
    course_id: Optional[str] = None
    topic_id: Optional[str] = None


@router.post("/conversation/start")
async def start_conversation(
    request: StartConversationRequest,
    user: CurrentUser,
):
    """
    Start a new Gemini Live conversation session.
    Returns session_id for WebSocket connection.
    """
    try:
        # Ensure DB is connected (safety check for race conditions)
        if not db.is_connected():
            await db.connect()

        gemini_service = get_gemini_live_service()

        # Find or create active study session (acts as the current study session)
        study_session = await db.studysession.find_first(
            where={"userId": user.id, "endTime": None}, order={"startTime": "desc"}
        )

        # If no active study session, create one
        if not study_session:
            study_session = await db.studysession.create(
                data={
                    "userId": user.id,
                    "startTime": datetime.now(timezone.utc),
                    "duration": 0.0,  # Will be updated when session ends
                    "courseId": request.course_id,
                    "topicId": request.topic_id,
                }
            )
        else:
            # Update study session with course/topic if provided
            if request.course_id or request.topic_id:
                update_data = {}
                if request.course_id:
                    update_data["courseId"] = request.course_id
                if request.topic_id:
                    update_data["topicId"] = request.topic_id
                if update_data:
                    study_session = await db.studysession.update(
                        where={"id": study_session.id}, data=update_data
                    )

        # Get course and topic context for system instruction
        course = None
        topic = None
        if study_session.courseId:
            course = await db.course.find_unique(where={"id": study_session.courseId})
        if study_session.topicId:
            topic = await db.topic.find_unique(where={"id": study_session.topicId})

        # Build contextual system instruction
        context_parts = []
        if course:
            context_parts.append(f"Course: {course.title}")
        if topic:
            context_parts.append(f"Topic: {topic.title}")
            if topic.content:
                context_parts.append(f"Topic Content: {topic.content[:500]}...")

        context_info = "\n".join(context_parts) if context_parts else ""

        # Enhanced system instruction with study session context
        enhanced_instruction = SYSTEM_INSTRUCTION
        if context_info:
            enhanced_instruction = f"""{SYSTEM_INSTRUCTION}

CURRENT STUDY SESSION CONTEXT:
{context_info}

IMPORTANT: You are helping the user study this topic. Take notes in the background based on the discussion.
When key concepts are discussed, automatically create or update notes for this topic.
"""

        # Use system instruction from request or enhanced default
        system_instruction = request.system_instruction or enhanced_instruction

        # Create or find chat session for message storage
        chat_session = await db.chatsession.find_first(
            where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
        )

        if not chat_session:
            chat_session = await db.chatsession.create(
                data={"userId": user.id, "title": "Voice Conversation"}
            )

        # Track conversation for note generation
        conversation_buffer = []
        llm_service = GeminiService()

        # Create callbacks for saving messages to database and note generation
        async def on_user_message(text: str):
            """Save user message to database and buffer for note generation."""
            try:
                await db.chatmessage.create(
                    data={
                        "sessionId": chat_session.id,
                        "userId": user.id,
                        "role": "USER",
                        "content": text,
                    }
                )
                conversation_buffer.append({"role": "user", "content": text})
                logger.info(f"Saved user message to session {chat_session.id}")
            except Exception as e:
                logger.error(f"Error saving user message: {e}")

        async def on_assistant_message(text: str):
            """Save assistant message to database and buffer for note generation."""
            try:
                await db.chatmessage.create(
                    data={
                        "sessionId": chat_session.id,
                        "userId": user.id,
                        "role": "ASSISTANT",
                        "content": text,
                    }
                )
                conversation_buffer.append({"role": "assistant", "content": text})
                logger.info(f"Saved assistant message to session {chat_session.id}")

                # Generate notes in background when conversation reaches threshold
                if len(conversation_buffer) >= 6:  # After 3 exchanges (6 messages)
                    asyncio.create_task(
                        generate_notes_from_conversation(
                            conversation_buffer.copy(),
                            study_session,
                            user.id,
                        )
                    )
                    conversation_buffer.clear()  # Clear buffer after processing
            except Exception as e:
                logger.error(f"Error saving assistant message: {e}")

        async def generate_notes_from_conversation(messages: list, study_session, user_id: str):
            """Generate notes from conversation in the background."""
            try:
                if not study_session.topicId:
                    logger.info("No topic ID in study session, skipping note generation")
                    return

                # Check if note already exists for this topic
                existing_note = await db.note.find_unique(where={"topicId": study_session.topicId})

                # Format conversation for summarization
                conversation_text = "\n".join(
                    [
                        f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
                        for msg in messages
                    ]
                )

                # Generate note content from conversation
                note_prompt = f"""Based on the following study conversation, create or update comprehensive notes.

Conversation:
{conversation_text}

Create well-structured notes in markdown format covering:
- Key concepts discussed
- Important explanations
- Examples or clarifications
- Any questions and answers

Format the notes with proper headings, lists, and structure."""

                note_content = await llm_service.model.generate_content_async(note_prompt)

                if existing_note:
                    # Update existing note by appending new content
                    updated_content = (
                        f"{existing_note.content}\n\n---\n\n## Additional Notes from Voice Discussion\n\n{note_content.text}"
                        if existing_note.content
                        else f"## Notes from Voice Discussion\n\n{note_content.text}"
                    )
                    await db.note.update(
                        where={"id": existing_note.id},
                        data={"content": updated_content, "updatedAt": datetime.now(timezone.utc)},
                    )
                    logger.info(f"Updated note {existing_note.id} from conversation")
                else:
                    # Create new note
                    topic = await db.topic.find_unique(where={"id": study_session.topicId})
                    note_title = f"Study Notes: {topic.title}" if topic else "Study Notes"

                    await db.note.create(
                        data={
                            "userId": user_id,
                            "title": note_title,
                            "content": f"## Notes from Voice Discussion\n\n{note_content.text}",
                            "topicId": study_session.topicId,
                            "courseId": study_session.courseId,
                        }
                    )
                    logger.info(
                        f"Created new note from conversation for topic {study_session.topicId}"
                    )

            except Exception as e:
                logger.error(f"Error generating notes from conversation: {e}", exc_info=True)

        # Start conversation with callbacks
        session_id = request.session_id or str(uuid.uuid4())
        result = await gemini_service.start_conversation(
            user_id=user.id,
            session_id=session_id,
            on_user_message=on_user_message,
            on_assistant_message=on_assistant_message,
            system_instruction=system_instruction,
        )

        # Store additional info in session for later use
        session_info = gemini_service.get_session_info(session_id)
        if session_info:
            session_info["chat_session_id"] = chat_session.id
            session_info["study_session_id"] = study_session.id

        return {
            "session_id": result["session_id"],
            "status": "started",
            "chat_session_id": chat_session.id,
            "study_session_id": study_session.id,
            "course_id": study_session.courseId,
            "topic_id": study_session.topicId,
        }

    except Exception as e:
        logger.error(f"Error starting Gemini Live conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start conversation: {str(e)}")


@router.post("/conversation/{session_id}/stop")
async def stop_conversation(session_id: str, user: CurrentUser):
    """
    Stop an active Gemini Live conversation session.
    Returns success even if session is already stopped (idempotent).
    """
    try:
        gemini_service = get_gemini_live_service()

        # Check if session exists
        session_info = gemini_service.get_session_info(session_id)
        if not session_info:
            # Session already stopped or doesn't exist - return success (idempotent)
            logger.info(f"Stop requested for non-existent session {session_id}, returning success")
            return {"session_id": session_id, "status": "stopped", "already_stopped": True}

        # Verify session belongs to user
        if session_info["user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Session does not belong to user")

        success = await gemini_service.stop_conversation(session_id)

        if success:
            return {"session_id": session_id, "status": "stopped"}
        else:
            raise HTTPException(status_code=500, detail="Failed to stop conversation")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping Gemini Live conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stop conversation: {str(e)}")


@router.get("/conversation/{session_id}/status")
async def get_conversation_status(session_id: str, user: CurrentUser):
    """
    Get the status of an active Gemini Live conversation session.
    """
    try:
        gemini_service = get_gemini_live_service()

        session_info = gemini_service.get_session_info(session_id)
        if not session_info:
            raise HTTPException(status_code=404, detail="Session not found")

        if session_info["user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Session does not belong to user")

        return {
            "session_id": session_id,
            "status": "active",
            "user_id": session_info["user_id"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversation status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


@router.get("/conversations")
async def list_conversations(user: CurrentUser):
    """
    List all active Gemini Live conversation sessions for the current user.
    """
    try:
        gemini_service = get_gemini_live_service()

        # Filter sessions by user_id
        user_sessions = [
            session_id
            for session_id in gemini_service.get_active_sessions()
            if gemini_service.get_session_info(session_id)["user_id"] == user.id
        ]

        return {
            "sessions": [
                {
                    "session_id": session_id,
                    "status": "active",
                }
                for session_id in user_sessions
            ]
        }

    except Exception as e:
        logger.error(f"Error listing conversations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list conversations: {str(e)}")


@router.websocket("/ws/{session_id}")
async def gemini_live_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
):
    """
    WebSocket endpoint for Gemini Live conversation.

    Handles:
    - Receiving audio data from client (base64 encoded PCM audio)
    - Sending transcription and assistant responses back to client
    - Forwarding audio to Gemini Live API
    """
    # Authenticate user
    try:
        user = await get_current_user_ws(token)
    except Exception as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Verify session belongs to user
    gemini_service = get_gemini_live_service()
    session_info = gemini_service.get_session_info(session_id)

    if not session_info:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
        return

    if session_info["user_id"] != user.id:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Session does not belong to user"
        )
        return

    await websocket.accept()
    logger.info(f"WebSocket accepted for session {session_id}, user {user.id}")

    # Set up callbacks to forward messages to WebSocket client
    async def on_transcription(text: str):
        """Send transcription to WebSocket client."""
        try:
            await websocket.send_json({"type": "transcription", "text": text})
        except Exception as e:
            logger.error(f"Error sending transcription: {e}")

    async def on_assistant_message(text: str):
        """Send assistant message to WebSocket client."""
        try:
            await websocket.send_json({"type": "assistant_message", "text": text})
        except Exception as e:
            logger.error(f"Error sending assistant message: {e}")

    # Update session callbacks to also send to WebSocket
    original_on_user = session_info.get("on_user_message")
    original_on_assistant = session_info.get("on_assistant_message")
    original_on_transcription = session_info.get("on_transcription")

    async def combined_on_user(text: str):
        if original_on_user:
            if asyncio.iscoroutinefunction(original_on_user):
                await original_on_user(text)
            else:
                original_on_user(text)
        await on_transcription(text)

    async def combined_on_assistant(text: str):
        if original_on_assistant:
            if asyncio.iscoroutinefunction(original_on_assistant):
                await original_on_assistant(text)
            else:
                original_on_assistant(text)
        await on_assistant_message(text)

    # Update session callbacks
    session_info["on_user_message"] = combined_on_user
    session_info["on_assistant_message"] = combined_on_assistant
    if original_on_transcription:
        session_info["on_transcription"] = original_on_transcription

    try:
        while True:
            # Check if session is still active before processing messages
            if session_id not in gemini_service.get_active_sessions():
                logger.info(f"Session {session_id} no longer active, closing WebSocket")
                await websocket.send_json(
                    {"type": "error", "message": "Session ended due to service unavailability"}
                )
                break

            # Receive messages from client
            message = await websocket.receive()

            # Check for disconnect message
            if message.get("type") == "websocket.disconnect":
                logger.info(f"WebSocket disconnect received for session {session_id}")
                break

            if "text" in message:
                # JSON message (control commands)
                data = json.loads(message["text"])
                msg_type = data.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "stop":
                    await gemini_service.stop_conversation(session_id)
                    await websocket.send_json({"type": "stopped", "session_id": session_id})
                    break
                elif msg_type == "audio":
                    # Audio data (base64 encoded PCM)
                    audio_base64 = data.get("data")
                    if audio_base64:
                        try:
                            audio_bytes = base64.b64decode(audio_base64)
                            success = await gemini_service.send_audio(session_id, audio_bytes)
                            if not success:
                                logger.warning(
                                    f"Failed to send audio for session {session_id}, closing WebSocket"
                                )
                                await websocket.send_json(
                                    {
                                        "type": "error",
                                        "message": "Failed to send audio - session may be inactive",
                                    }
                                )
                                break
                        except Exception as e:
                            logger.error(f"Error processing audio: {e}")
                            await websocket.send_json(
                                {"type": "error", "message": f"Error processing audio: {str(e)}"}
                            )
                            break

            elif "bytes" in message:
                # Binary audio data (raw PCM)
                audio_bytes = message["bytes"]
                try:
                    success = await gemini_service.send_audio(session_id, audio_bytes)
                    if not success:
                        logger.warning(
                            f"Failed to send audio bytes for session {session_id}, closing WebSocket"
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "Failed to send audio - session may be inactive",
                            }
                        )
                        break
                except Exception as e:
                    logger.error(f"Error processing audio bytes: {e}")
                    await websocket.send_json(
                        {"type": "error", "message": f"Error processing audio: {str(e)}"}
                    )
                    break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
        # Don't try to close - already disconnected
    except RuntimeError as e:
        # Handle "Cannot call receive once disconnect message has been received" error
        error_msg = str(e).lower()
        if "disconnect" in error_msg or "receive" in error_msg:
            logger.info(f"WebSocket already disconnected for session {session_id}: {e}")
        else:
            logger.error(f"WebSocket runtime error for session {session_id}: {e}", exc_info=True)
            # Only try to close if it's not a disconnect-related error
            try:
                await websocket.close()
            except Exception:
                pass  # WebSocket already closed
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}", exc_info=True)
        # Only try to close if websocket is still open and error is not disconnect-related
        error_msg = str(e).lower()
        if "disconnect" not in error_msg and "close" not in error_msg:
            try:
                await websocket.close()
            except Exception:
                pass  # WebSocket already closed
