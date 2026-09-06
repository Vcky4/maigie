"""The relay: learner's socket ⇄ our process ⇄ the provider's socket.

Ported from `run_gemini_live_bridge` at `4953972^`. The protocol handling, barge-in forwarding, tool
dispatch and billing arithmetic are the original behaviour. What changed:

- **Session state is a local dataclass, not a global dict behind a lock.** The billing counters and audio
  timestamps belong to one bridge in one process; the original shared them through a module-level dict, which
  forced an `asyncio.Lock` around every read. `BridgeState` is owned by this coroutine tree, so the lock is
  gone. It is safe without one: every mutation here is a read-modify-write with no `await` in the middle, and
  the event loop cannot interleave those.
- **Exhaustion is now a value, not an exception.** This used to catch `SubscriptionLimitError` from
  `consume_credits`, which was itself a fix for an earlier version that matched a *string* code
  (`"SUBSCRIPTION_LIMIT_EXCEEDED"` against an actual `"SUBSCRIPTION_LIMIT"`) and therefore matched
  nothing, billing a learner out of credits onwards in silence. Voice draws from its own balance now
  (§6.3) and `voice_service.spend` does not raise — the provider minutes are already spent by the time
  it runs — so `charge` ends the session by *reading* a zero balance. One less thing that can rot: a
  value cannot be compared against the wrong constant.
- **No tools means no tools.** The original fell back to the *entire* agentic toolset when the caller passed
  `None`, so a session opened without a topic could create courses and delete notes with no client handling
  for any of it. `None` now means none.
- **Nothing about the conversation is written anywhere.** The original accumulated turns and, every six of
  them, sent them to a model and appended the result to the learner's topic note — automatically, with no
  action from them and no way to decline. Turns are still collected here, into `SessionTranscript`, but that
  is a bounded in-memory buffer that dies with the socket. It is written to a `Note` only when the learner
  asks, through the `save_note` frame. See `transcript.py` and `notes.py`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import websockets

from src.config import get_settings
from src.domains.billing.services import voice_service
from src.domains.identity.repository import IdentityRepository
from src.domains.intelligence.action.skills.registry import skill_registry
from src.domains.knowledge.services import illustration_service
from src.shared.exceptions import SubscriptionLimitError

from . import session_store
from .billing import (
    BILLING_WALL_CLOCK,
    abandoned_after_seconds,
    billing_flush_interval_seconds,
    billing_min_consume_seconds,
    billing_mode_for_tier,
    billing_tick_seconds,
    chargeable_seconds_raw,
    standby_idle_seconds,
)
from .transcript import SessionTranscript

logger = logging.getLogger(__name__)

GEMINI_LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

#: Nudges the model to open the conversation instead of waiting for the learner to speak first. Sent as a
#: user turn, which is why `handle_server_content` filters it back out of the transcript: the learner never
#: said it and seeing it in their own transcript is the model appearing to put words in their mouth.
DEFAULT_GREETING_PROMPT = (
    "Start with a brief, warm greeting and immediately begin discussing the topic. "
    "Keep it concise - no more than two sentences."
)

#: Provider frames carrying audio are small, but a single `serverContent` can batch several. The library
#: default of 1 MiB would drop the connection mid-sentence if one ever exceeded it, and the failure would
#: look like the model hanging up.
_MAX_PROVIDER_FRAME_BYTES = 8 * 1024 * 1024

SendToClient = Callable[[str | bytes], Coroutine[Any, Any, None]]
ReceiveFromClient = Callable[[], Coroutine[Any, Any, "str | bytes | None"]]


@dataclass(slots=True)
class BillingSnapshot:
    """What the settlement needs to know once the session is over."""

    billing_mode: str
    billing_started: bool
    billable_seconds: float
    charged_seconds: int


@dataclass(slots=True)
class BridgeState:
    """Everything one running session tracks. Local to this bridge — see the module docstring."""

    billing_mode: str
    billing_started: bool = False
    #: Seconds that count as billable under `billing_mode`, not seconds connected.
    billable_seconds: float = 0.0
    #: Seconds already taken off the voice balance, so accrual never charges the same second twice.
    #:
    #: Was `consumed_credits`, holding units. Voice draws from `voiceSecondsRemaining` now (§6.3), so
    #: the accumulator, the accrual and the balance are all in one denomination and there is no rate
    #: between them — see `billing.py` for why the absence of that rate is the point.
    charged_seconds: int = 0
    last_user_audio_mono: float | None = None
    last_ai_audio_mono: float | None = None
    tick_last_mono: float | None = None
    last_flush_mono: float | None = None
    #: Set when the learner runs out of credits. Both forwarders check it and unwind.
    force_disconnect: bool = False
    #: Held while a charge is in flight, so a slow database write cannot be charged twice by the next tick.
    is_charging: bool = False

    def snapshot(self) -> BillingSnapshot:
        return BillingSnapshot(
            billing_mode=self.billing_mode,
            billing_started=self.billing_started,
            billable_seconds=self.billable_seconds,
            charged_seconds=self.charged_seconds,
        )


async def run_bridge(
    session_id: str,
    user_id: str,
    tier: str | None,
    *,
    send_to_client: SendToClient,
    receive_from_client: ReceiveFromClient,
    system_instruction: str,
    tools: list[dict[str, Any]] | None = None,
    transcript: SessionTranscript | None = None,
    on_done: Callable[[BillingSnapshot], Any] | None = None,
) -> None:
    """Relay one voice session until either side hangs up.

    `transcript` is owned by the caller, not by this bridge, because the socket handler outlives it and is
    what serves a `save_note` request. Passing None means the conversation is forwarded and not held at all.

    `on_done` always runs, including on the error and cancellation paths, because it carries the billing
    snapshot: a session that crashed still consumed provider time and the learner still has to be charged
    for it. Skipping settlement on failure would make crashing the cheapest way to study.
    """
    settings = get_settings()
    api_key = (settings.GEMINI_API_KEY or "").strip()
    state = BridgeState(billing_mode=billing_mode_for_tier(tier))

    if not api_key:
        await _send_error(
            send_to_client, session_id, "Voice study is not configured on this server."
        )
        logger.error("GEMINI_API_KEY is not set — voice study cannot start")
        if on_done:
            on_done(state.snapshot())
        return

    model = (settings.GEMINI_LIVE_MODEL or "").strip() or "models/gemini-3.1-flash-live-preview"
    greeting = settings.GEMINI_LIVE_GREETING_PROMPT
    greeting_prompt = (DEFAULT_GREETING_PROMPT if greeting is None else greeting).strip()

    identity_repo = IdentityRepository()

    # ------------------------------------------------------------------ billing

    async def charge(amount: int) -> None:
        """Take `amount` seconds off the voice balance, and end the session once it is spent.

        **Seconds against `voiceSecondsRemaining`, not units against the usage window** (§6.3). Voice
        used to compete with text for one allowance at a 40× cost ratio, which meant the allowance had
        to be priced for the voice case and was spent almost entirely on the text case.

        `voice_service.spend` does not raise when the balance runs out — the provider minutes are
        already used by the time this runs, so there is nothing left to refuse. What ends the session is
        this function noticing an empty balance afterwards, which is the same posture as `record_units`:
        charge on spend, and let the *next* moment be the one that stops.
        """
        if amount <= 0:
            return
        try:
            balance = await voice_service.spend(user_id, amount)
            state.charged_seconds += amount
        except Exception as exc:
            # Not fatal: the seconds stay in `billable_seconds` and the final settlement will pick them up.
            logger.warning("Voice charge failed for session %s: %s", session_id, exc)
            return

        if balance.total_seconds > 0:
            return

        # Out of voice. Unlike the old credit refusal this is not an exception path — the balance simply
        # reached zero — so the message is composed here rather than carried on one.
        logger.info(
            "Voice session %s ending — user %s has no voice balance left",
            session_id,
            user_id,
        )
        state.force_disconnect = True
        await _send_json(
            send_to_client,
            {
                # Kept as `credit_limit_error` for the client contract even though credits are gone,
                # because renaming it is a client change and this is a server one. Phase 7 renames both
                # together; changing it here alone would break the handler that shows the message.
                "type": "credit_limit_error",
                "session_id": session_id,
                # No `windowResetsAt`, and its absence is correct rather than an omission: the usage
                # window refills in five hours and has nothing to do with voice. A voice balance refills
                # when the subscription period turns over, or when the learner buys `plus_voice_30`,
                # and telling them to wait five hours would be advice that does not work.
                "message": (
                    "You've used your live voice minutes. They refill when your plan renews, "
                    "or you can add 30 minutes."
                ),
                "tier": str(tier or "FREE"),
                "windowResetsAt": None,
            },
        )

    async def billing_loop() -> None:
        """Accrue billable time and flush it to the ledger in batches.

        Batched because the tick is two seconds: charging on every tick would mean a credit write every two
        seconds per active session. A charge goes out once the unbilled amount is worth writing, or once the
        flush interval has passed, whichever comes first — so a quiet session still settles periodically
        instead of accumulating an unbilled hour.
        """
        tick = billing_tick_seconds()
        idle_gap = standby_idle_seconds()
        abandoned_gap = abandoned_after_seconds()
        min_chunk = billing_min_consume_seconds()
        flush_interval = billing_flush_interval_seconds()
        loop = asyncio.get_running_loop()

        try:
            while True:
                await asyncio.sleep(tick)
                if state.force_disconnect:
                    break

                now = loop.time()
                if state.tick_last_mono is None:
                    state.tick_last_mono = now
                    continue
                delta = max(0.0, now - state.tick_last_mono)
                state.tick_last_mono = now

                # Abandonment. Reusing this loop because it is the only thing already ticking, and adding a
                # second timer to watch the first one's data would be two clocks disagreeing about when the
                # session ended.
                #
                # Two things depend on a session actually ending. A FREE learner is billed wall-clock, so an
                # empty room costs them by the minute. And the end-of-session note is written at teardown, so
                # a learner who switched note-taking on, talked for half an hour and then walked away without
                # closing the tab would never get the note they asked for. Ending on silence is also simply
                # true: a voice session with nobody in it is over.
                #
                # `force_disconnect` rather than an exception: it is the same signal the credit-exhaustion
                # path uses, so the relay unwinds through its own `finally` and settles normally.
                last_speech = max(
                    state.last_user_audio_mono or 0.0, state.last_ai_audio_mono or 0.0
                )
                if last_speech and (now - last_speech) >= abandoned_gap:
                    logger.info(
                        "Voice session %s abandoned after %.0fs of silence — ending it",
                        session_id,
                        now - last_speech,
                    )
                    state.force_disconnect = True
                    await _send_json(
                        send_to_client,
                        {
                            "type": "stopped",
                            "session_id": session_id,
                            "reason": "abandoned",
                            "message": "Voice study ended because the room went quiet.",
                        },
                    )
                    break

                if state.billing_mode == BILLING_WALL_CLOCK:
                    bill_delta = delta
                else:
                    recent = (
                        state.last_user_audio_mono is not None
                        and (now - state.last_user_audio_mono) <= idle_gap
                    ) or (
                        state.last_ai_audio_mono is not None
                        and (now - state.last_ai_audio_mono) <= idle_gap
                    )
                    bill_delta = delta if recent else 0.0
                state.billable_seconds += bill_delta

                if state.last_flush_mono is None:
                    state.last_flush_mono = now

                owed = chargeable_seconds_raw(state.billable_seconds) - state.charged_seconds
                due = owed > 0 and (
                    owed >= min_chunk or (now - state.last_flush_mono) >= flush_interval
                )
                if due and not state.is_charging:
                    state.is_charging = True
                    try:
                        await charge(owed)
                    finally:
                        state.is_charging = False
                        state.last_flush_mono = loop.time()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------- provider → client

    async def handle_server_content(content: dict[str, Any], provider_ws: Any) -> None:
        if content.get("interrupted"):
            # Barge-in. The learner spoke over the tutor and the provider abandoned the rest of the turn;
            # the client has to drop whatever audio it still has queued or it will play a reply to
            # something the learner already interrupted.
            await _send_json(send_to_client, {"type": "interrupted", "session_id": session_id})

        transcription = content.get("inputTranscription") or {}
        if text := transcription.get("text"):
            # The greeting nudge was sent as a user turn, so it comes back as one. Filtering it keeps the
            # learner from seeing an instruction they never spoke attributed to them.
            if not (greeting_prompt and text.strip() == greeting_prompt):
                if transcript is not None:
                    transcript.add("user", text)
                await _send_json(
                    send_to_client,
                    {"type": "transcription", "session_id": session_id, "text": text},
                )

        output = content.get("outputTranscription") or {}
        if text := output.get("text"):
            if transcript is not None:
                transcript.add("assistant", text)
            await _send_json(
                send_to_client,
                {"type": "assistant_message", "session_id": session_id, "text": text},
            )

        model_turn = content.get("modelTurn")
        if not isinstance(model_turn, dict):
            return
        for part in model_turn.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if text := part.get("text"):
                if transcript is not None:
                    transcript.add("assistant", text)
                await _send_json(
                    send_to_client,
                    {
                        "type": "assistant_message",
                        "session_id": session_id,
                        "text": text,
                    },
                )
            inline = part.get("inlineData") or {}
            if data := inline.get("data"):
                try:
                    await send_to_client(base64.b64decode(data))
                    state.last_ai_audio_mono = asyncio.get_running_loop().time()
                except Exception as exc:
                    logger.warning("Could not decode provider audio: %s", exc)
            call = part.get("functionCall") or part.get("function_call")
            if isinstance(call, dict):
                await dispatch_tool(call, provider_ws)

    async def dispatch_tool(call: dict[str, Any], provider_ws: Any) -> None:
        """Run a tool the model asked for, tell the client what happened, tell the model it landed.

        The response always goes back to the provider, including on failure. A tool call left unanswered
        leaves the model waiting mid-turn, so the learner hears the tutor stop talking for no reason.
        """
        name = call.get("name")
        args = _tool_args(call.get("args"))
        call_id = call.get("id")

        session = await session_store.get(session_id)
        context = {
            "courseId": session.course_id if session else None,
            "topicId": session.topic_id if session else None,
        }

        result = await skill_registry.execute_tool(str(name), args, user_id, context)
        logger.info("Voice tool %s for session %s returned %s", name, session_id, result)

        if isinstance(result, dict) and result.get("action") == "navigate_next":
            await _send_json(
                send_to_client,
                {"type": "navigate_next_topic", "session_id": session_id},
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
                topic_id = context.get("topicId")
                # Kept as well as sent. This visual used to exist only in the frame below: the client put
                # it in an in-memory map that nothing read, so the tutor announced a diagram, the learner
                # saw nothing, and a reload lost it regardless.
                #
                # `record` cannot raise — a storage failure must not break a turn the model is mid-way
                # through, and the learner has the diagram either way. `None` when there is no topic open,
                # since an illustration with nothing to illustrate has nowhere to live.
                illustration_id = (
                    await illustration_service.record(
                        user_id,
                        topic_id=str(topic_id),
                        mermaid=mermaid,
                        display_math=display_math,
                        caption=caption,
                        # The model chose to draw this. The learner pressing "Diagram" is the other source.
                        source=illustration_service.SOURCE_TUTOR,
                    )
                    if topic_id
                    else None
                )
                await _send_json(
                    send_to_client,
                    {
                        "type": "study_visual",
                        "session_id": session_id,
                        "mermaid": mermaid,
                        "display_math": display_math,
                        "caption": caption,
                        # Sent so the client can offer to remove this one without refetching the list.
                        # Null means it was shown but not kept, which is a real state rather than an error.
                        "illustration_id": illustration_id,
                    },
                )

        response: dict[str, Any] = {"name": name, "response": result}
        if call_id:
            response["id"] = call_id
        await provider_ws.send(json.dumps({"toolResponse": {"functionResponses": [response]}}))

    # ------------------------------------------------------------------ the run

    setup: dict[str, Any] = {
        "setup": {
            "model": model,
            "generationConfig": {"responseModalities": ["AUDIO"]},
            # Both directions transcribed: the learner reads what the tutor said, and the tutor's
            # understanding of what *they* said is visible to them rather than guessed at.
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "systemInstruction": {"parts": [{"text": system_instruction}]},
        }
    }
    if tools:
        setup["setup"]["tools"] = [_camel_case_tool_group(group) for group in tools]

    billing_task: asyncio.Task[None] | None = None
    try:
        async with websockets.connect(
            f"{GEMINI_LIVE_WS_URL}?key={api_key}", max_size=_MAX_PROVIDER_FRAME_BYTES
        ) as provider_ws:
            await provider_ws.send(json.dumps(setup))
            while True:
                message = _decode(await provider_ws.recv())
                if "setupComplete" in message:
                    break
                if "serverContent" in message:
                    await handle_server_content(message["serverContent"], provider_ws)

            # Billing starts here and not at `start_session`: before `setupComplete` there is no session to
            # charge for, and a provider that never completes setup must not produce a bill.
            state.billing_started = True
            state.tick_last_mono = asyncio.get_running_loop().time()
            await _send_json(
                send_to_client,
                {
                    "type": "session_started",
                    "session_id": session_id,
                    "voice_billing_mode": state.billing_mode,
                },
            )
            billing_task = asyncio.create_task(billing_loop())

            if greeting_prompt:
                await provider_ws.send(json.dumps(_text_turn(greeting_prompt)))

            exit_reason = {"reason": "unknown"}

            async def client_to_provider() -> None:
                try:
                    while True:
                        if state.force_disconnect:
                            exit_reason["reason"] = "credits"
                            break
                        message = await receive_from_client()
                        if message is None:
                            exit_reason["reason"] = "client_stop"
                            break
                        if isinstance(message, bytes):
                            await provider_ws.send(
                                json.dumps(
                                    {
                                        "realtimeInput": {
                                            "audio": {
                                                "mimeType": "audio/pcm;rate=16000",
                                                "data": base64.b64encode(message).decode("ascii"),
                                            }
                                        }
                                    }
                                )
                            )
                            state.last_user_audio_mono = asyncio.get_running_loop().time()
                        elif isinstance(message, str):
                            try:
                                payload = json.loads(message)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "Ignoring unparseable client frame on %s",
                                    session_id,
                                )
                                continue
                            if payload.get("type") == "client_message" and payload.get("text"):
                                await provider_ws.send(json.dumps(_text_turn(str(payload["text"]))))
                except asyncio.CancelledError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    exit_reason["reason"] = "provider_closed"
                except Exception as exc:
                    logger.exception(
                        "Client → provider forwarding failed on %s: %s", session_id, exc
                    )

            async def provider_to_client() -> None:
                try:
                    while not state.force_disconnect:
                        message = _decode(await provider_ws.recv())
                        if "serverContent" in message:
                            await handle_server_content(message["serverContent"], provider_ws)
                except asyncio.CancelledError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    return
                except Exception as exc:
                    logger.exception(
                        "Provider → client forwarding failed on %s: %s", session_id, exc
                    )

            receiver = asyncio.create_task(provider_to_client())
            try:
                await client_to_provider()
            finally:
                # The learner's socket may still be open with the microphone live. Unless they were the one
                # who stopped, they have to be told the far end is gone, or the client keeps streaming audio
                # into a closed relay and shows a session that looks healthy.
                if exit_reason["reason"] != "client_stop":
                    await _send_json(
                        send_to_client,
                        {
                            "type": "stopped",
                            "session_id": session_id,
                            "message": "Voice study ended because the connection to the tutor closed. "
                            "Start Study again to reconnect.",
                        },
                    )
                await _cancel(receiver)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Voice bridge failed on session %s: %s", session_id, exc)
        await _send_error(send_to_client, session_id, "Voice study stopped unexpectedly.")
    finally:
        await _cancel(billing_task)
        if on_done:
            on_done(state.snapshot())


# --------------------------------------------------------------------- helpers


def study_tools_for(topic_id: str | None) -> list[dict[str, Any]] | None:
    """The tool set a voice session gets: the study tools with a topic, nothing without one.

    Only the study category, not every registered tool. Native audio cannot speak a diagram, so
    `study_show_visual` pushes one to the screen, and `complete_topic_and_continue` lets the learner finish
    a topic by saying so. The rest of the agentic toolset — creating courses, deleting notes — has no client
    handling on this socket, and a tutor that silently created a course mid-conversation would be worse than
    one that could not.
    """
    return skill_registry.get_study_tools_legacy_format() if topic_id else None


def _tool_args(raw: Any) -> dict[str, Any]:
    """Arguments arrive as an object or as a JSON string depending on the model's mood."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _camel_case_tool_group(group: dict[str, Any]) -> dict[str, Any]:
    """The registry emits `function_declarations`; the raw provider socket wants `functionDeclarations`."""
    if "function_declarations" in group:
        return {"functionDeclarations": group["function_declarations"]}
    return group


def _text_turn(text: str) -> dict[str, Any]:
    return {
        "clientContent": {
            "turns": [{"role": "user", "parts": [{"text": text}]}],
            "turnComplete": True,
        }
    }


def _decode(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8")
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


async def _send_json(send: SendToClient, payload: dict[str, Any]) -> None:
    await send(json.dumps(payload))


async def _send_error(send: SendToClient, session_id: str, message: str) -> None:
    await _send_json(send, {"type": "error", "session_id": session_id, "message": message})


async def _cancel(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
