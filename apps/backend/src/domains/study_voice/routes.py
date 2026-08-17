"""HTTP and WebSocket surface for voice study.

Five REST routes and one socket, at the paths the two shipped clients already call. Ported from
`src/routes/gemini_live.py` at `4953972^`.

## The socket is the whole feature

`POST /conversation/start` only composes the brief and issues an id. Nothing is connected, nothing is
charged, and no provider socket exists until the client opens `/ws` and sends `start_session`. That split is
what lets the browser hold one shared socket across screens while sessions come and go on it.

## What is deliberately not here

Anything that writes a transcript down on its own. The deleted implementation summarised the conversation
into a `Note` every six fragments and again at the end, automatically. What replaces it is the `save_note`
frame: the learner presses a button, the note is written from the buffer we were already holding to run the
session, and that is the only path. Credit settlement, which lived in the same deleted function, **is**
ported — see `settlement.py`.

## Why saving a note is a frame and not an HTTP route

The conversation exists only in the memory of the process running the relay (`transcript.py`). An HTTP request
could land on any worker, so a `POST` would only work if the transcript were stored somewhere shared — which
is the exact thing we are not doing. A frame on the session's own socket necessarily arrives at the process
holding the buffer. The restriction is real and worth naming: the note can only be saved **while the session
is open**. Once the learner hangs up, there is nothing left to write from, which is the point.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from src.domains.billing.services.credit_consumption_service import (
    CREDIT_COSTS,
    check_credit_availability,
    consume_credits,
)
from src.domains.identity.db_models import User
from src.domains.identity.repository import IdentityRepository
from src.shared.auth import CurrentUser
from src.shared.auth.jwt import decode_access_token
from src.shared.exceptions import MaigieError, SubscriptionLimitError

from . import bridge, context, diagram, notes, session_store, settlement
from .billing import min_session_credits
from .models import (
    ConversationListResponse,
    ConversationStatusResponse,
    ConversationSummary,
    StartConversationRequest,
    StartConversationResponse,
    StopConversationResponse,
    StudyDiagramRequest,
    StudyDiagramResponse,
)
from .transcript import SessionTranscript

logger = logging.getLogger(__name__)

router = APIRouter(tags=["study-voice"])


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


@router.post("/study/diagram", response_model=StudyDiagramResponse)
async def study_diagram(
    current_user: CurrentUser, body: StudyDiagramRequest
) -> StudyDiagramResponse:
    """Draw what the learner asked to see. Uses a text model, not the live audio one."""
    cost = CREDIT_COSTS.get("study_diagram", 80)
    available, message = await check_credit_availability(current_user, cost)
    if not available:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=message or "Not enough credits for a diagram.",
        )

    result = await diagram.generate_for_topic(
        current_user.id,
        topic_id=body.topic_id,
        topic_title=body.topic_title,
        course_title=body.course_title,
        hint=body.hint,
        transcript_tail=body.transcript_tail,
    )

    # Charged after the diagram exists, not before. A generation that failed is not something to bill for,
    # and the learner would have no diagram to show for the credits.
    try:
        await consume_credits(current_user, cost, operation="study_diagram")
    except SubscriptionLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=getattr(exc, "detail", None) or str(exc),
        ) from exc

    return StudyDiagramResponse(**result)


@router.post("/conversation/start", response_model=StartConversationResponse)
async def start_conversation(
    current_user: CurrentUser,
    request: StartConversationRequest | None = None,
) -> StartConversationResponse:
    """Compose the tutor brief and issue a session id for the socket to use.

    Any session the learner already had is closed here — one at a time, because they have one voice and one
    shared socket, and a second live session would bill for audio nobody can hear.
    """
    body = request or StartConversationRequest()
    system_instruction = await context.build_brief(
        current_user.id, course_id=body.course_id, topic_id=body.topic_id
    )
    session = await session_store.create(
        current_user.id,
        system_instruction=system_instruction,
        course_id=body.course_id,
        topic_id=body.topic_id,
    )
    return StartConversationResponse(
        session_id=session.session_id,
        status="active",
        course_id=session.course_id,
        topic_id=session.topic_id,
    )


@router.post("/conversation/{session_id}/stop", response_model=StopConversationResponse)
async def stop_conversation(session_id: str, current_user: CurrentUser) -> StopConversationResponse:
    """Forget a session. The socket, if one is running, ends when the client stops sending."""
    await _owned_session(session_id, current_user.id)
    await session_store.delete(session_id)
    return StopConversationResponse(session_id=session_id, status="stopped")


@router.get("/conversation/{session_id}/status", response_model=ConversationStatusResponse)
async def conversation_status(
    session_id: str, current_user: CurrentUser
) -> ConversationStatusResponse:
    """Whether the session record still exists.

    This reports on the *record*, not on the provider connection — the relay holding that socket may be on
    another worker. A client that needs to know whether audio is flowing has `session_started` and `stopped`
    frames on its own socket, which are the only authority on that.
    """
    await _owned_session(session_id, current_user.id)
    return ConversationStatusResponse(
        session_id=session_id, status="active", user_id=current_user.id
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(current_user: CurrentUser) -> ConversationListResponse:
    """The learner's current session, if they have one. At most one, by design."""
    sessions = await session_store.list_for_user(current_user.id)
    return ConversationListResponse(
        sessions=[ConversationSummary(session_id=s.session_id, status="active") for s in sessions]
    )


