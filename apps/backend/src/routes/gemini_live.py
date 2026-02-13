"""
Gemini Live API routes.
REST endpoints for conversation start/stop/status and WebSocket bridge to Google Live API.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from pydantic import BaseModel

from src.config import settings
from src.core.database import db
from src.core.security import decode_access_token
from src.dependencies import CurrentUser
from src.services.credit_service import CREDIT_COSTS, check_credit_availability
from src.services.gemini_live_service import (
    create_session as create_live_session,
    delete_session as delete_live_session,
    get_session as get_live_session,
    list_sessions_for_user,
    post_gemini_live_session,
    run_gemini_live_bridge,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gemini-live"])


# --- Pydantic models for REST ---


class StartConversationRequest(BaseModel):
    """Request body for starting a Gemini Live conversation."""

    session_id: str | None = None
    system_instruction: str | None = None
    course_id: str | None = None
    topic_id: str | None = None


class StartConversationResponse(BaseModel):
    """Response after starting a conversation."""

    session_id: str
    status: str
    chat_session_id: str
    study_session_id: str | None = None
    course_id: str | None = None
    topic_id: str | None = None


# --- REST endpoints ---


@router.post("/conversation/start", response_model=StartConversationResponse)
async def start_conversation(
    current_user: CurrentUser,
    request: StartConversationRequest | None = None,
) -> dict[str, Any]:
    """
    Start a new Gemini Live conversation session.
    Returns session_id to use with the WebSocket (send start_session with this id).
    """
    req = request or StartConversationRequest()
    result = await create_live_session(
        user_id=current_user.id,
        system_instruction=req.system_instruction,
        course_id=req.course_id,
        topic_id=req.topic_id,
        chat_session_id=None,
        study_session_id=None,
    )
    return StartConversationResponse(
        session_id=result["session_id"],
        status=result["status"],
        chat_session_id=result.get("chat_session_id") or "",
        study_session_id=result.get("study_session_id"),
        course_id=result.get("course_id"),
        topic_id=result.get("topic_id"),
    )


@router.post("/conversation/{session_id}/stop")
async def stop_conversation(
    session_id: str,
    current_user: CurrentUser,
) -> dict[str, str]:
    """Stop an active Gemini Live conversation session."""
    session = await get_live_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await delete_live_session(session_id)
    return {"session_id": session_id, "status": "stopped"}


@router.get("/conversation/{session_id}/status")
async def get_conversation_status(
    session_id: str,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """Get the status of a Gemini Live conversation session."""
    session = await get_live_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {
        "session_id": session_id,
        "status": "active",
        "user_id": current_user.id,
    }


@router.get("/conversations")
async def list_conversations(
    current_user: CurrentUser,
) -> dict[str, Any]:
    """List active Gemini Live conversation sessions for the current user."""
    sessions = await list_sessions_for_user(current_user.id)
    return {"sessions": sessions}


# --- WebSocket auth (token in query) ---


async def get_user_from_token(token: str) -> Any:
    """Validate JWT and return user. Raises HTTPException on failure."""
    if not db.is_connected():
        await db.connect()
    try:
        payload = decode_access_token(token)
        email: str | None = payload.get("sub")
        if not email:
            raise HTTPException(status_code=403, detail="Invalid token")
        user = await db.user.find_unique(where={"email": email})
        if not user:
            raise HTTPException(status_code=403, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=403, detail="Could not validate credentials")


@router.websocket("/ws")
async def gemini_live_websocket(
    websocket: WebSocket,
    token: str = Query(..., alias="token"),
) -> None:
    """
    WebSocket endpoint for Gemini Live.
    Connect with ?token=JWT. Send start_session with session_id, then binary audio.
    Send stop to end the session.
    """
    try:
        user = await get_user_from_token(token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    client_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
    bridge_task: asyncio.Task[None] | None = None
    current_session_id: str | None = None
    current_topic_id: str | None = None
    current_course_id: str | None = None
    conversation_turns: list[dict[str, str]] = []
    session_id_for_post: str | None = None  # captured when bridge starts, for post-session

    async def send_to_client(msg: str | bytes) -> None:
        try:
            if isinstance(msg, str):
                await websocket.send_text(msg)
            else:
                await websocket.send_bytes(msg)
        except RuntimeError as e:
            if "close" in str(e).lower() or "disconnect" in str(e).lower():
                return
            logger.warning("Send to client failed: %s", e)
        except Exception as e:
            logger.warning("Send to client failed: %s", e)

    def on_bridge_done() -> None:
        nonlocal bridge_task
        sid = session_id_for_post
        turns = list(conversation_turns)
        topic_id = current_topic_id
        course_id = current_course_id
        bridge_task = None
        if sid and user:
            asyncio.create_task(post_gemini_live_session(user.id, sid, turns, topic_id, course_id))

    try:
        while True:
            try:
                raw = await websocket.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError as e:
                if "disconnect" in str(e).lower() or "close" in str(e).lower():
                    break
                raise

            if "text" in raw and raw["text"]:
                data = json.loads(raw["text"])
                msg_type = data.get("type")
                msg_session_id = data.get("session_id")

                if msg_type == "start_session" and msg_session_id:
                    if bridge_task and bridge_task.done() is False:
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "error",
                                    "session_id": msg_session_id,
                                    "message": "Another session is already active on this connection",
                                }
                            )
                        )
                        continue
                    session = await get_live_session(msg_session_id)
                    if not session:
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "error",
                                    "session_id": msg_session_id,
                                    "message": "Session not found",
                                }
                            )
                        )
                        continue
                    if session.get("user_id") != user.id:
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "error",
                                    "session_id": msg_session_id,
                                    "message": "Forbidden",
                                }
                            )
                        )
                        continue

                    # Pre-check credits so we don't start the call if user is out
                    credits_needed = CREDIT_COSTS.get("gemini_live_voice", 500)
                    is_available, _ = await check_credit_availability(user, credits_needed)
                    if not is_available:
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "error",
                                    "session_id": msg_session_id,
                                    "message": "Insufficient credits. Start a free trial or wait for your limit to reset to use voice study.",
                                }
                            )
                        )
                        continue

                    current_session_id = msg_session_id
                    session_id_for_post = msg_session_id
                    current_topic_id = session.get("topic_id")
                    current_course_id = session.get("course_id")
                    conversation_turns.clear()

                    async def receive_from_client() -> str | bytes | None:
                        return await client_queue.get()

                    bridge_task = asyncio.create_task(
                        run_gemini_live_bridge(
                            session_id=msg_session_id,
                            user_id=user.id,
                            send_to_client=send_to_client,
                            receive_from_client=receive_from_client,
                            system_instruction=session.get("system_instruction"),
                            on_done=on_bridge_done,
                            conversation_turns=conversation_turns,
                        )
                    )
                    continue

                if msg_type == "stop" and msg_session_id:
                    await client_queue.put(None)
                    await send_to_client(
                        json.dumps({"type": "stopped", "session_id": msg_session_id})
                    )
                    if msg_session_id == current_session_id:
                        current_session_id = None
                    continue

                if msg_type == "ping":
                    await send_to_client(json.dumps({"type": "pong", "session_id": msg_session_id}))
                    continue

            if "bytes" in raw and raw["bytes"]:
                await client_queue.put(raw["bytes"])

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("Gemini Live WebSocket error: %s", e)
        try:
            await send_to_client(
                json.dumps(
                    {
                        "type": "error",
                        "session_id": current_session_id or "",
                        "message": str(e),
                    }
                )
            )
        except Exception:
            pass
    finally:
        if bridge_task and bridge_task.done() is False:
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
