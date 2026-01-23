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
from src.services.socket_manager import manager as gemini_live_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gemini-live", tags=["Gemini Live"])


# Create a separate connection manager for Gemini Live to avoid conflicts with chat
class GeminiLiveConnectionManager:
    """Manages WebSocket connections for Gemini Live (one per user)."""

    def __init__(self):
        # Maps user_id -> WebSocket connection
        self.active_connections: dict[str, WebSocket] = {}
        # Maps user_id -> active_session_id (only one active Gemini Live session per user)
        self.user_sessions: dict[str, str] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept a new WebSocket connection and store it."""
        await websocket.accept()
        # If user already has a connection, close it first
        if user_id in self.active_connections:
            try:
                old_ws = self.active_connections[user_id]
                await old_ws.close(code=1000, reason="New connection established")
            except Exception:
                pass
        self.active_connections[user_id] = websocket
        logger.info(f"User {user_id} connected to Gemini Live WebSocket.")

    def disconnect(self, user_id: str):
        """Remove a user's connection."""
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]
        logger.info(f"User {user_id} disconnected from Gemini Live WebSocket.")

    def set_active_session(self, user_id: str, session_id: str):
        """Set the active Gemini Live session for a user."""
        self.user_sessions[user_id] = session_id

    def get_active_session(self, user_id: str) -> str | None:
        """Get the active Gemini Live session for a user."""
        return self.user_sessions.get(user_id)

    async def send_json(self, data: dict, user_id: str):
        """Send a JSON object to a specific user."""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.error(f"Error sending JSON to user {user_id}: {e}")
                self.disconnect(user_id)

    async def send_bytes(self, data: bytes, user_id: str):
        """Send binary data to a specific user."""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                await websocket.send_bytes(data)
            except Exception as e:
                logger.error(f"Error sending bytes to user {user_id}: {e}")
                self.disconnect(user_id)


# Global instance for Gemini Live connections
gemini_live_connection_manager = GeminiLiveConnectionManager()


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