async def _owned_session(session_id: str, user_id: str) -> Any:
    session = await session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.user_id != user_id:
        # 404, not 403: confirming that someone else's session id exists is information the caller has no
        # business having.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


async def _user_from_token(token: str) -> User | None:
    """Resolve the query-string token to a user, or None.

    A token in the query string is not good practice — it lands in access logs — but browsers cannot set
    headers on a WebSocket handshake and both clients already do this. The design document proposes a
    short-lived ticket handshake instead; that is a coordinated client change, not a port decision.
    """
    try:
        payload = decode_access_token(token)
    except JWTError:
        return None
    email = payload.get("sub")
    if not email:
        return None
    user = await IdentityRepository().find_by_email(str(email))
    if user is None or not user.is_active:
        return None
    return user


@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket, token: str = Query(...)) -> None:
    """One socket per learner, carrying one session at a time.

    Frames in: `start_session`, `stop`, `ping`, `update_context`, `client_message`, `save_note`, and raw
    binary PCM.
    Frames out: `session_started`, `transcription`, `assistant_message`, `interrupted`, `study_visual`,
    `navigate_next_topic`, `note_saved`, `note_error`, `credit_limit_error`, `stopped`, `error`, `pong`, and
    raw binary audio.
    """
    user = await _user_from_token(token)
    if user is None:
        # Closed before `accept`, so the client sees a policy violation rather than an open socket that
        # silently ignores everything it sends.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # The bridge pulls from this queue instead of reading the socket directly, because this loop owns the
    # socket: it has to keep handling `stop` and `ping` while the bridge is running.
    client_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
    bridge_task: asyncio.Task[None] | None = None
    active_session_id: str | None = None
    # The conversation, in memory, for as long as this socket is open. Owned here rather than by the bridge
    # because this loop is what serves `save_note`, and because it must be discarded when the socket closes
    # even if the bridge ended some other way.
    transcript = SessionTranscript()

    async def send_to_client(message: str | bytes) -> None:
        try:
            if isinstance(message, str):
                await websocket.send_text(message)
            else:
                await websocket.send_bytes(message)
        except RuntimeError as exc:
            # Raised when the socket has already closed. Expected whenever a learner closes the tab
            # mid-sentence, so it is not worth a stack trace.
            if "close" in str(exc).lower() or "disconnect" in str(exc).lower():
                return
            logger.warning("Failed to send to voice client: %s", exc)
        except Exception as exc:
            logger.warning("Failed to send to voice client: %s", exc)

    def on_bridge_done(snapshot: bridge.BillingSnapshot) -> None:
        """Settle the bill without holding the socket open for it.

        The session record is deliberately left in place. The client reconnects by re-sending
        `start_session` with the same id, so deleting it here would turn every dropped connection into a
        session the learner cannot resume.
        """
        nonlocal bridge_task
        bridge_task = None
        session_id = active_session_id
        if session_id:
            asyncio.create_task(settlement.settle(user.id, session_id, snapshot))

    async def start(session_id: str) -> None:
        nonlocal bridge_task, active_session_id

        if bridge_task is not None and not bridge_task.done():
            await _error(
                send_to_client, session_id, "A voice session is already running on this connection"
            )
            return

        session = await session_store.get(session_id)
        if not session:
            await _error(send_to_client, session_id, "Session not found")
            return
        if session.user_id != user.id:
            await _error(send_to_client, session_id, "Session not found")
            return

        # Refused before a provider socket is opened. Starting a call we cannot bill for would burn provider
        # minutes and end abruptly a few seconds later.
        available, _message = await check_credit_availability(user, min_session_credits())
        if not available:
            await _error(
                send_to_client,
                session_id,
                "Not enough credits for voice study. Top up or wait for your limit to reset.",
            )
            return

        active_session_id = session_id
        # A reconnect re-enters the same session id, and the buffer from the dropped attempt is still here.
        # Kept rather than cleared: the learner had one conversation and a note should cover all of it.
        bridge_task = asyncio.create_task(
            bridge.run_bridge(
                session_id,
                user.id,
                user.tier,
                send_to_client=send_to_client,
                receive_from_client=client_queue.get,
                system_instruction=session.system_instruction,
                tools=bridge.study_tools_for(session.topic_id),
                transcript=transcript,
                on_done=on_bridge_done,
            )
        )

    async def save_note(session_id: str) -> None:
        """Write this session's note, because the learner asked for it.

        Failures come back as `note_error` rather than `error`, so the client can report them against the
        button that caused them instead of showing a panel that reads as though the session died.
        """
        session = await session_store.get(session_id)
        if not session or session.user_id != user.id:
            await _send(
                send_to_client,
                {
                    "type": "note_error",
                    "session_id": session_id,
                    "message": "That session is no longer available.",
                },
            )
            return
        try:
            saved = await notes.save_session_note(user, session, transcript)
        except MaigieError as exc:
            await _send(
                send_to_client,
                {
                    "type": "note_error",
                    "session_id": session_id,
                    "message": getattr(exc, "detail", None) or str(exc),
                },
            )
            return
        except Exception:
            logger.exception("Failed to write a session note for user %s", user.id)
            await _send(
                send_to_client,
                {
                    "type": "note_error",
                    "session_id": session_id,
                    "message": "The note could not be written. Try again in a moment.",
                },
            )
            return

        await _send(send_to_client, {"type": "note_saved", "session_id": session_id, **saved})

    try:
        while True:
            try:
                raw = await websocket.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError as exc:
                if "disconnect" in str(exc).lower() or "close" in str(exc).lower():
                    break
                raise

            if raw.get("type") == "websocket.disconnect":
                # Mobile closes the socket without sending `stop`, so this is the normal exit there. The
                # bill is still settled: the `finally` below cancels the bridge, which snapshots on its way
                # out.
                break

            if raw.get("bytes"):
                await client_queue.put(raw["bytes"])
                continue

            text = raw.get("text")
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Ignoring unparseable frame on voice socket for user %s", user.id)
                continue

            message_type = data.get("type")
            session_id = data.get("session_id")

            if message_type == "start_session" and session_id:
                await start(str(session_id))
            elif message_type == "save_note":
                # Not awaited inline: writing a note is an LLM call taking seconds, and this loop is what
                # forwards the learner's audio. Blocking here would mute them while they waited.
                asyncio.create_task(save_note(str(session_id or active_session_id or "")))
            elif message_type == "stop" and session_id:
                # `None` is the bridge's stop signal: it unwinds its own forwarders and settles, rather than
                # being cancelled from outside mid-charge.
                await client_queue.put(None)
                await send_to_client(json.dumps({"type": "stopped", "session_id": session_id}))
            elif message_type == "ping":
                await send_to_client(json.dumps({"type": "pong", "session_id": session_id}))
            elif (
                message_type == "update_context" and session_id == active_session_id and session_id
            ):
                await session_store.update_context(
                    str(session_id),
                    topic_id=data.get("topic_id"),
                    course_id=data.get("course_id"),
                )
            elif message_type == "client_message":
                await client_queue.put(text)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Voice socket failed for user %s: %s", user.id, exc)
        await _error(send_to_client, active_session_id or "", "Voice study stopped unexpectedly.")
    finally:
        if bridge_task is not None and not bridge_task.done():
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass
        # The conversation goes no further than this process. Explicit rather than left to the garbage
        # collector, because a pending task holding a reference is exactly how "we do not keep transcripts"
        # becomes untrue in practice.
        transcript.clear()
        try:
            await websocket.close()
        except Exception:
            pass


async def _send(send: Any, payload: dict[str, Any]) -> None:
    await send(json.dumps(payload))


async def _error(send: Any, session_id: str, message: str) -> None:
    await _send(send, {"type": "error", "session_id": session_id, "message": message})
