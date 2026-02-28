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
DEFAULT_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
DEFAULT_LIVE_GREETING_PROMPT = (
    "Start with a brief greeting and ask what the learner wants to study."
)


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
    conversation_turns: list[dict[str, str]] | None = None,
) -> None:
    """
    Connect to Google Live API and bridge messages between client and Gemini.
    - send_to_client(msg): send JSON string or bytes to client.
    - receive_from_client(): await next message from client (bytes or str).
    - on_done(): called when bridge ends (e.g. stop or error).
    - conversation_turns: optional list to append {"role": "user"|"assistant", "text": "..."} for post-session note.
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
    greeting_env = os.getenv("GEMINI_LIVE_GREETING_PROMPT")
    greeting_prompt = (
        greeting_env if greeting_env is not None else DEFAULT_LIVE_GREETING_PROMPT
    ).strip()

    def append_turn(role: str, text: str) -> None:
        if conversation_turns is None:
            return
        normalized = text.strip()
        if not normalized:
            return
        if conversation_turns:
            last = conversation_turns[-1]
            if last.get("role") == role and last.get("text") == normalized:
                return
        conversation_turns.append({"role": role, "text": normalized})

    async def handle_server_content(sc: dict[str, Any]) -> None:
        if sc.get("interrupted"):
            await send_to_client(json.dumps({"type": "interrupted", "session_id": session_id}))
        if "inputTranscription" in sc and sc["inputTranscription"].get("text"):
            text = sc["inputTranscription"]["text"]
            if greeting_prompt and text.strip() == greeting_prompt:
                return
            append_turn("user", text)
            await send_to_client(
                json.dumps(
                    {
                        "type": "transcription",
                        "session_id": session_id,
                        "text": text,
                    }
                )
            )
        if "outputTranscription" in sc and sc["outputTranscription"].get("text"):
            text = sc["outputTranscription"]["text"]
            append_turn("assistant", text)
            await send_to_client(
                json.dumps(
                    {
                        "type": "assistant_message",
                        "session_id": session_id,
                        "text": text,
                    }
                )
            )
        model_turn = sc.get("modelTurn")
        if model_turn and isinstance(model_turn, dict) and "parts" in model_turn:
            for part in model_turn["parts"]:
                if "text" in part and part["text"]:
                    append_turn("assistant", part["text"])
                    await send_to_client(
                        json.dumps(
                            {
                                "type": "assistant_message",
                                "session_id": session_id,
                                "text": part["text"],
                            }
                        )
                    )
                if "inlineData" in part and "data" in part["inlineData"]:
                    b64 = part["inlineData"]["data"]
                    try:
                        audio_bytes = base64.b64decode(b64)
                        await send_to_client(audio_bytes)
                    except Exception as e:
                        logger.warning("Failed to decode audio from modelTurn: %s", e)

    setup = {
        "setup": {
            "model": model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
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
                    await handle_server_content(msg["serverContent"])

            # Notify client that session is ready for audio
            await send_to_client(json.dumps({"type": "session_started", "session_id": session_id}))
            if greeting_prompt:
                await ws.send(
                    json.dumps(
                        {
                            "clientContent": {
                                "turns": [{"role": "user", "parts": [{"text": greeting_prompt}]}],
                                "turnComplete": True,
                            }
                        }
                    )
                )

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
                            await handle_server_content(msg["serverContent"])
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


async def post_gemini_live_session(
    user_id: str,
    session_id: str,
    conversation_turns: list[dict[str, str]],
    topic_id: str | None,
    course_id: str | None,
) -> None:
    """
    Run after a Gemini Live session ends: record credits (non-blocking) and optionally
    create a structured note from the conversation. Does not affect session latency.
    """
    # Record credits after the call (does not affect latency). If user ran out mid-call, we log and continue.
    try:
        from src.core.database import db
        from src.services.credit_service import CREDIT_COSTS, consume_credits
        from src.utils.exceptions import SubscriptionLimitError

        user = await db.user.find_unique(where={"id": user_id})
        if user and CREDIT_COSTS.get("gemini_live_voice"):
            await consume_credits(
                user, CREDIT_COSTS["gemini_live_voice"], operation="gemini_live_voice"
            )
    except SubscriptionLimitError as e:
        logger.warning(
            "Post-session credit recording skipped (limit reached): user_id=%s, session_id=%s, detail=%s",
            user_id,
            session_id,
            getattr(e, "detail", str(e)),
        )
    except Exception as e:
        logger.exception("Post-session credit recording failed: %s", e)

    # Generate and save note from conversation (only if we have content and topic)
    if not conversation_turns or len(conversation_turns) < 2 or not topic_id:
        return

    try:
        from src.core.database import db
        from src.models.notes import NoteCreate
        from src.services import note_service

        transcript = "\n".join(
            f"{t['role'].upper()}: {t['text']}" for t in conversation_turns if t.get("text")
        )
        if not transcript.strip():
            return

        from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        prompt = (
            "From this voice study conversation transcript, create a short structured study note "
            "as the student would write from what they learnt. Output exactly two lines:\n"
            "TITLE: <one short title>\n"
            "CONTENT: <concise bullet or paragraph content>\n"
            "Do not add any other text.\n\nTranscript:\n"
        ) + transcript[:12000]

        response = await client.aio.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
        )
        text = (response.text or "").strip()
        title = "Study session notes"
        content = ""
        for line in text.split("\n"):
            line_strip = line.strip()
            if line_strip.upper().startswith("TITLE:"):
                title = (line_strip[6:].strip() or title)[:500]
            elif line_strip.upper().startswith("CONTENT:"):
                content = line_strip[8:].strip()

        if not content:
            content = text[:50000] if text else "Summary of voice study session."

        existing = await db.note.find_first(where={"topicId": topic_id})
        if existing:
            return

        note_data = NoteCreate(
            title=title[:500],
            content=content[:50000],
            topicId=topic_id,
            courseId=course_id,
        )
        await note_service.create_note(db, user_id, note_data)
        logger.info("Created note from Gemini Live session %s for topic %s", session_id, topic_id)
    except ValueError as e:
        logger.debug("Note not created (e.g. already exists): %s", e)
    except Exception as e:
        logger.exception("Post-session note creation failed: %s", e)
