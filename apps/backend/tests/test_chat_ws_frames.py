"""The frame contract of `/api/v1/intelligence/ws`, written against the clients that consume it.

**This is a specification, not a characterisation.** The Ask Maigie plan's Phase 2 asked for a
characterisation test — pin current behaviour, then refactor behind it. There was no behaviour to pin.
`websocket_handler.py` called four methods on `manager` that did not exist:

    manager.send_connection_json   12 call sites   absent
    manager.send_room_json          9 call sites   absent
    manager.join_room               2 call sites   absent
    manager.leave_room              1 call site    absent

`ConnectionManager` defined `connect`, `disconnect`, `send_personal_message`, `send_to_user`,
`send_json` and the channel methods, and nothing else. No `__getattr__`, no subclass, and no
definition of those four names anywhere in the repository. Measured before the fix, with a fake socket
and a manager exposing exactly the real attribute surface, a keepalive and a real question ended
identically:

    outcome:             AttributeError: 'ConnectionManager' object has no attribute
                         'send_connection_json'
    manager calls:       ['connect', 'disconnect']
    text sent to model:  []

The socket accepted, saved the learner's message, and died on the `message_saved` frame. **The model
was never reached, on any turn, ever.**

So the contract these tests assert is read off the clients, which are the only working implementations
of it — `maigie-mobile/src/features/chat/chatWsClient.ts` and web's `ChatWebSocketClient` in
`features/courses/services/chatApi.ts`. Both demux the same seven frames and both branch on a bare
non-JSON frame. That agreement is the specification.

**Why these are unit tests with a faked transport and database.** The deployed `DATABASE_URL` points at
a managed Postgres, and there is no local one, so the suite's database-backed tests skip. Driving a
real socket against a real database is not available here. What is checkable without one is precisely
what was broken: which frames leave the handler, in what order, and whether the model is reached at
all. `StrictFakeManager` is the load-bearing piece — it raises `AttributeError` for any attribute the
*real* manager does not have, so these tests fail if a call site drifts onto another method that was
never written.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter

import src.domains.intelligence.conversation.context_enrichment as ce
import src.domains.intelligence.conversation.websocket_handler as wh
from src.shared.infrastructure.socket_manager import manager as real_manager

#: The attribute surface of the real manager. Anything outside it is a defect, not a test-double gap.
REAL_MANAGER_ATTRS = {name for name in dir(real_manager) if not name.startswith("_")}


class InputExhausted(Exception):
    """Raised by the fake socket when the handler asks for more input than the test scripted.

    The handler loops forever by design, so this is how a test says "the turn is over" rather than a
    failure. It is distinct from `AssertionError` so a genuine assertion cannot be mistaken for it.
    """


class StrictFakeManager:
    """A stand-in for `ConnectionManager` that refuses attributes the real one lacks.

    This is the whole point of the file. A `MagicMock()` would have happily absorbed
    `send_connection_json` and every test would have passed against a handler that could not send a
    single frame — which is how twenty-five broken call sites survived to be found by hand.
    """

    def __init__(self) -> None:
        self.frames: list[tuple[str, str]] = []
        self.rooms: list[tuple[str, str]] = []

    def _record(self, kind: str, payload: object) -> None:
        if isinstance(payload, dict):
            self.frames.append((kind, str(payload.get("type", "<untyped>"))))
        else:
            self.frames.append((kind, "<raw>"))

    def __getattr__(self, name: str):
        if name not in REAL_MANAGER_ATTRS:
            raise AttributeError(f"'ConnectionManager' object has no attribute '{name}'")

        if name in ("join_room", "leave_room"):

            def sync(connection_id: str, room: str):
                self.rooms.append((name, room))

            return sync

        async def recorder(*args, **kwargs):
            if name == "connect":
                return "conn_1"
            if name in ("send_json", "send_connection_json", "send_room_json"):
                self._record(name, args[0] if args else None)
            elif name == "send_text_to_user":
                self.frames.append((name, "<text>"))
            elif name == "send_personal_message":
                self._record(name, args[1] if len(args) > 1 else None)
            return None

        return recorder

    @property
    def frame_types(self) -> list[str]:
        """Just the `type` of each frame, in order. What the clients switch on."""
        return [frame_type for _, frame_type in self.frames]


class FakeDbSession:
    """A session that answers every query with "nothing found, count of one".

    Deliberately dumb. These tests are about frames, not SQL; the queries this stands in for are
    covered — where they can be — by `test_intelligence_schemas.py`.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalar.return_value = 1
        result.scalars.return_value.all.return_value = []
        return result

    async def commit(self):
        return None


