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
import re
import uuid
from typing import Any, Callable, Coroutine

import websockets

logger = logging.getLogger(__name__)

# Dedicated topic note for live voice study recap — never merge into the user's personal note.
STUDY_VOICE_INSIGHTS_NOTE_TITLE = "Study insights (voice)"
_STUDY_VOICE_INSIGHTS_NOTE_INTRO = (
    "> **Auto-generated from live study** on this topic. "
    "Your personal notes stay in a **separate** note on this topic; this note only captures "
    "session insights and diagrams from voice.\n\n---\n\n"
)


def _normalize_live_tool_args(raw: Any) -> dict[str, Any]:
    """Gemini Live may send function args as a dict or as a JSON string."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tier_has_standby_voice_billing(tier: str | None) -> bool:
    """Paid tiers: bill only during recent user/AI audio (active_audio). FREE: wall-clock while connected."""
    return str(tier or "FREE") != "FREE"


def _gemini_live_credits_per_minute() -> float:
    return float(os.getenv("GEMINI_LIVE_CREDITS_PER_MINUTE", "100"))


def _gemini_live_min_session_credits_wall_clock() -> int:
    return int(os.getenv("GEMINI_LIVE_MIN_SESSION_CREDITS", "500"))


def _gemini_live_standby_idle_seconds() -> float:
    """No user PCM and no AI audio for this long → standby (no billable time for paid tiers)."""
    return float(os.getenv("GEMINI_LIVE_STANDBY_IDLE_SECONDS", "2.5"))


def _gemini_live_billing_tick_seconds() -> float:
    return float(os.getenv("GEMINI_LIVE_BILLING_TICK_SECONDS", "2.0"))


def _gemini_live_billing_min_consume_chunk() -> int:
    """Pre-multiplier credits; batch DB writes until delta reaches this (or flush interval)."""
    return int(os.getenv("GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK", "50"))


def _gemini_live_billing_flush_interval_seconds() -> float:
    return float(os.getenv("GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS", "60"))


def voice_credits_from_billable_seconds_raw(billable_seconds: float) -> int:
    """Pre-multiplier credits from billable time only (no FREE-tier session floor)."""
    per_min = _gemini_live_credits_per_minute()
    return int(max(0.0, billable_seconds) / 60.0 * per_min)


def voice_credits_total_final_settlement(billable_seconds: float, billing_mode: str) -> int:
    """Final pre-multiplier total after session ends (FREE wall-clock gets a session minimum)."""
    raw = voice_credits_from_billable_seconds_raw(billable_seconds)
    if billing_mode == "active_audio":
        return raw
    return max(_gemini_live_min_session_credits_wall_clock(), raw)


# Default Live API model. Override with GEMINI_LIVE_MODEL env.
# See https://ai.google.dev/gemini-api/docs/live for current model names.
DEFAULT_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_LIVE_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
DEFAULT_LIVE_GREETING_PROMPT = "Start with a brief, warm greeting and immediately begin discussing the topic. Keep it concise - no more than two sentences."


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
    # Ensure only one active session per user - clear existing ones
    async with _sessions_lock:
        to_delete = [sid for sid, data in _sessions.items() if data.get("user_id") == user_id]
        if to_delete:
            logger.info("Ending previous sessions for user %s: %s", user_id, to_delete)
            for sid in to_delete:
                _sessions.pop(sid, None)

    # Fetch existing note content/resources if topic_id is provided.
    note_content = ""
    topic_resources_context = ""
    topic_uploaded_resources_context = ""
    if topic_id:
        try:
            from src.core.database import db

            notes_list = await db.note.find_many(
                where={"topicId": topic_id, "userId": user_id},
                order={"updatedAt": "asc"},
            )
            parts = [(n.content or "").strip() for n in notes_list if n.content]
            if parts:
                note_content = "\n\n---\n\n".join(parts)
                logger.info("Fetched %s note segment(s) for topic %s", len(parts), topic_id)

            resources = await db.resource.find_many(
                where={"topicId": topic_id, "userId": user_id},
                order={"updatedAt": "desc"},
                take=30,
            )
            if resources:

                def _fmt_resource_line(r: Any) -> str:
                    rtype = str(getattr(r, "type", "OTHER") or "OTHER").upper()
                    title = (getattr(r, "title", "") or "Untitled").strip()
                    url = (getattr(r, "url", "") or "").strip()
                    desc = (getattr(r, "description", "") or "").strip()
                    if len(desc) > 140:
                        desc = desc[:140] + "..."
                    line = f"- [{rtype}] {title}"
                    if url:
                        line += f" ({url})"
                    if desc:
                        line += f" — {desc}"
                    return line

                def _is_ai(r: Any) -> bool:
                    recommendation_source = str(
                        getattr(r, "recommendationSource", "") or ""
                    ).lower()
                    if recommendation_source == "ai":
                        return True
                    meta = getattr(r, "metadata", None)
                    if isinstance(meta, dict) and meta.get("studioAiRecommendation") is True:
                        return True
                    return False

                uploaded_manual = [r for r in resources if not _is_ai(r)]
                topic_resources_context = "\n".join(_fmt_resource_line(r) for r in resources[:10])
                if uploaded_manual:
                    topic_uploaded_resources_context = "\n".join(
                        _fmt_resource_line(r) for r in uploaded_manual[:10]
                    )
                logger.info(
                    "Fetched %s resource(s) for Gemini Live topic %s (%s non-AI)",
                    len(resources),
                    topic_id,
                    len(uploaded_manual),
                )
        except Exception as e:
            logger.warning("Failed to fetch notes/resources for Gemini Live context: %s", e)

    session_id = str(uuid.uuid4())

    # Enrich system instruction with note context
    final_instruction = system_instruction or "You are a helpful and friendly AI academic mentor."
    if note_content:
        final_instruction += f"\n\nHere are the current notes for this topic that you should reference:\n{note_content}"
    if topic_uploaded_resources_context:
        final_instruction += (
            "\n\nHere are the uploaded/manual resources for this topic (prioritize these for grounding):\n"
            f"{topic_uploaded_resources_context}"
        )
    if topic_resources_context:
        final_instruction += (
            "\n\nAdditional resources linked to this topic:\n" f"{topic_resources_context}"
        )

    async with _sessions_lock:
        _sessions[session_id] = {
            "user_id": user_id,
            "system_instruction": final_instruction,
            "course_id": course_id,
            "topic_id": topic_id,
            "chat_session_id": chat_session_id,
            "study_session_id": study_session_id,
            "created_at": asyncio.get_event_loop().time(),
            "turns_since_last_note_update": 0,
            "is_updating_note": False,
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


async def update_session_context(
    session_id: str, topic_id: str | None = None, course_id: str | None = None
) -> None:
    """Update active session context (e.g. when user manually navigates to a new topic)."""
    async with _sessions_lock:
        if session_id in _sessions:
            if topic_id:
                _sessions[session_id]["topic_id"] = topic_id
            if course_id:
                _sessions[session_id]["course_id"] = course_id
            logger.info(
                "Updated context for session %s: topic_id=%s course_id=%s",
                session_id,
                topic_id,
                course_id,
            )


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
    on_done: Callable[[int, float, bool, str], Any] | None = None,
    conversation_turns: list[dict[str, str]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    """
    Connect to Google Live API and bridge messages between client and Gemini.
    - send_to_client(msg): send JSON string or bytes to client.
    - receive_from_client(): await next message from client (bytes or str).
    - on_done(consumed_credits, billable_seconds, billing_started, billing_mode): snapshot for
      post-session settlement. billing_mode is wall_clock (FREE) or active_audio (paid standby).
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
            on_done(0, 0.0, False, "wall_clock")
        return

    from src.core.database import db

    user_row = await db.user.find_unique(where={"id": user_id})
    tier = str(user_row.tier) if user_row and user_row.tier else "FREE"
    billing_mode = "active_audio" if _tier_has_standby_voice_billing(tier) else "wall_clock"
    async with _sessions_lock:
        if session_id in _sessions:
            _sessions[session_id]["billing_mode"] = billing_mode
            _sessions[session_id]["voice_billing_started"] = False
            _sessions[session_id]["billable_seconds"] = 0.0
            _sessions[session_id]["last_user_audio_mono"] = None
            _sessions[session_id]["last_ai_audio_mono"] = None
            _sessions[session_id]["billing_tick_last_mono"] = None
            _sessions[session_id]["billing_last_flush_mono"] = None
            _sessions[session_id]["consumed_credits"] = 0

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

        # Real-time note update logic
        asyncio.create_task(check_and_trigger_note_update())

    async def mark_user_audio_activity() -> None:
        async with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["last_user_audio_mono"] = asyncio.get_running_loop().time()

    async def mark_ai_audio_activity() -> None:
        async with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["last_ai_audio_mono"] = asyncio.get_running_loop().time()

    async def apply_voice_credit_delta(to_consume: int) -> None:
        if to_consume <= 0:
            return
        try:
            from src.services.credit_service import consume_credits
            from src.utils.exceptions import SubscriptionLimitError

            user = await db.user.find_unique(where={"id": user_id})
            if user:
                await consume_credits(user, to_consume, operation="gemini_live_voice", db_client=db)
                async with _sessions_lock:
                    if session_id in _sessions:
                        prev = int(_sessions[session_id].get("consumed_credits", 0) or 0)
                        _sessions[session_id]["consumed_credits"] = prev + to_consume
        except Exception as e:
            if getattr(
                e, "code", None
            ) == "SUBSCRIPTION_LIMIT_EXCEEDED" or "SubscriptionLimitError" in str(type(e)):
                logger.warning("Real-time credit limit reached for user %s: disconnecting", user_id)
                tier = str(getattr(e, "tier", "FREE")) if hasattr(e, "tier") else "FREE"

                await send_to_client(
                    json.dumps(
                        {
                            "type": "credit_limit_error",
                            "session_id": session_id,
                            "message": "You've reached your token limit for the day.",
                            "tier": tier,
                            "is_daily_limit": True,
                            "show_referral_option": True,
                        }
                    )
                )

                async with _sessions_lock:
                    if session_id in _sessions:
                        _sessions[session_id]["force_disconnect"] = True
            else:
                logger.warning("Failed to apply voice credit delta: %s", e)

    async def voice_billing_loop() -> None:
        tick = _gemini_live_billing_tick_seconds()
        idle_gap = _gemini_live_standby_idle_seconds()
        min_chunk = _gemini_live_billing_min_consume_chunk()
        flush_iv = _gemini_live_billing_flush_interval_seconds()
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(tick)
                charge_amount = 0
                async with _sessions_lock:
                    sess = _sessions.get(session_id)
                    if not sess or sess.get("force_disconnect"):
                        break
                    now = loop.time()
                    last_tick = sess.get("billing_tick_last_mono")
                    if last_tick is None:
                        sess["billing_tick_last_mono"] = now
                        continue
                    delta = max(0.0, now - last_tick)
                    sess["billing_tick_last_mono"] = now

                    mode = sess.get("billing_mode", "wall_clock")
                    if mode == "wall_clock":
                        bill_delta = delta
                    else:
                        lu = sess.get("last_user_audio_mono")
                        la = sess.get("last_ai_audio_mono")
                        audio_recent = (lu is not None and (now - lu) <= idle_gap) or (
                            la is not None and (now - la) <= idle_gap
                        )
                        bill_delta = delta if audio_recent else 0.0

                    sess["billable_seconds"] = float(sess.get("billable_seconds", 0.0)) + bill_delta

                    billable = float(sess["billable_seconds"])
                    current_raw_total = voice_credits_from_billable_seconds_raw(billable)
                    previously_consumed = int(sess.get("consumed_credits", 0) or 0)
                    to_consume = current_raw_total - previously_consumed

                    last_flush = sess.get("billing_last_flush_mono")
                    if last_flush is None:
                        sess["billing_last_flush_mono"] = now
                        last_flush = now

                    should_charge = to_consume > 0 and (
                        to_consume >= min_chunk or (now - float(last_flush)) >= flush_iv
                    )
                    if should_charge and not sess.get("is_checking_credits"):
                        sess["is_checking_credits"] = True
                        charge_amount = to_consume

                if charge_amount > 0:
                    try:
                        await apply_voice_credit_delta(charge_amount)
                    finally:
                        async with _sessions_lock:
                            if session_id in _sessions:
                                _sessions[session_id]["is_checking_credits"] = False
                                _sessions[session_id]["billing_last_flush_mono"] = loop.time()
        except asyncio.CancelledError:
            pass

    async def check_and_trigger_note_update():
        async with _sessions_lock:
            session = _sessions.get(session_id)
            if not session or not session.get("topic_id") or session.get("is_updating_note"):
                return

            session["turns_since_last_note_update"] += 1
            # Update every 6 turns (roughly 3 user, 3 assistant exchanges)
            if session["turns_since_last_note_update"] >= 6:
                session["turns_since_last_note_update"] = 0
                session["is_updating_note"] = True
                # Trigger the actual update in a separate task so we don't block
                asyncio.create_task(perform_realtime_note_update(session))

    async def perform_realtime_note_update(session: dict[str, Any]):
        try:
            topic_id = session.get("topic_id")
            course_id = session.get("course_id")
            # Get last 10 turns for context
            recent_turns = conversation_turns[-10:] if conversation_turns else []
            if len(recent_turns) < 4:
                return

            transcript = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in recent_turns)

            from google import genai

            client = genai.Client(api_key=_get_api_key())

            # Update database
            from src.core.database import db
            from src.models.notes import NoteCreate
            from src.services import note_service

            # Only touch the dedicated voice-insights note; never append to the user's personal note.
            note = await db.note.find_first(
                where={
                    "topicId": topic_id,
                    "userId": user_id,
                    "title": STUDY_VOICE_INSIGHTS_NOTE_TITLE,
                    "archived": False,
                }
            )

            existing_for_prompt = ""
            if note and (note.content or "").strip():
                ec = (note.content or "").strip()
                existing_for_prompt = ec[-14000:] if len(ec) > 14000 else ec

            prompt = (
                "You maintain ONE running study document for a live voice tutoring session on a single topic.\n"
                "The student may also keep a **personal** note on this topic — you must NOT assume you are editing that note.\n\n"
                "Below is (A) what is already captured in the dedicated AI insights note, then (B) a fresh transcript snippet.\n"
                "Your job: output **only** new material to **append** — well-structured Markdown (bullets with **bold** labels where "
                "helpful, short sub-bullets, avoid repeating the same opening sentence the summary already states).\n"
                "If a diagram, flowchart, timeline, or structure would help and is not already represented in (A), add **one** valid "
                "Mermaid block: opening line exactly ```mermaid then body then closing ``` on its own line. Keep Mermaid ≤ 25 lines.\n"
                "For flowchart/graph nodes, any label containing parentheses, brackets, colons, or slashes MUST use double-quoted text, "
                'e.g. A["Vector space V (set of vectors)"] — never A[Vector space V (set of vectors)].\n'
                "Do **not** output a heading like 'Insights from discussion' or '### Insights' — jump straight into bullets/paragraphs.\n"
                "If nothing materially new appears compared to (A), reply with exactly: NO_NEW_POINTS\n\n"
                f"--- Existing AI insights note (may be empty) ---\n{existing_for_prompt or '(empty)'}\n\n"
                f"--- New transcript ---\n{transcript}\n"
            )

            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )

            new_points = (response.text or "").strip()
            if not new_points or "NO_NEW_POINTS" in new_points:
                return

            if note:
                current_content = (note.content or "").rstrip()
                updated_content = f"{current_content}\n\n{new_points.strip()}"
                if len(updated_content) > 50000:
                    updated_content = updated_content[-50000:]

                updated_note = await db.note.update(
                    where={"id": note.id}, data={"content": updated_content}
                )
            else:
                topic = await db.topic.find_unique(where={"id": topic_id})
                body = _STUDY_VOICE_INSIGHTS_NOTE_INTRO + new_points.strip()
                note_data = NoteCreate(
                    title=STUDY_VOICE_INSIGHTS_NOTE_TITLE,
                    content=body,
                    topicId=topic_id,
                    courseId=course_id,
                )
                updated_note = await note_service.create_note(db, user_id, note_data)

            # Notify client
            await send_to_client(
                json.dumps(
                    {
                        "type": "note_updated",
                        "session_id": session_id,
                        "note_id": updated_note.id,
                        "content": updated_note.content,
                    }
                )
            )
            logger.info("Real-time note update completed for session %s", session_id)

        except Exception as e:
            logger.error("Failed to perform real-time note update: %s", e)
        finally:
            async with _sessions_lock:
                if session_id in _sessions:
                    _sessions[session_id]["is_updating_note"] = False

    async def handle_server_content(sc: dict[str, Any], ws_conn: Any = None) -> None:
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
                        await mark_ai_audio_activity()
                    except Exception as e:
                        logger.warning("Failed to decode audio from modelTurn: %s", e)
                fc_raw = part.get("functionCall") or part.get("function_call")
                if fc_raw and ws_conn:
                    fc = fc_raw if isinstance(fc_raw, dict) else {}
                    name = fc.get("name")
                    args = _normalize_live_tool_args(fc.get("args"))
                    call_id = fc.get("id")

                    logger.info(
                        "Received functionCall from Gemini: name=%s, id=%s, args=%s",
                        name,
                        call_id,
                        args,
                    )

                    import src.services.gemini_tool_handlers as tool_handlers

                    current_course_id = None
                    current_topic_id = None
                    async with _sessions_lock:
                        session_data = _sessions.get(session_id, {})
                        current_course_id = session_data.get("course_id")
                        current_topic_id = session_data.get("topic_id")

                    try:
                        logger.info(
                            "Executing tool %s with context courseId=%s, topicId=%s",
                            name,
                            current_course_id,
                            current_topic_id,
                        )
                        result = await tool_handlers.handle_tool_call(
                            name,
                            args,
                            user_id,
                            {"courseId": current_course_id, "topicId": current_topic_id},
                        )
                        logger.info("Tool %s executed successfully, result: %s", name, result)
                    except Exception as e:
                        logger.error("Error executing tool %s: %s", name, e, exc_info=True)
                        result = {"error": str(e)}

                    if result.get("action") == "navigate_next":
                        logger.info("Tool returned navigate_next, sending to client")
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "navigate_next_topic",
                                    "session_id": session_id,
                                }
                            )
                        )

                    if (
                        name == "study_show_visual"
                        and isinstance(result, dict)
                        and result.get("status") == "success"
                    ):
                        mermaid = str(args.get("mermaid") or "").strip()
                        display_math = str(args.get("display_math") or "").strip()
                        caption = str(args.get("caption") or "").strip()
                        if mermaid or display_math:
                            await send_to_client(
                                json.dumps(
                                    {
                                        "type": "study_visual",
                                        "session_id": session_id,
                                        "mermaid": mermaid,
                                        "display_math": display_math,
                                        "caption": caption,
                                    }
                                )
                            )

                    func_resp = {"name": name, "response": result}
                    if call_id:
                        func_resp["id"] = call_id

                    tool_resp = {"toolResponse": {"functionResponses": [func_resp]}}
                    await ws_conn.send(json.dumps(tool_resp))
                    logger.info("Sent toolResponse back to Gemini: %s", tool_resp)

    from src.services.gemini_tools import get_all_tools

    session_tools = tools if tools is not None else get_all_tools()

    setup: dict[str, Any] = {
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

    if session_tools:
        setup["setup"]["tools"] = session_tools

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
                    await handle_server_content(msg["serverContent"], ws_conn=ws)

            # Notify client that session is ready for audio; start time-based billing
            voice_billing_task: asyncio.Task[None] | None = None
            bm = "wall_clock"
            async with _sessions_lock:
                if session_id in _sessions:
                    _sessions[session_id]["voice_billing_started"] = True
                    _sessions[session_id][
                        "billing_tick_last_mono"
                    ] = asyncio.get_running_loop().time()
                    bm = str(_sessions[session_id].get("billing_mode", "wall_clock"))
            await send_to_client(
                json.dumps(
                    {
                        "type": "session_started",
                        "session_id": session_id,
                        "voice_billing_mode": bm,
                    }
                )
            )
            voice_billing_task = asyncio.create_task(voice_billing_loop())
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

            bridge_exit_reason: dict[str, str] = {"reason": "unknown"}

            async def from_client_to_gemini() -> None:
                try:
                    while True:
                        async with _sessions_lock:
                            sess = _sessions.get(session_id)
                            if sess and sess.get("force_disconnect"):
                                bridge_exit_reason["reason"] = "forced"
                                break

                        client_msg = await receive_from_client()
                        if client_msg is None:
                            bridge_exit_reason["reason"] = "client_stop"
                            break
                        if isinstance(client_msg, bytes):
                            b64 = base64.b64encode(client_msg).decode("ascii")
                            payload = {
                                "realtimeInput": {
                                    "audio": {"mimeType": "audio/pcm;rate=16000", "data": b64}
                                }
                            }
                            await ws.send(json.dumps(payload))
                            await mark_user_audio_activity()
                        elif isinstance(client_msg, str):
                            try:
                                msg_data = json.loads(client_msg)
                                if msg_data.get("type") == "client_message":
                                    payload = {
                                        "clientContent": {
                                            "turns": [
                                                {
                                                    "role": "user",
                                                    "parts": [{"text": msg_data.get("text")}],
                                                }
                                            ],
                                            "turnComplete": True,
                                        }
                                    }
                                    await ws.send(json.dumps(payload))
                            except Exception as e:
                                logger.warning("Failed to process client text message: %s", e)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception("Error forwarding client -> Gemini: %s", e)

            async def from_gemini_to_client() -> None:
                try:
                    while True:
                        async with _sessions_lock:
                            sess = _sessions.get(session_id)
                            if sess and sess.get("force_disconnect"):
                                break

                        raw = await ws.recv()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        msg = json.loads(raw)
                        if "serverContent" in msg:
                            await handle_server_content(msg["serverContent"], ws_conn=ws)
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
                # If the Gemini link or forwarder stops while the browser socket stays open, the client
                # would otherwise keep sending PCM with sessionReady=true into a dead consumer.
                if bridge_exit_reason.get("reason") != "client_stop":
                    try:
                        await send_to_client(
                            json.dumps(
                                {
                                    "type": "stopped",
                                    "session_id": session_id,
                                    "message": "Live voice session ended (model connection closed). Start Study again to reconnect.",
                                }
                            )
                        )
                    except Exception:
                        pass
                if voice_billing_task is not None:
                    voice_billing_task.cancel()
                    try:
                        await voice_billing_task
                    except asyncio.CancelledError:
                        pass
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
            consumed_pre_multiplier = 0
            billable = 0.0
            billing_started_flag = False
            bm = "wall_clock"
            async with _sessions_lock:
                sess = _sessions.get(session_id)
                if sess is not None:
                    consumed_pre_multiplier = int(sess.get("consumed_credits", 0) or 0)
                    billable = float(sess.get("billable_seconds", 0.0) or 0.0)
                    billing_started_flag = bool(sess.get("voice_billing_started"))
                    bm = str(sess.get("billing_mode", "wall_clock"))
            on_done(consumed_pre_multiplier, billable, billing_started_flag, bm)


def _extract_json_object_from_model_text(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}\s*$", t)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


async def generate_study_diagram_for_topic(
    user_id: str,
    *,
    topic_id: str,
    topic_title: str | None,
    course_title: str | None,
    hint: str | None,
    transcript_tail: str | None,
) -> dict[str, str]:
    """Use the text Gemini model to produce Mermaid / display math for Study Mode (REST fallback)."""
    from src.core.database import db
    from google import genai

    topic = await db.topic.find_unique(
        where={"id": topic_id},
        include={"module": {"include": {"course": True}}},
    )
    course = topic.module.course if topic and topic.module else None
    if not topic or not course or course.userId != user_id:
        raise ValueError("Topic not found")

    notes_list = await db.note.find_many(
        where={"topicId": topic_id, "userId": user_id},
        order={"updatedAt": "asc"},
    )
    parts = [(n.content or "").strip() for n in notes_list if n.content]
    note_blob = "\n\n---\n\n".join(parts) if parts else "(no notes yet)"
    if len(note_blob) > 8000:
        note_blob = note_blob[-8000:]

    tt = topic_title or getattr(topic, "title", None) or "Topic"
    ct = course_title or getattr(course, "title", None) or "Course"
    hint_text = (
        hint or ""
    ).strip() or "The main idea the student is trying to understand right now."
    tail = (transcript_tail or "").strip()
    if len(tail) > 6000:
        tail = tail[-6000:]

    prompt = (
        "You help visualize ideas for a live voice study session.\n"
        "Return ONLY a single JSON object (no markdown fences around the JSON, no other text) with keys:\n"
        '- "mermaid": string, valid Mermaid diagram body WITHOUT triple-backtick fences. '
        "Examples: start with flowchart TD, graph LR, sequenceDiagram, or mindmap. Keep it ≤ 30 lines. "
        "For flowchart/graph, any node label containing parentheses MUST be double-quoted inside the brackets, "
        'e.g. A["V (vectors)"] never A[V (vectors)]. '
        'Use "" if a diagram is not appropriate.\n'
        '- "display_math": string, LaTeX for ONE display equation without $ or $$ delimiters, or "".\n'
        '- "caption": string, one short line, or "".\n'
        "At least one of mermaid or display_math must be non-empty.\n\n"
        f"Course: {ct}\nTopic: {tt}\n\nRelevant notes:\n{note_blob}\n\n"
        f"Recent voice transcript (may be fragmented):\n{tail or '(none)'}\n\n"
        f"What to illustrate: {hint_text}\n"
    )

    api_key = _get_api_key()
    if not api_key:
        raise ValueError("Gemini API key not configured")

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    raw_text = (response.text or "").strip()
    obj = _extract_json_object_from_model_text(raw_text)
    if not obj:
        raise ValueError("Model did not return valid JSON")
    mermaid = str(obj.get("mermaid") or "").strip()
    display_math = str(obj.get("display_math") or "").strip()
    caption = str(obj.get("caption") or "").strip()
    if not mermaid and not display_math:
        raise ValueError("Model returned empty diagram")
    return {
        "mermaid": mermaid,
        "display_math": display_math,
        "caption": caption,
    }


async def post_gemini_live_session(
    user_id: str,
    session_id: str,
    conversation_turns: list[dict[str, str]],
    topic_id: str | None,
    course_id: str | None,
    *,
    credits_already_consumed: int = 0,
    billable_seconds: float = 0.0,
    billing_started: bool = False,
    billing_mode: str = "wall_clock",
) -> None:
    """
    Run after a Gemini Live session ends: settle remaining credits (non-blocking) and optionally
    create a structured note from the conversation. Does not affect session latency.
    Voice billing uses billable time (wall-clock for FREE, audio-active windows for paid tiers).
    """
    try:
        from src.core.database import db
        from src.services.credit_service import consume_credits
        from src.utils.exceptions import SubscriptionLimitError

        user = await db.user.find_unique(where={"id": user_id})
        if user and billing_started:
            credits_total = voice_credits_total_final_settlement(billable_seconds, billing_mode)
            already = max(0, int(credits_already_consumed))
            to_consume = max(0, credits_total - already)

            if to_consume > 0:
                await consume_credits(user, to_consume, operation="gemini_live_voice")
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
            model="gemini-2.5-flash",
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