@router.websocket("/ws")
async def gemini_live_websocket(
    websocket: WebSocket,
    token: str = Query(...),
):
    """
    WebSocket endpoint for Gemini Live conversation.

    Maintains a single persistent WebSocket connection per user.
    Messages include session_id to route to the correct Gemini Live session.

    Handles:
    - Receiving audio data from client (binary PCM or base64 encoded)
    - Sending transcription and assistant responses back to client
    - Forwarding audio to Gemini Live API
    """
    # Authenticate user
    try:
        user = await get_current_user_ws(token)
        logger.info(f"Gemini Live WebSocket connection attempt for user {user.id}")
    except Exception as e:
        logger.warning(f"Gemini Live WebSocket authentication failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Connect using connection manager (one connection per user)
    await gemini_live_connection_manager.connect(websocket, user.id)
    logger.info(f"Gemini Live WebSocket connected for user {user.id}")

    gemini_service = get_gemini_live_service()

    # Set up callbacks that route to the connection manager
    async def setup_session_callbacks(session_id: str):
        """Set up callbacks for a Gemini Live session to send via WebSocket."""
        session_info = gemini_service.get_session_info(session_id)
        if not session_info:
            return

        original_on_user = session_info.get("on_user_message")
        original_on_assistant = session_info.get("on_assistant_message")
        original_on_transcription = session_info.get("on_transcription")

        async def on_transcription(text: str):
            """Send transcription to WebSocket client."""
            await gemini_live_connection_manager.send_json(
                {"type": "transcription", "text": text, "session_id": session_id}, user.id
            )

        async def on_assistant_message(text: str):
            """Send assistant message to WebSocket client."""
            await gemini_live_connection_manager.send_json(
                {"type": "assistant_message", "text": text, "session_id": session_id}, user.id
            )

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
            # Receive messages from client
            message = await websocket.receive()

            # Check for disconnect message
            if message.get("type") == "websocket.disconnect":
                logger.info(f"Gemini Live WebSocket disconnect received for user {user.id}")
                break

            # Extract session_id from message
            session_id = None
            if "text" in message:
                # JSON message (control commands or audio)
                try:
                    data = json.loads(message["text"])
                    session_id = data.get("session_id")
                    msg_type = data.get("type")

                    if not session_id and msg_type != "ping":
                        await gemini_live_connection_manager.send_json(
                            {"type": "error", "message": "session_id required in message"}, user.id
                        )
                        continue

                    if session_id:
                        # Verify session belongs to user
                        session_info = gemini_service.get_session_info(session_id)
                        if not session_info:
                            await gemini_live_connection_manager.send_json(
                                {
                                    "type": "error",
                                    "message": "Session not found",
                                    "session_id": session_id,
                                },
                                user.id,
                            )
                            continue

                        if session_info["user_id"] != user.id:
                            await gemini_live_connection_manager.send_json(
                                {
                                    "type": "error",
                                    "message": "Session does not belong to user",
                                    "session_id": session_id,
                                },
                                user.id,
                            )
                            continue

                        # Set active session for user
                        gemini_live_connection_manager.set_active_session(user.id, session_id)
                        # Ensure callbacks are set up
                        await setup_session_callbacks(session_id)

                    if msg_type == "ping":
                        await gemini_live_connection_manager.send_json(
                            (
                                {"type": "pong", "session_id": session_id}
                                if session_id
                                else {"type": "pong"}
                            ),
                            user.id,
                        )
                    elif msg_type == "stop":
                        if session_id:
                            await gemini_service.stop_conversation(session_id)
                            await gemini_live_connection_manager.send_json(
                                {"type": "stopped", "session_id": session_id}, user.id
                            )
                            gemini_live_connection_manager.set_active_session(user.id, None)
                    elif msg_type == "audio":
                        # Audio data (base64 encoded PCM)
                        audio_base64 = data.get("data")
                        if audio_base64 and session_id:
                            try:
                                audio_bytes = base64.b64decode(audio_base64)
                                success = await gemini_service.send_audio(session_id, audio_bytes)
                                if not success:
                                    await gemini_live_connection_manager.send_json(
                                        {
                                            "type": "error",
                                            "message": "Failed to send audio - session may be inactive",
                                            "session_id": session_id,
                                        },
                                        user.id,
                                    )
                            except Exception as e:
                                logger.error(f"Error processing audio: {e}")
                                await gemini_live_connection_manager.send_json(
                                    {
                                        "type": "error",
                                        "message": f"Error processing audio: {str(e)}",
                                        "session_id": session_id,
                                    },
                                    user.id,
                                )
                    elif msg_type == "start_session":
                        # Client notifies that a session is starting
                        if session_id:
                            gemini_live_connection_manager.set_active_session(user.id, session_id)
                            await setup_session_callbacks(session_id)
                            await gemini_live_connection_manager.send_json(
                                {"type": "session_started", "session_id": session_id}, user.id
                            )

                except json.JSONDecodeError:
                    await gemini_live_connection_manager.send_json(
                        {"type": "error", "message": "Invalid JSON message"}, user.id
                    )

            elif "bytes" in message:
                # Binary audio data (raw PCM) - need session_id from active session
                session_id = gemini_live_connection_manager.get_active_session(user.id)
                if not session_id:
                    await gemini_live_connection_manager.send_json(
                        {
                            "type": "error",
                            "message": "No active session. Send start_session message first.",
                        },
                        user.id,
                    )
                    continue

                audio_bytes = message["bytes"]
                try:
                    success = await gemini_service.send_audio(session_id, audio_bytes)
                    if not success:
                        await gemini_live_connection_manager.send_json(
                            {
                                "type": "error",
                                "message": "Failed to send audio - session may be inactive",
                                "session_id": session_id,
                            },
                            user.id,
                        )
                except Exception as e:
                    logger.error(f"Error processing audio bytes: {e}")
                    await gemini_live_connection_manager.send_json(
                        {
                            "type": "error",
                            "message": f"Error processing audio: {str(e)}",
                            "session_id": session_id,
                        },
                        user.id,
                    )

    except WebSocketDisconnect:
        logger.info(f"Gemini Live WebSocket disconnected for user {user.id}")
    except RuntimeError as e:
        error_msg = str(e).lower()
        if "disconnect" in error_msg or "receive" in error_msg:
            logger.info(f"Gemini Live WebSocket already disconnected for user {user.id}: {e}")
        else:
            logger.error(
                f"Gemini Live WebSocket runtime error for user {user.id}: {e}", exc_info=True
            )
    except Exception as e:
        logger.error(f"Gemini Live WebSocket error for user {user.id}: {e}", exc_info=True)
    finally:
        gemini_live_connection_manager.disconnect(user.id)