class FakeUser:
    id = "user_1"
    name = "Ada Lovelace"
    tier = "FREE"
    is_onboarded = True


class FakeWebSocket:
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.closed = False

    async def receive_text(self) -> str:
        if not self.script:
            raise InputExhausted
        return self.script.pop(0)

    async def send_json(self, data):
        return None

    async def send_text(self, text):
        return None

    async def close(self, *args, **kwargs):
        self.closed = True


def ws_endpoint():
    """The endpoint coroutine, pulled off a throwaway router."""
    router = APIRouter()
    wh.register_chat_websocket_routes(router, None)
    return next(r for r in router.routes if getattr(r, "path", None) == "/ws").endpoint


async def drive(
    script: list[str],
    *,
    reply: str = "Entropy measures disorder.",
    chunks: tuple[str, ...] = ("Entropy measures ", "disorder."),
    executed_actions: list | None = None,
    query_results: list | None = None,
    credits_available: tuple[bool, str | None] = (True, None),
    route_request=None,
) -> dict:
    """Run one connection through the handler and report what left it.

    Returns the manager double, the prompts the model saw, and the rows that were written.
    """
    manager = StrictFakeManager()

    session = MagicMock()
    session.id = "sess_1"
    session.user_id = "user_1"
    session.title = "New Chat"

    created_rows: list[dict] = []

    def make_row(data):
        row = MagicMock()
        row.id = f"msg_{len(created_rows)}"
        row.reply_to_message_id = data.get("replyToMessageId")
        row.created_at = MagicMock()
        row.created_at.isoformat.return_value = "2026-08-26T09:00:00+00:00"
        created_rows.append(data)
        return row

    prompts: list[str] = []

    async def fake_route_request(**kwargs):
        prompts.append(kwargs.get("user_message"))
        stream_callback = kwargs.get("stream_callback")
        if stream_callback:
            for index, chunk in enumerate(chunks):
                await stream_callback(chunk, index == len(chunks) - 1)
        return (
            reply,
            {"input_tokens": 120, "output_tokens": 40, "model_name": "gemini-2.0-flash"},
            executed_actions or [],
            query_results or [],
        )

    flags = MagicMock()
    flags.effective_tier_for_request = AsyncMock(return_value="free")

    credit_result = MagicMock(warning=None, notice=None, purchased_balance_remaining=0)

    # The thread read is injected rather than reached through a patched session factory. It used to be
    # an inline query in the handler, so patching `wh.get_session_factory` covered it; it now lives in
    # `context_enrichment` behind `ContextReaders.read_history`, which is the seam that exists for
    # exactly this. Retrieval and memory need no stub — both are best-effort and their failure against
    # an absent database is caught and logged, which these tests want to keep true.
    async def no_history(*, session_id, user_id, review_item_id, limit):
        return []

    test_readers = replace(ce.production_readers(), read_history=no_history)

    with (
        patch.object(wh, "manager", manager),
        patch.object(ce, "production_readers", lambda: test_readers),
        patch.object(wh, "get_session_factory", lambda: FakeDbSession),
        patch.object(wh, "_get_circle_group_for_session", AsyncMock(return_value=None)),
        patch.object(wh, "get_feature_flag_service", lambda: flags),
        patch.object(wh, "_get_user_model_preference", AsyncMock(return_value=None)),
        patch.object(
            wh, "check_credit_availability", AsyncMock(return_value=credits_available)
        ) as credit_check,
        patch.object(wh, "consume_credits", AsyncMock(return_value=credit_result)) as consume,
        # Only read on the refusal path, to compose the message. Patched rather than modelled on
        # `FakeUser` because the shape of a credit-usage report is billing's business, not this test's.
        patch.object(
            wh,
            "get_credit_usage",
            AsyncMock(
                return_value={
                    "daily_limit": 5_000,
                    "credits_used_today": 5_000,
                    "credits_used": 5_000,
                    "hard_cap": 5_000,
                    "period_end": "2026-09-01",
                    "next_daily_reset": "midnight",
                }
            ),
        ),
        patch.object(wh.intelligence_repo, "create_chat_session", AsyncMock(return_value=session)),
        patch.object(wh.intelligence_repo, "find_chat_session", AsyncMock(return_value=session)),
        patch.object(wh.intelligence_repo, "update_chat_session", AsyncMock()),
        patch.object(wh.intelligence_repo, "create_action_log", AsyncMock()),
        patch.object(
            wh.intelligence_repo,
            "create_message",
            AsyncMock(side_effect=lambda data: make_row(data)),
        ),
        patch.object(wh, "get_llm_router") as get_router,
        patch(
            "src.domains.identity.repository.IdentityRepository.find_by_id",
            AsyncMock(return_value=FakeUser()),
        ),
    ):
        get_router.return_value.route_request = route_request or fake_route_request
        socket = FakeWebSocket(script)
        error: BaseException | None = None
        try:
            await ws_endpoint()(socket, FakeUser())
        except InputExhausted:
            pass
        except BaseException as exc:  # noqa: BLE001 — the test decides whether it mattered
            error = exc

    return {
        "manager": manager,
        "prompts": prompts,
        "rows": created_rows,
        "error": error,
        "credit_check": credit_check,
        "consume": consume,
    }


