"""Where a voice session lives between `POST /conversation/start` and the socket that uses it.

A voice session is created by an HTTP request and consumed moments later by a WebSocket `start_session`
frame. Those are two separate requests, and with more than one uvicorn worker they are very likely two
separate processes. The original implementation kept them in a module-level `dict`, which meant the socket
could only ever find a session created by the same worker — silently returning "Session not found" the rest
of the time. Porting is the cheapest moment to fix that, so the record now lives in Redis.

## Two keys, not a scan

- `study_voice:session:<session_id>` — the record.
- `study_voice:user:<user_id>` — the learner's current session id.

The second exists because both things this store is asked for are questions about a *learner*: "does this
learner already have a session" (start closes it) and "list this learner's sessions". The original answered
both by iterating every session in the dict, which is not something you can do against a cache without
`SCAN`. One learner has at most one session, so a single pointer answers both exactly.

## What is deliberately *not* here

The billing counters, audio timestamps and disconnect flag the original kept alongside these fields. Those
change several times a second — `mark_user_audio_activity` fires per audio frame — and they belong to one
running bridge in one process. Writing them here would mean a network round trip per 20ms of speech to
share state nobody else reads. They live in `bridge.BridgeState` instead, which is also why the
`asyncio.Lock` the original needed around every access is gone: that state is now owned by a single
coroutine tree rather than shared through a global.

## When Redis is down

`Cache` degrades to no-ops by design, so a Redis outage would make every session vanish the instant it was
written. Rather than let voice fail with a lie ("Session not found"), this falls back to an in-process dict
and logs it. Single-worker development, where Redis often is not running, therefore works unchanged; a
multi-worker deployment that loses Redis degrades to "voice only works when both requests land on the same
worker", which is the original behaviour and is at least reported.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from src.shared.infrastructure.redis import cache

logger = logging.getLogger(__name__)

#: A record is only needed for the seconds between `start` and the socket, but a learner may take a while
#: to grant microphone permission, and a session that outlives its socket is harmless. An hour is generous
#: and still bounds the leak from a client that starts a session and never connects.
SESSION_TTL_SECONDS = 3600

_SESSION_KEY = "study_voice:session"
_USER_KEY = "study_voice:user"

# Fallback store, used only when Redis is unavailable. See the module docstring.
_local_sessions: dict[str, dict[str, Any]] = {}
_local_user_session: dict[str, str] = {}


@dataclass(slots=True)
class VoiceSession:
    """One pending or running voice session.

    `system_instruction` is the composed tutor brief, not anything the client sent — see `context.py`.
    """

    session_id: str
    user_id: str
    system_instruction: str
    course_id: str | None = None
    topic_id: str | None = None
    chat_session_id: str | None = None
    study_session_id: str | None = None
    created_at: str = ""
    #: The note this session has already been saved to, if the learner asked for one.
    #:
    #: Here rather than derived from the topic, because "the note for this conversation" is not the same as
    #: "a note on this topic" — the learner may have several — and pressing save twice should rewrite one
    #: note rather than leave two accounts of the same sitting.
    note_id: str | None = None
    #: Whether the learner has asked for this conversation to become a note.
    #:
    #: Off by default, which is the whole consent design: with this false the transcript is still buffered in
    #: memory to run the session, and nothing is ever written from it. Turning it on is the learner saying
    #: "keep this and write it up", and it is what makes the note at teardown legitimate rather than the
    #: unasked-for automatic writer this module was built to remove.
    note_taking: bool = False
    #: Turn count at the last note write, so a regeneration with nothing new to say can be refused.
    #:
    #: `has_enough_for_a_note` counts the whole buffer, so without this marker a second write after two
    #: silent minutes would re-run the model over the same conversation and charge for it again.
    turns_at_last_note: int = 0
    #: Whether the wall-clock session minimum has already been charged for this session.
    #:
    #: The web client retries a dropped socket up to five times, re-sending `start_session` with the *same*
    #: id each time. Each attempt is a separate relay with its own settlement, so a FREE learner on a flaky
    #: connection would pay `GEMINI_LIVE_MIN_SESSION_CREDITS` once per attempt — five times the floor for one
    #: sitting they experienced as one sitting. The floor belongs to the session, not to the socket.
    floor_settled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceSession:
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        known["session_id"] = str(known.get("session_id") or "")
        known["user_id"] = str(known.get("user_id") or "")
        known["system_instruction"] = str(known.get("system_instruction") or "")
        known["created_at"] = str(known.get("created_at") or "")
        known["floor_settled"] = bool(known.get("floor_settled"))
        known["note_taking"] = bool(known.get("note_taking"))
        # Coerced rather than trusted: a record written before this field existed has no value for it, and
        # `None` would fail the comparison in `notes.py` rather than reading as "no note yet".
        known["turns_at_last_note"] = int(known.get("turns_at_last_note") or 0)
        return cls(**known)


def _session_key(session_id: str) -> str:
    return cache.make_key([_SESSION_KEY, session_id])


def _user_key(user_id: str) -> str:
    return cache.make_key([_USER_KEY, user_id])


async def create(
    user_id: str,
    *,
    system_instruction: str,
    course_id: str | None = None,
    topic_id: str | None = None,
    chat_session_id: str | None = None,
    study_session_id: str | None = None,
) -> VoiceSession:
    """Open a session for a learner, closing any session they already had.

    One at a time, as before. A learner with two live sessions would be billed for both while only
    hearing one, and the client keeps a single shared socket anyway, so the second could never be heard.
    """
    session = VoiceSession(
        session_id=str(uuid.uuid4()),
        user_id=user_id,
        system_instruction=system_instruction,
        course_id=course_id,
        topic_id=topic_id,
        chat_session_id=chat_session_id,
        study_session_id=study_session_id,
        created_at=datetime.now(UTC).isoformat(),
    )

    previous = await _current_session_id(user_id)
    if previous and previous != session.session_id:
        logger.info("Replacing previous voice session %s for user %s", previous, user_id)
        await _forget(previous)

    if cache.is_connected:
        await cache.set(
            _session_key(session.session_id), session.to_dict(), expire=SESSION_TTL_SECONDS
        )
        await cache.set(_user_key(user_id), session.session_id, expire=SESSION_TTL_SECONDS)
    else:
        logger.warning(
            "Redis unavailable — holding voice session %s in process memory. The socket must land on "
            "this worker for the session to be found.",
            session.session_id,
        )
        _local_sessions[session.session_id] = session.to_dict()
        _local_user_session[user_id] = session.session_id

    return session


async def get(session_id: str) -> VoiceSession | None:
    """The record, or None if it never existed, expired, or was stopped."""
    if cache.is_connected:
        raw = await cache.get(_session_key(session_id))
        if isinstance(raw, dict):
            return VoiceSession.from_dict(raw)
    raw_local = _local_sessions.get(session_id)
    return VoiceSession.from_dict(raw_local) if raw_local else None


async def delete(session_id: str) -> None:
    """Stop a session. Clears the learner's pointer too, when it points here."""
    await _forget(session_id)


