"""
Gemini Live API service.
Bridges client WebSocket to Google's Live API (BidiGenerateContent) over WebSockets.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Any, Callable, Coroutine

import websockets

logger = logging.getLogger(__name__)

# Default Live API model. Override with GEMINI_LIVE_MODEL env.
# See https://ai.google.dev/gemini-api/docs/live for current model names.
DEFAULT_LIVE_MODEL = "models/gemini-2.0-flash-live-001"
GEMINI_LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


def _get_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY")


# In-memory session store: session_id -> { user_id, system_instruction, course_id, topic_id, created_at }
_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = asyncio.Lock()


async def create_session(
    user_id: str,
    *,
    system_instruction: str | None = None,
    course_id: str | None = None,
    topic_id: str | None = None,
    chat_session_id: str | None = None,
    study_session_id: str | None = None,
) -> dict[str, Any]:
    """Create a new Gemini Live conversation session. Returns session_id and metadata."""
    session_id = str(uuid.uuid4())
    async with _sessions_lock:
        _sessions[session_id] = {
            "user_id": user_id,
            "system_instruction": system_instruction
            or "You are a helpful and friendly AI assistant.",
            "course_id": course_id,
            "topic_id": topic_id,
            "chat_session_id": chat_session_id,
            "study_session_id": study_session_id,
            "created_at": asyncio.get_event_loop().time(),
        }
    return {
        "session_id": session_id,
        "status": "active",
        "chat_session_id": chat_session_id or "",
        "study_session_id": study_session_id,
        "course_id": course_id,
        "topic_id": topic_id,
    }


async def get_session(session_id: str) -> dict[str, Any] | None:
    """Get session by id if it exists and belongs to the user."""
    async with _sessions_lock:
        return _sessions.get(session_id)


async def delete_session(session_id: str) -> None:
    """Remove session from store."""
    async with _sessions_lock:
        _sessions.pop(session_id, None)


async def list_sessions_for_user(user_id: str) -> list[dict[str, Any]]:
    """List active session ids for a user."""
    async with _sessions_lock:
        return [
            {"session_id": sid, "status": "active"}
            for sid, data in _sessions.items()
            if data.get("user_id") == user_id
        ]


async def run_gemini_live_bridge(
    session_id: str,
    user_id: str,
    *,
    send_to_client: Callable[[str | bytes], Coroutine[Any, Any, None]],
    receive_from_client: Callable[[], Coroutine[Any, Any, str | bytes | None]],
    system_instruction: str | None = None,
    on_done: Callable[[], Any] | None = None,
) -> None:
    """
    Connect to Google Live API and bridge messages between client and Gemini.
    - send_to_client(msg): send JSON string or bytes to client.
    - receive_from_client(): await next message from client (bytes or str).
    - on_done(): called when bridge ends (e.g. stop or error).
    """
    api_key = _get_api_key()
    if not api_key:
        await send_to_client(
            json.dumps(
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": "GEMINI_API_KEY not configured",
                }
            )
        )
        if on_done:
            on_done()
        return

    url = f"{GEMINI_LIVE_WS_URL}?key={api_key}"
    model = os.getenv("GEMINI_LIVE_MODEL", DEFAULT_LIVE_MODEL)
    system_text = system_instruction or "You are a helpful and friendly AI assistant."

    setup = {
        "setup": {
            "model": model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
            },
            "systemInstruction": {
                "parts": [{"text": system_text}],
            },
        }
    }

    try:
        async with websockets.connect(url) as ws:  # type: ignore[union-attr]
            # Send setup and wait for setupComplete
            await ws.send(json.dumps(setup))
            setup_done = False
            while not setup_done:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                msg = json.loads(raw)
                if "setupComplete" in msg:
                    setup_done = True
                    break
                if "serverContent" in msg:
                    sc = msg["serverContent"]
                    if sc.get("interrupted"):
                        await send_to_client(
                            json.dumps({"type": "interrupted", "session_id": session_id})
                        )
                    if "inputTranscription" in sc and sc["inputTranscription"].get("text"):
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "transcription",
                                    "session_id": session_id,
                                    "text": sc["inputTranscription"]["text"],
                                }
                            )
                        )
                    if "outputTranscription" in sc and sc["outputTranscription"].get("text"):
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "assistant_message",
                                    "session_id": session_id,
                                    "text": sc["outputTranscription"]["text"],
                                }
                            )
                        )
                    model_turn = sc.get("modelTurn")
                    if model_turn and isinstance(model_turn, dict) and "parts" in model_turn:
                        for part in model_turn["parts"]:
                            if "inlineData" in part and "data" in part["inlineData"]:
                                b64 = part["inlineData"]["data"]
                                try:
                                    audio_bytes = base64.b64decode(b64)
                                    await send_to_client(audio_bytes)
                                except Exception as e:
                                    logger.warning("Failed to decode audio from modelTurn: %s", e)

            # Notify client that session is ready for audio
            await send_to_client(json.dumps({"type": "session_started", "session_id": session_id}))

            async def from_client_to_gemini() -> None:
                try:
                    while True:
                        client_msg = await receive_from_client()
                        if client_msg is None:
                            break
                        if isinstance(client_msg, bytes):
                            b64 = base64.b64encode(client_msg).decode("ascii")
                            payload = {
                                "realtimeInput": {
                                    "audio": {"mimeType": "audio/pcm;rate=16000", "data": b64}
                                }
                            }
                            await ws.send(json.dumps(payload))
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception("Error forwarding client -> Gemini: %s", e)

            async def from_gemini_to_client() -> None:
                try:
                    while True:
                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        msg = json.loads(raw)
                        if "serverContent" in msg:
                            sc = msg["serverContent"]
                            if sc.get("interrupted"):
                                await send_to_client(
                                    json.dumps({"type": "interrupted", "session_id": session_id})
                                )
                            if "inputTranscription" in sc and sc["inputTranscription"].get("text"):
                                await send_to_client(
                                    json.dumps(
                                        {
                                            "type": "transcription",
                                            "session_id": session_id,
                                            "text": sc["inputTranscription"]["text"],
                                        }
                                    )
                                )
                            if "outputTranscription" in sc and sc["outputTranscription"].get(
                                "text"
                            ):
                                await send_to_client(
                                    json.dumps(
                                        {
                                            "type": "assistant_message",
                                            "session_id": session_id,
                                            "text": sc["outputTranscription"]["text"],
                                        }
                                    )
                                )
                            model_turn = sc.get("modelTurn")
                            if (
                                model_turn
                                and isinstance(model_turn, dict)
                                and "parts" in model_turn
                            ):
                                for part in model_turn["parts"]:
                                    if "inlineData" in part and "data" in part["inlineData"]:
                                        b64 = part["inlineData"]["data"]
                                        try:
                                            audio_bytes = base64.b64decode(b64)
                                            await send_to_client(audio_bytes)
                                        except Exception as e:
                                            logger.warning("Failed to decode audio: %s", e)
                except asyncio.CancelledError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    return
                except Exception as e:
                    logger.exception("Error forwarding Gemini -> client: %s", e)

            recv_task = asyncio.create_task(from_gemini_to_client())
            try:
                await from_client_to_gemini()
            finally:
                recv_task.cancel()
                try:
                    await recv_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        logger.exception("Gemini Live bridge error: %s", e)
        await send_to_client(
            json.dumps(
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": str(e),
                }
            )
        )
    finally:
        if on_done:
            on_done()