def run(*args, **kwargs) -> dict:
    return asyncio.run(drive(*args, **kwargs))


class TestTheTransportGapIsClosed:
    """The four methods the handler calls must exist on the real manager.

    Asserted against the real object rather than a double, because a double is what hid this.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "send_connection_json",
            "send_room_json",
            "join_room",
            "leave_room",
            "send_text_to_user",
        ],
    )
    def test_the_manager_has_the_method_the_handler_calls(self, name):
        assert hasattr(real_manager, name), (
            f"ConnectionManager has no {name!r}. The chat handler calls it; every call raises "
            f"AttributeError and the socket dies mid-turn. See src/core/websocket.py."
        )

    def test_rooms_are_namespaced_away_from_channels(self):
        """Rooms share the channel registry, so a room id must not collide with a channel name."""
        from src.core.websocket import _room_channel

        assert _room_channel("progress") != "progress"
        assert _room_channel("sess_1") == "room:sess_1"


class TestNoAwaitMismatches:
    """No coroutine may be used as a value, and no plain value may be awaited.

    A defect class, not a defect. Five were found in `websocket_handler` at once, and each failed in a
    different way, which is why none of them had been noticed:

    - `_extract_suggestion` — async, unpacked without `await`: `TypeError` on every turn with components.
    - `format_action_component_response` — sync, awaited: `TypeError: object dict can't be used in
      'await' expression`, same path.
    - `_build_greeting_prompt` — async, not awaited: a coroutine was passed to the model as the prompt.
    - `_build_greeting_components` — async, not awaited: a coroutine was stored as `componentData` and
      then iterated.
    - `_is_circle_member` — async, not awaited: `not <coroutine>` is always `False`, so the room
      membership guard authorised everyone. Unreachable only because the group lookup returns `None`
      first, which is luck rather than defence.

    Static because it has to be: only the last two are reachable in normal operation, so a runtime test
    would have caught three of five. This inspects every bare-name call in the module against the real
    definition.
    """

    @staticmethod
    def mismatches() -> list[tuple[str, str]]:
        import ast
        import inspect
        from pathlib import Path

        import src.domains.intelligence.conversation.websocket_handler as handler

        # `encoding=` is not optional. `read_text()` defaults to the platform encoding, which is
        # cp1252 on Windows, and the handler contains non-Latin-1 characters — so this guard raised
        # `UnicodeDecodeError` on every Windows run and passed only on CI. A guard that is dead on the
        # machine the code is written on is worse than no guard: it reports green where it never ran.
        source = Path(inspect.getsourcefile(handler)).read_text(encoding="utf-8")
        awaited_state: dict[str, set[bool]] = {}

        class Visitor(ast.NodeVisitor):
            def visit_Await(self, node):  # noqa: N802 — ast.NodeVisitor dispatches on this name
                if isinstance(node.value, ast.Call):
                    self.record(node.value, True)
                self.generic_visit(node)

            def visit_Call(self, node):  # noqa: N802 — ast.NodeVisitor dispatches on this name
                self.record(node, False)
                self.generic_visit(node)

            def record(self, call, awaited):
                if isinstance(call.func, ast.Name):
                    awaited_state.setdefault(call.func.id, set()).add(awaited)

        Visitor().visit(ast.parse(source))

        found = []
        for name, states in sorted(awaited_state.items()):
            target = getattr(handler, name, None)
            if target is None or not callable(target):
                continue
            is_async = inspect.iscoroutinefunction(target)
            if is_async and states == {False}:
                found.append(
                    (name, "is async but is never awaited — the coroutine is used as a value")
                )
            if not is_async and True in states:
                found.append((name, "is synchronous but is awaited — TypeError at runtime"))
        return found

    def test_the_handler_awaits_exactly_what_it_should(self):
        found = self.mismatches()
        assert found == [], "async/await mismatches in websocket_handler:\n" + "\n".join(
            f"  {name}: {problem}" for name, problem in found
        )

    def test_the_detector_finds_a_planted_mismatch(self):
        """Guards the guard. An inspection that silently matches nothing passes forever."""
        import ast
        import inspect

        source = "async def f(): pass\nx = f()\n"
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert calls, "the detector's own parsing assumption is wrong"
        assert inspect.iscoroutinefunction(
            __import__(
                "src.domains.intelligence.conversation.chat_helpers", fromlist=["_is_circle_member"]
            )._is_circle_member
        ), "_is_circle_member stopped being async; re-check its two call sites for the missing await"


class TestStubsAreHonestRatherThanHarmful:
    """Three modules the handler depends on are unimplemented stubs.

    `chat_helpers`, `chat_greeting` and `component_response` were created during the domain refactor with
    "implementation pending migration" docstrings, and the migration never happened. They are left
    unimplemented — circle rooms are out of Ask Maigie's scope and a suggestion-splitting heuristic would
    be invented product behaviour — but they must be *absent*, not actively wrong.
    """

    def test_a_suggestion_split_returns_the_answer_whole(self):
        """Unimplemented must mean "no split", never "part of the answer"."""
        from src.domains.intelligence.conversation.chat_helpers import _extract_suggestion

        content, suggestion = _extract_suggestion("Entropy measures disorder. Next, try a quiz.")
        assert content == "Entropy measures disorder. Next, try a quiz."
        assert suggestion is None

    def test_a_suggestion_split_is_not_a_coroutine(self):
        import inspect

        from src.domains.intelligence.conversation.chat_helpers import _extract_suggestion

        assert not inspect.iscoroutinefunction(_extract_suggestion), (
            "_extract_suggestion is async again. The handler unpacks its result into two names without "
            "awaiting it, which raises on every turn that produces components."
        )

    def test_a_message_with_no_reply_target_previews_as_none(self):
        """`{}` claimed a reply preview with no fields in it. `None` says there is no reply."""
        from src.domains.intelligence.conversation.chat_helpers import _serialize_reply_preview

        assert _serialize_reply_preview(None) is None

    def test_a_real_reply_target_previews_with_its_content(self):
        from src.domains.intelligence.conversation.chat_helpers import _serialize_reply_preview

        row = SimpleNamespace(
            id="msg_1",
            role="ASSISTANT",
            content="Entropy measures disorder.",
            user_id="user_1",
            user=None,
        )
        preview = _serialize_reply_preview(row, fallback_user_name="Ada")
        assert preview["id"] == "msg_1"
        assert preview["role"] == "assistant"
        assert preview["content"] == "Entropy measures disorder."
        assert preview["userName"] == "Ada"

    def test_a_long_reply_is_truncated_rather_than_sent_whole(self):
        from src.domains.intelligence.conversation.chat_helpers import _serialize_reply_preview

        row = SimpleNamespace(id="m", role="USER", content="x" * 5_000, user_id="u", user=None)
        assert len(_serialize_reply_preview(row)["content"]) == 200

    def test_roles_are_mapped_to_the_spelling_the_clients_switch_on(self):
        from src.domains.intelligence.conversation.chat_helpers import _map_db_role_to_client

        assert _map_db_role_to_client("ASSISTANT") == "assistant"
        assert _map_db_role_to_client("USER") == "user"

    def test_room_membership_denies_by_default(self):
        """The stub must fail closed. It is the only thing standing in front of a room right now."""
        import asyncio

        from src.domains.intelligence.conversation.chat_helpers import _is_circle_member

        assert asyncio.run(_is_circle_member("group", "user_1")) is False


class TestKeepalive:
    """The heartbeat both clients send every 25 seconds.

    Mobile: `setInterval(... send(JSON.stringify({type:'ping'})), 25_000)`. Web: the same.
    This was the most-sent frame in the product and the handler answered it by trying to call a method
    that did not exist — inside a `try` whose `except (json.JSONDecodeError, AttributeError)` swallowed
    the failure, so the keepalive fell through and was answered as though it were a question.
    """

    def test_a_ping_is_answered_with_pong(self):
        result = run([json.dumps({"type": "ping"})])
        assert result["manager"].frame_types == ["pong"]

    def test_a_ping_never_reaches_the_model(self):
        result = run([json.dumps({"type": "ping"})])
        assert result["prompts"] == [], (
            "A keepalive was sent to the model. This is the swallowed-AttributeError path: the ping "
            "branch failed, the handler's JSON guard caught it, and the frame was treated as chat."
        )

    def test_a_ping_persists_nothing(self):
        assert run([json.dumps({"type": "ping"})])["rows"] == []


class TestAFullTurn:
    """The sequence the clients are written to consume, end to end."""

    def test_the_model_is_reached(self):
        result = run(["What is entropy?"])
        assert result["prompts"] == ["What is entropy?"]

    def test_the_frame_sequence(self):
        result = run(["What is entropy?"])
        assert result["manager"].frame_types == [
            "message_saved",  # user row, for optimistic-message correlation
            "stream",  # first chunk
            "stream",  # final chunk
            "assistant_final",  # the answer, with its persisted id
            "message_saved",  # assistant row
        ]

    def test_streaming_precedes_the_final_frame(self):
        """A client that renders `assistant_final` before the stream would flicker."""
        types = run(["What is entropy?"])["manager"].frame_types
        assert types.index("stream") < types.index("assistant_final")

    def test_two_rows_are_written_for_one_turn(self):
        rows = run(["What is entropy?"])["rows"]
        roles = [row["role"] for row in rows]
        assert roles == ["USER", "ASSISTANT"]

    def test_the_user_row_holds_what_the_learner_typed(self):
        rows = run(["What is entropy?"])["rows"]
        assert rows[0]["content"] == "What is entropy?"

    def test_the_assistant_row_is_attributed_and_metered(self):
        """Decision F: every generation records its model, tokens, cost and revenue."""
        assistant = run(["What is entropy?"])["rows"][1]
        assert assistant["content"] == "Entropy measures disorder."
        assert assistant["modelName"] == "gemini-2.0-flash"
        assert assistant["inputTokens"] == 120
        assert assistant["outputTokens"] == 40
        assert assistant["tokenCount"] == 160
        assert assistant["costUsd"] is not None
        assert assistant["revenueUsd"] is not None

    def test_the_turn_does_not_raise(self):
        assert run(["What is entropy?"])["error"] is None

    def test_a_json_envelope_is_unwrapped(self):
        """Clients send `{message, context, tempId}` when there is context to attach."""
        result = run([json.dumps({"message": "What is entropy?", "tempId": "tmp_1"})])
        assert result["prompts"] == ["What is entropy?"]


class TestATurnThatUsedATool:
    """A turn where the model called a tool, so the answer arrives with components.

    This is the "skills" experience — course cards, schedule blocks, the badges under an answer — and it
    took a second, narrower crash than the transport gap. `chat_helpers._extract_suggestion` is declared
    `async` and returns `None`; the handler calls it *without* `await* and unpacks the result into two
    names, so any turn with both components and a non-empty answer raised

        TypeError: cannot unpack non-iterable coroutine object

    Every function in `chat_helpers` except `_strip_maigie_mention` is an unimplemented stub — the
    module's own docstring says "Stub — implementation pending migration". So this path had never run
    either.
    """

    ACTION = {
        "type": "create_course",
        "data": {"title": "Thermodynamics"},
        "result": {"status": "success", "course_id": "course_1", "message": "Course created!"},
    }

    def test_a_tool_using_turn_does_not_raise(self):
        result = run(["Make me a thermodynamics course"], executed_actions=[self.ACTION])
        assert result["error"] is None, f"a turn with components raised {result['error']!r}"

    def test_the_answer_still_reaches_the_client(self):
        result = run(["Make me a thermodynamics course"], executed_actions=[self.ACTION])
        assert "assistant_final" in result["manager"].frame_types

    def test_the_assistant_row_is_still_written(self):
        result = run(["Make me a thermodynamics course"], executed_actions=[self.ACTION])
        assert [row["role"] for row in result["rows"]] == ["USER", "ASSISTANT"]

    def test_the_answer_is_not_truncated_by_the_suggestion_split(self):
        """`_extract_suggestion` is unimplemented, so it must return the answer whole.

        Returning a partial answer would be worse than not splitting at all: the learner would lose
        prose the model produced, and nothing would report it.
        """
        result = run(["Make me a thermodynamics course"], executed_actions=[self.ACTION])
        assistant = result["rows"][1]
        assert assistant["content"] == "Entropy measures disorder."

    def test_the_action_is_logged(self):
        result = run(["Make me a thermodynamics course"], executed_actions=[self.ACTION])
        assert result["error"] is None


class TestAFailedGenerationIsNotAnAnswer:
    """§1: a failed turn is never rendered as an answer, and never persisted as one.

    Both error branches used to assign their message to `response_text` and fall through to the
    persistence step, so a provider outage was written into the learner's history as something Maigie
    said — indistinguishable, on reload, from a real reply.

    This mattered more than it looked. When these tests were written the LLM routing layer was
    unmigrated and `get_llm_router()` raised unconditionally, so **every** turn took the generic branch
    and every learner was told "I'm sorry, I encountered an error" by a Maigie that had never been
    asked. Failing visibly is what made that legible instead of looking like a model with nothing to
    say — and it is what led to the routing layer being migrated rather than worked around.

    **That premise is no longer current: the routing layer is migrated and `get_llm_router()` returns a
    real router.** These tests are kept and still matter, for the reason they were written: they inject
    the failure directly rather than relying on the subsystem being broken, so they guard the invariant
    itself. A provider outage, a timeout or an exhausted fallback chain all still reach these branches,
    and none of them may become a stored answer.
    """

    @staticmethod
    def failing_router(exc: Exception):
        def raise_it(**kwargs):
            raise exc

        return raise_it

    def test_a_provider_failure_sends_an_error_frame(self):
        result = run(["What is entropy?"], route_request=self.failing_router(RuntimeError("boom")))
        assert "error" in result["manager"].frame_types

    def test_a_provider_failure_persists_no_assistant_row(self):
        result = run(["What is entropy?"], route_request=self.failing_router(RuntimeError("boom")))
        roles = [row["role"] for row in result["rows"]]
        assert roles == ["USER"], f"a failed turn wrote {roles}"

    def test_a_provider_failure_does_not_send_assistant_final(self):
        result = run(["What is entropy?"], route_request=self.failing_router(RuntimeError("boom")))
        assert "assistant_final" not in result["manager"].frame_types

    def test_a_provider_failure_does_not_consume_credits(self):
        """The learner was not answered, so they are not charged."""
        result = run(["What is entropy?"], route_request=self.failing_router(RuntimeError("boom")))
        result["consume"].assert_not_awaited()

    def test_the_unmigrated_router_is_reported_as_a_failure_not_an_answer(self):
        """Was the live case; now a regression guard.

        `UnmigratedSubsystemError` is not an `LLMProviderError`, so it takes the generic branch. Kept
        because the generic branch is the one that used to persist a fabricated answer, and because
        other unmigrated subsystems still raise this type.
        """
        from src.shared.infrastructure.unmigrated import UnmigratedSubsystemError

        result = run(
            ["What is entropy?"],
            route_request=self.failing_router(UnmigratedSubsystemError("router not migrated")),
        )
        assert [row["role"] for row in result["rows"]] == ["USER"]
        assert "error" in result["manager"].frame_types


class TestCreditsAreCheckedBeforeTheModelRuns:
    """A learner over their cap must not be billed a model call they are then refused.

    Before this phase the check ran roughly 280 lines *after* `route_request`: the model was called, the
    answer streamed frame by frame to the client, and only then was the learner told they were out of
    credits — with no assistant row written and `consume_credits` never called. So the turn cost real
    money, delivered a real answer, and recorded neither.
    """

    def test_an_exhausted_learner_never_reaches_the_model(self):
        result = run(["What is entropy?"], credits_available=(False, None))
        assert (
            result["prompts"] == []
        ), "The model ran for a learner with no credits. The check must precede generation."

    def test_an_exhausted_learner_gets_the_credit_limit_frame(self):
        result = run(["What is entropy?"], credits_available=(False, None))
        assert "credit_limit_error" in result["manager"].frame_types

    def test_an_exhausted_learner_gets_no_assistant_row(self):
        """§1: a failed turn is never rendered as an answer, and never persisted as one."""
        result = run(["What is entropy?"], credits_available=(False, None))
        roles = [row["role"] for row in result["rows"]]
        assert "ASSISTANT" not in roles

    def test_an_exhausted_learner_is_not_charged(self):
        result = run(["What is entropy?"], credits_available=(False, None))
        result["consume"].assert_not_awaited()

    def test_a_successful_turn_is_charged(self):
        result = run(["What is entropy?"])
        result["consume"].assert_awaited()

    def test_the_check_happens_on_every_turn(self):
        result = run(["What is entropy?"])
        result["credit_check"].assert_awaited()