async def list_for_user(user_id: str) -> list[VoiceSession]:
    """The learner's live session, as a list because the contract returns one."""
    session_id = await _current_session_id(user_id)
    if not session_id:
        return []
    session = await get(session_id)
    return [session] if session else []


async def update_context(
    session_id: str, *, topic_id: str | None = None, course_id: str | None = None
) -> VoiceSession | None:
    """Follow the learner to a new topic mid-session.

    Only overwrites what was supplied: a frame carrying a topic but no course must not blank the course.
    Note this does **not** rebuild the brief — the provider was given its system instruction at setup and
    there is no way to replace it on a live connection. The new ids affect tool dispatch, which is what
    the frame is for; a genuinely different topic needs a new session.
    """
    session = await get(session_id)
    if not session:
        return None
    if topic_id:
        session.topic_id = topic_id
    if course_id:
        session.course_id = course_id
    await _persist(session)
    return session


async def remember_note(session_id: str, note_id: str, *, turns: int = 0) -> None:
    """Tie a note to this session, and record how much conversation it already covers.

    `turns` is the transcript length at the moment of writing. It is what lets a later write tell "there is
    more to say" from "nothing has happened since", which matters once a note is also written at teardown:
    a learner who wrote one manually and then stopped talking should not pay for an identical second pass.
    """
    session = await get(session_id)
    if session is None:
        return
    session.note_id = note_id
    session.turns_at_last_note = turns
    await _persist(session)


async def set_note_taking(session_id: str, enabled: bool) -> VoiceSession | None:
    """Turn note-taking on or off for this session.

    Returns the updated record, or `None` when the session is gone — the caller reports that rather than
    silently succeeding, because a learner who pressed Take note and got no acknowledgement will assume it
    is on and expect a note that was never going to be written.
    """
    session = await get(session_id)
    if session is None:
        return None
    session.note_taking = enabled
    await _persist(session)
    return session


async def claim_session_floor(session_id: str) -> bool:
    """Claim the right to charge the session minimum, returning False if it is already claimed.

    Called once per settlement. A session the record has outlived — expired, or stopped before the relay
    unwound — returns True, because charging the floor for a session we cannot prove was already charged is
    the same decision the original made for every session, and undercharging a sitting we have no record of
    would be exploitable by simply calling stop first.
    """
    session = await get(session_id)
    if session is None:
        return True
    if session.floor_settled:
        return False
    session.floor_settled = True
    await _persist(session)
    return True


async def _persist(session: VoiceSession) -> None:
    """Write a changed record back to wherever it came from."""
    if cache.is_connected:
        await cache.set(
            _session_key(session.session_id), session.to_dict(), expire=SESSION_TTL_SECONDS
        )
    if session.session_id in _local_sessions:
        _local_sessions[session.session_id] = session.to_dict()


async def _current_session_id(user_id: str) -> str | None:
    if cache.is_connected:
        raw = await cache.get(_user_key(user_id))
        if isinstance(raw, str) and raw:
            return raw
    return _local_user_session.get(user_id)


async def _forget(session_id: str) -> None:
    session = await get(session_id)
    if cache.is_connected:
        await cache.delete(_session_key(session_id))
        if session and await _current_session_id(session.user_id) == session_id:
            await cache.delete(_user_key(session.user_id))
    _local_sessions.pop(session_id, None)
    if session and _local_user_session.get(session.user_id) == session_id:
        _local_user_session.pop(session.user_id, None)
