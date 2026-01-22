"""
Gemini Live API Routes.
Handles real-time voice conversations using Gemini Live API with WebRTC support.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from prisma import Prisma
from src.config import settings
from src.dependencies import CurrentUser
from src.routes.chat import get_current_user_ws
from src.services.gemini_live_service import get_gemini_live_service
from src.services.llm_service import SYSTEM_INSTRUCTION, GeminiService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gemini-live", tags=["Gemini Live"])
db = Prisma()


class StartConversationRequest(BaseModel):
    """Request model for starting a Gemini Live conversation."""

    room_url: Optional[str] = None
    token: Optional[str] = None
    session_id: Optional[str] = None
    system_instruction: Optional[str] = None
    course_id: Optional[str] = None
    topic_id: Optional[str] = None


class DailyRoomConfig(BaseModel):
    """Daily.co room configuration."""

    room_url: str
    token: str
    room_name: str


async def create_daily_room(user_id: str) -> DailyRoomConfig:
    """
    Create a Daily.co room for WebRTC connection.

    Args:
        user_id: User ID for the room

    Returns:
        DailyRoomConfig with room URL and token
    """
    daily_api_key = settings.DAILY_API_KEY
    if not daily_api_key:
        raise HTTPException(
            status_code=500,
            detail="Daily.co API key not configured. Set DAILY_API_KEY environment variable.",
        )

    # Create room via Daily.co API
    room_name = f"maigie-{user_id}-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.daily.co/v1/rooms",
            headers={
                "Authorization": f"Bearer {daily_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "name": room_name,
                "privacy": "private",
                "properties": {
                    "exp": int((datetime.now().timestamp() + 7200)),  # 2 hour expiry
                    "enable_chat": False,
                    "enable_knocking": False,
                    "enable_screenshare": False,
                    "enable_recording": False,
                },
            },
        )

        if response.status_code != 200:
            logger.error(f"Failed to create Daily room: {response.text}")
            raise HTTPException(
                status_code=500, detail="Failed to create Daily.co room for WebRTC connection"
            )

        room_data = response.json()

        # Create token for the room
        token_response = await client.post(
            f"https://api.daily.co/v1/rooms/{room_name}/tokens",
            headers={
                "Authorization": f"Bearer {daily_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "properties": {
                    "room_name": room_name,
                    "user_id": user_id,
                    "is_owner": True,
                }
            },
        )

        if token_response.status_code != 200:
            logger.error(f"Failed to create Daily token: {token_response.text}")
            raise HTTPException(
                status_code=500, detail="Failed to create Daily.co token for WebRTC connection"
            )

        token_data = token_response.json()

        return DailyRoomConfig(
            room_url=room_data["url"],
            token=token_data["token"],
            room_name=room_name,
        )


@router.post("/conversation/start")
async def start_conversation(
    request: StartConversationRequest,
    user: CurrentUser,
):
    """
    Start a new Gemini Live conversation session.

    Creates a Daily.co room if not provided, then starts the Gemini Live pipeline.
    """
    try:
        gemini_service = get_gemini_live_service()

        # Create Daily room if not provided
        if not request.room_url or not request.token:
            room_config = await create_daily_room(user.id)
            room_url = room_config.room_url
            token = room_config.token
        else:
            room_url = request.room_url
            token = request.token

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
        result = await gemini_service.start_conversation(
            room_url=room_url,
            token=token,
            user_id=user.id,
            session_id=request.session_id or str(uuid.uuid4()),
            on_user_message=on_user_message,
            on_assistant_message=on_assistant_message,
            system_instruction=system_instruction,
        )

        return {
            "session_id": result["session_id"],
            "room_url": room_url,
            "token": token,
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
    """
    try:
        gemini_service = get_gemini_live_service()

        # Verify session belongs to user
        session_info = gemini_service.get_session_info(session_id)
        if not session_info:
            raise HTTPException(status_code=404, detail="Session not found")

        if session_info["user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Session does not belong to user")

        success = await gemini_service.stop_conversation(session_id)

        if success:
            # Generate final notes from any remaining conversation
            session_info = gemini_service.get_session_info(session_id)
            if session_info and session_info.get("study_session_id"):
                # Trigger final note generation if needed
                # This would require storing conversation buffer in session_info
                pass

            return {"session_id": session_id, "status": "stopped"}
        else:
            raise HTTPException(status_code=500, detail="Failed to stop conversation")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping Gemini Live conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stop conversation: {str(e)}")


@router.get("/conversation/{session_id}/status")
async def get_conversation_status(session_id: str, user: CurrentUser = Depends()):
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
    WebSocket endpoint for Gemini Live conversation signaling and updates.

    This endpoint can be used for:
    - Receiving real-time transcription updates
    - Receiving assistant response text
    - Sending control messages (pause, resume, etc.)
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

    try:
        while True:
            # Receive messages from client (for control commands)
            message = await websocket.receive_json()

            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif message.get("type") == "stop":
                await gemini_service.stop_conversation(session_id)
                await websocket.send_json({"type": "stopped", "session_id": session_id})
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}", exc_info=True)
        await websocket.close()
