"""
Gemini Live API Routes.
Handles real-time voice conversations using Gemini Live API with WebRTC support.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from prisma import Prisma
from src.config import settings
from src.dependencies import CurrentUser
from src.routes.chat import get_current_user_ws
from src.services.gemini_live_service import get_gemini_live_service
from src.services.llm_service import SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gemini-live", tags=["Gemini Live"])
db = Prisma()


class StartConversationRequest(BaseModel):
    """Request model for starting a Gemini Live conversation."""

    room_url: Optional[str] = None
    token: Optional[str] = None
    session_id: Optional[str] = None
    system_instruction: Optional[str] = None


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
    user: CurrentUser = Depends(),
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

        # Create or update chat session in database first
        session = await db.chatsession.find_first(
            where={"userId": user.id, "isActive": True}, order={"updatedAt": "desc"}
        )

        if not session:
            session = await db.chatsession.create(
                data={"userId": user.id, "title": "Voice Conversation"}
            )

        # Use system instruction from request or default
        system_instruction = request.system_instruction or SYSTEM_INSTRUCTION

        # Create callbacks for saving messages to database
        async def on_user_message(text: str):
            """Save user message to database."""
            try:
                await db.chatmessage.create(
                    data={
                        "sessionId": session.id,
                        "userId": user.id,
                        "role": "USER",
                        "content": text,
                    }
                )
                logger.info(f"Saved user message to session {session.id}")
            except Exception as e:
                logger.error(f"Error saving user message: {e}")

        async def on_assistant_message(text: str):
            """Save assistant message to database."""
            try:
                await db.chatmessage.create(
                    data={
                        "sessionId": session.id,
                        "userId": user.id,
                        "role": "ASSISTANT",
                        "content": text,
                    }
                )
                logger.info(f"Saved assistant message to session {session.id}")
            except Exception as e:
                logger.error(f"Error saving assistant message: {e}")

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
            "chat_session_id": session.id,
        }

    except Exception as e:
        logger.error(f"Error starting Gemini Live conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start conversation: {str(e)}")


@router.post("/conversation/{session_id}/stop")
async def stop_conversation(session_id: str, user: CurrentUser = Depends()):
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
async def list_conversations(user: CurrentUser = Depends()):
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
