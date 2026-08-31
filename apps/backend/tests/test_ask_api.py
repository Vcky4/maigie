"""`POST /api/v1/intelligence/ask` — the HTTP half of Decision C.

**What these are for.** The point of one pipeline behind two transports is that the transports cannot
drift. So these do not re-test the turn — `tests/test_ask_service.py` and `tests/test_chat_ws_frames.py`
do that. They test the three things that are this route's own: that nothing about the turn is decided
here, that a refusal becomes the right status code, and that a refused turn leaves no row.

Fresh, as the plan asks: `tests/test_chat.py` describes a pre-domain architecture and is not collected.

No database and no model. `answer()` takes its effects as an argument, so the route can be driven with a
fake bundle — which is the same seam the frame tests use, and the reason both suites can run here at all.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.domains.intelligence import models, routes  # noqa: E402
from src.domains.intelligence.conversation import (
    ask_service,
    context_enrichment,
)  # noqa: E402
from src.shared.auth import get_current_user  # noqa: E402

USER = SimpleNamespace(id="user_1", name="Ada", tier="FREE", is_onboarded=True)


def a_row(id_="msg_1", **extra):
    defaults = {"content": "", "image_urls": None}
    defaults.update(extra)
    return SimpleNamespace(id=id_, **defaults)


def fake_effects(**overrides):
    """A full `AskEffects` that answers without touching anything real."""
    defaults = {
        "create_message": AsyncMock(side_effect=lambda data: a_row("msg_assistant")),
        "complete_attempt": AsyncMock(
            side_effect=lambda _attempt_id, data: a_row("msg_assistant")
        ),
        "create_action_log": AsyncMock(),
        "generate": AsyncMock(
            return_value=(
                "Entropy measures disorder.",
                {"input_tokens": 10, "output_tokens": 5},
                [],
                [],
            )
        ),
        "resolve_tier": AsyncMock(return_value="free"),
        "model_preference": AsyncMock(return_value=None),
        "fallback_model_name": lambda: "gemini-2.0-flash",
        "check_credits": AsyncMock(return_value=(True, None)),
        "credit_usage": AsyncMock(
            return_value={
                "daily_limit": 5_000,
                "credits_used_today": 5_000,
                "credits_used": 5_000,
                "hard_cap": 5_000,
                "period_end": "2026-09-01",
                "next_daily_reset": "midnight",
            }
        ),
        "consume_credits": AsyncMock(return_value=None),
        "cost_calculator": lambda **_: 0.001,
        "revenue_calculator": lambda **_: 0.002,
        "queue_task": lambda name, kwargs: None,
        "format_list": lambda **_: {},
        "format_action": lambda **_: {},
        "tool_badge": ask_service.tool_skill_badge,
        "query_badge": ask_service.query_type_skill_badge,
        "extract_suggestion": lambda text: (text, None),
        "purchase_deep_link": "maigie://purchase",
    }
    defaults.update(overrides)
    return ask_service.AskEffects(**defaults)


def fake_readers():
    async def none1(_a):
        return None

    async def none2(_a, _b):
        return None

    async def empty(_a, _b):
        return []

    async def raises(_a, _b):
        raise LookupError("not found")

    async def no_history(*, session_id, user_id, review_item_id, limit):
        return []

    async def attach(_user_id, _topic_id, _context):
        return None

    async def no_hits(_query, _user_id, _limit):
        return []

    return context_enrichment.ContextReaders(
        find_note=none2,
        find_review=none2,
        find_topic=none1,
        find_module=none1,
        find_course=none2,
        check_topic_ownership=raises,
        list_topic_notes=empty,
        latest_note_for_topic=none2,
        attach_topic_resources=attach,
        read_history=no_history,
        retrieve=no_hits,
        memory=none2,
    )


class Harness:
    """The route under test, with its effects and reads faked and its writes recorded."""

    def __init__(
        self, *, effects=None, rate_allowed=True, session=None, find_session=None
    ):
        self.messages: list[dict] = []
        self.sessions: list[dict] = []
        self.session = session or SimpleNamespace(
            id="sess_1", user_id="user_1", title="New Chat"
        )
        self.effects = effects or fake_effects()

        async def create_message(data):
            self.messages.append(data)
            return a_row(
                f"msg_{len(self.messages)}",
                content=data.get("content", ""),
                image_urls=data.get("imageUrls"),
            )

        async def create_message_and_attempt(*, message_data, attempt_data):
            message = await create_message(message_data)
            attempt = a_row(
                "attempt_1",
                **{
                    "status": attempt_data["status"],
                    "retryable": attempt_data.get("retryable", False),
                    "context": attempt_data.get("context"),
                    "tool_side_effects": False,
                },
            )
            return message, attempt

        self.attempt_updates: list[tuple[str, dict]] = []

        async def update_attempt(attempt_id, data):
            self.attempt_updates.append((attempt_id, data))

        async def create_chat_session(data):
            self.sessions.append(data)
            return self.session

        self.create_message = create_message
        self.create_message_and_attempt = create_message_and_attempt
        self.update_attempt = update_attempt
        self.heartbeat_attempt = AsyncMock()
        self.create_chat_session = create_chat_session
        self.find_session = find_session or AsyncMock(return_value=self.session)
        self.rate_allowed = rate_allowed
        self.prior_attempt = SimpleNamespace(
            id="attempt_prior",
            status="FAILED",
            retryable=True,
            tool_side_effects=False,
            context={"goalId": "goal_1"},
        )
        self.retry_message_row = a_row(
            "msg_retry",
            content="Try this again",
            image_urls=["https://example.test/a.png"],
            user_id="user_1",
        )
        self.retry_answered = False

        async def find_attempt_for_retry(**_kwargs):
            return self.prior_attempt

        async def find_message(_message_id):
            return self.retry_message_row

        async def user_message_has_answer(_message_id):
            return self.retry_answered

        async def create_attempt(data):
            return a_row(
                "attempt_retry",
                status=data["status"],
                retryable=False,
                context=data.get("context"),
                tool_side_effects=False,
            )

        self.find_attempt_for_retry = find_attempt_for_retry
        self.find_message = find_message
        self.user_message_has_answer = user_message_has_answer
        self.create_attempt = create_attempt

    def __enter__(self):
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1/intelligence")
        app.dependency_overrides[get_current_user] = lambda: USER

        async def check_rate_limit(_key, _max, _window):
            return (self.rate_allowed, 0)

        self._patches = [
            patch.object(
                routes.intelligence_repo, "create_message", self.create_message
            ),
            patch.object(
                routes.intelligence_repo,
                "create_message_and_attempt",
                self.create_message_and_attempt,
            ),
            patch.object(
                routes.intelligence_repo, "update_attempt", self.update_attempt
            ),
            patch.object(
                routes.intelligence_repo, "update_running_attempt", self.update_attempt
            ),
            patch.object(
                routes.intelligence_repo, "heartbeat_attempt", self.heartbeat_attempt
            ),
            patch.object(
                routes.intelligence_repo,
                "find_attempt_for_retry",
                self.find_attempt_for_retry,
            ),
            patch.object(routes.intelligence_repo, "find_message", self.find_message),
            patch.object(
                routes.intelligence_repo,
                "user_message_has_answer",
                self.user_message_has_answer,
            ),
            patch.object(
                routes.intelligence_repo, "create_attempt", self.create_attempt
            ),
            patch.object(
                routes.intelligence_repo,
                "create_chat_session",
                self.create_chat_session,
            ),
            patch.object(
                routes.intelligence_repo, "find_chat_session", self.find_session
            ),
            patch.object(ask_service, "production_effects", lambda: self.effects),
            patch.object(context_enrichment, "production_readers", fake_readers),
            patch.object(context_enrichment, "production_cache", lambda: None),
            patch(
                "src.shared.infrastructure.rate_limit.check_rate_limit",
                check_rate_limit,
            ),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(app, raise_server_exceptions=False)
        return self

    def __exit__(self, *_exc):
        for p in reversed(self._patches):
            p.stop()

    def ask(self, **body):
        payload = {"message": "What is entropy?"}
        payload.update(body)
        return self.client.post("/api/v1/intelligence/ask", json=payload)

    def retry(self, session_id="sess_1", message_id="msg_retry"):
        return self.client.post(
            f"/api/v1/intelligence/conversations/{session_id}/messages/{message_id}/retry"
        )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


class TestATurnOverHttp:
    def test_it_answers(self):
        with Harness() as h:
            response = h.ask()
        assert response.status_code == 200
        assert response.json()["content"] == "Entropy measures disorder."

    def test_the_session_id_comes_back_even_when_none_was_sent(self):
        """A client starting a conversation needs the id to continue it. Making them read it off the
        message row would be a second contract for the same fact."""
        with Harness() as h:
            body = h.ask().json()
        assert body["sessionId"] == "sess_1"

    def test_no_session_id_starts_a_conversation(self):
        with Harness() as h:
            h.ask()
            assert len(h.sessions) == 1

    def test_a_session_id_appends_rather_than_starting_one(self):
        with Harness() as h:
            h.ask(sessionId="sess_1")
            assert h.sessions == []

    def test_two_rows_per_turn(self):
        """One `USER`, one `ASSISTANT`. The learner's message is written by the route; the answer by
        `answer()`."""
        with Harness() as h:
            h.ask()
            assert [row["role"] for row in h.messages] == ["USER"]
            assert h.effects.complete_attempt.await_count == 1

    def test_the_response_is_camel_case_on_the_wire(self):
        """Decision D. The field is `suggestion_text` in Python and `suggestionText` published."""
        with Harness() as h:
            body = h.ask().json()
        assert "suggestionText" in body
        assert "suggestion_text" not in body


class TestTheHttpPathIsMetered:
    def test_the_turn_is_recorded_as_http_not_websocket(self):
        """Migration 049's other half. Per-surface metering only works if both surfaces write the
        column, and `askMode` existed with no writer at all until this phase."""
        with Harness() as h:
            h.ask()
            row = h.effects.complete_attempt.await_args.args[1]
        assert row["askMode"] == ask_service.ASK_MODE_HTTP

    def test_credits_are_consumed_on_a_successful_turn(self):
        with Harness() as h:
            h.ask()
            h.effects.consume_credits.assert_awaited()

    def test_the_answer_carries_token_counts_and_cost(self):
        with Harness() as h:
            h.ask()
            row = h.effects.complete_attempt.await_args.args[1]
        assert row["inputTokens"] == 10
        assert row["outputTokens"] == 5
        assert row["costUsd"] is not None


class TestStreamingIsTheOnlyDifference:
    def test_this_transport_passes_no_chunk_callback(self):
        """The whole of Decision C at the call site: one argument differs between the transports, and it
        is the one that cannot change the answer."""
        with Harness() as h:
            h.ask()
            assert h.effects.generate.await_args.kwargs["stream_callback"] is None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestAnUnusableMessageIsRefused:
    @pytest.mark.parametrize("message", ["   ", "\n\t "])
    def test_a_blank_message_is_a_400(self, message):
        """`400`, not `422`: the request is well-formed and the message is not, and the body says which
        so a client can show it rather than guess."""
        with Harness() as h:
            response = h.ask(message=message)
        assert response.status_code == 400

    def test_an_over_long_message_is_a_400(self):
        with Harness() as h:
            response = h.ask(message="e" * (ask_service.MESSAGE_MAX_LENGTH + 1))
        assert response.status_code == 400

    def test_a_refused_message_leaves_no_row(self):
        """The rule the plan actually cares about. A row without a reply is a question the learner will
        never see answered."""
        with Harness() as h:
            h.ask(message="   ")
            assert h.messages == []
            assert h.sessions == []

    def test_a_refused_message_never_reaches_the_model(self):
        with Harness() as h:
            h.ask(message="   ")
            h.effects.generate.assert_not_awaited()

    def test_the_limit_is_enforced_in_the_pipeline_not_by_the_schema(self):
        """If `AskRequest` carried a `max_length`, pydantic would answer `422` with its own wording and
        the socket would answer something friendlier — two contracts for one rule."""
        field = models.AskRequest.model_fields["message"]
        assert all(
            getattr(meta, "max_length", None) is None for meta in field.metadata
        ), "message declares a max_length; the limit belongs to screen_turn"


class TestRateLimiting:
    def test_too_many_turns_is_a_429(self):
        with Harness(rate_allowed=False) as h:
            response = h.ask()
        assert response.status_code == 429

    def test_the_refusal_says_when_to_come_back(self):
        with Harness(rate_allowed=False) as h:
            response = h.ask()
        assert response.headers["Retry-After"] == str(
            ask_service.RATE_LIMIT_WINDOW_SECONDS
        )

    def test_a_rate_limited_turn_leaves_no_row(self):
        with Harness(rate_allowed=False) as h:
            h.ask()
            assert h.messages == []

    def test_a_rate_limited_turn_never_reaches_the_model(self):
        with Harness(rate_allowed=False) as h:
            h.ask()
            h.effects.generate.assert_not_awaited()


class TestCreditExhaustion:
    def test_it_is_a_402_rather_than_a_403(self):
        """Nothing is forbidden; something is owed. `403` would say the learner may not ask, which is
        false — they may, once they have credits."""
        with Harness(
            effects=fake_effects(check_credits=AsyncMock(return_value=(False, None)))
        ) as h:
            response = h.ask()
        assert response.status_code == 402

    def test_the_body_carries_the_same_words_the_socket_sends(self):
        with Harness(
            effects=fake_effects(check_credits=AsyncMock(return_value=(False, None)))
        ) as h:
            detail = h.ask().json()["detail"]
        assert "credit limit exceeded" in detail.lower()

    def test_an_exhausted_learner_gets_no_assistant_row(self):
        effects = fake_effects(check_credits=AsyncMock(return_value=(False, None)))
        with Harness(effects=effects) as h:
            h.ask()
            effects.create_message.assert_not_awaited()

    def test_an_exhausted_learner_never_reaches_the_model(self):
        effects = fake_effects(check_credits=AsyncMock(return_value=(False, None)))
        with Harness(effects=effects) as h:
            h.ask()
            effects.generate.assert_not_awaited()

    def test_an_exhausted_learner_is_not_charged(self):
        effects = fake_effects(check_credits=AsyncMock(return_value=(False, None)))
        with Harness(effects=effects) as h:
            h.ask()
            effects.consume_credits.assert_not_awaited()


class TestAFailedGenerationIsNotAnAnswer:
    """§1's second clause, on this transport. The guarantee is `answer()`'s — it raises rather than
    returning, so there is no path from a failure to the write — and these confirm the route does not
    undo it."""

    @staticmethod
    def broken():
        return fake_effects(
            generate=AsyncMock(side_effect=RuntimeError("provider down"))
        )

    def test_it_is_a_503(self):
        with Harness(effects=self.broken()) as h:
            assert h.ask().status_code == 503

    def test_no_assistant_row_is_written(self):
        effects = self.broken()
        with Harness(effects=effects) as h:
            h.ask()
            effects.create_message.assert_not_awaited()

    def test_no_credits_are_consumed(self):
        effects = self.broken()
        with Harness(effects=effects) as h:
            h.ask()
            effects.consume_credits.assert_not_awaited()

    def test_the_error_text_is_not_returned_as_content(self):
        """The defect this replaces put the provider's error message in `content`, where it was
        indistinguishable from something Maigie said."""
        with Harness(effects=self.broken()) as h:
            body = h.ask().json()
        assert "content" not in body
        assert "provider down" not in str(body)


class TestSessionOwnership:
    def test_another_learners_conversation_is_a_404(self):
        """`404`, not `403`: "exists but is not yours" confirms the id, which makes conversation ids
        probeable."""
        theirs = SimpleNamespace(id="sess_theirs", user_id="user_2", title="Theirs")
        with Harness(find_session=AsyncMock(return_value=theirs)) as h:
            response = h.ask(sessionId="sess_theirs")
        assert response.status_code == 404

    def test_a_refused_session_leaves_no_row(self):
        theirs = SimpleNamespace(id="sess_theirs", user_id="user_2", title="Theirs")
        with Harness(find_session=AsyncMock(return_value=theirs)) as h:
            h.ask(sessionId="sess_theirs")
            assert h.messages == []

    def test_a_missing_conversation_is_a_404(self):
        with Harness(find_session=AsyncMock(return_value=None)) as h:
            response = h.ask(sessionId="sess_gone")
        assert response.status_code == 404

    def test_the_same_authorisation_function_serves_both_transports(self):
        """The route and the socket must not be able to disagree about who owns a conversation."""
        import inspect

        source = inspect.getsource(routes._resolve_ask_session)
        assert "resolve_session_for_turn" in source


class TestOneTurnAtATimePerConversation:
    """§4.5.13, on this transport. `409` because a conflicting concurrent request is what this is — not a
    rate limit, which the learner fixes by waiting a minute, and not a bad request, which they fix by
    changing it."""

    def test_a_second_turn_on_a_busy_session_is_a_409(self):
        with Harness() as h:
            with patch.object(
                ask_service, "_TURNS_IN_FLIGHT", {"sess_1"}
            ):  # a turn is already running
                response = h.ask(sessionId="sess_1")
        assert response.status_code == 409

    def test_a_refused_second_turn_leaves_no_row(self):
        with Harness() as h:
            with patch.object(ask_service, "_TURNS_IN_FLIGHT", {"sess_1"}):
                h.ask(sessionId="sess_1")
            assert h.messages == []

    def test_a_refused_second_turn_never_reaches_the_model(self):
        with Harness() as h:
            with patch.object(ask_service, "_TURNS_IN_FLIGHT", {"sess_1"}):
                h.ask(sessionId="sess_1")
            h.effects.generate.assert_not_awaited()

    def test_a_busy_conversation_does_not_block_a_different_one(self):
        """The guard is per conversation, not per learner. Two conversations open is reasonable use."""
        with Harness() as h:
            with patch.object(ask_service, "_TURNS_IN_FLIGHT", {"sess_other"}):
                response = h.ask(sessionId="sess_1")
        assert response.status_code == 200

    def test_the_slot_is_released_after_a_successful_turn(self):
        with Harness() as h:
            h.ask()
        assert ask_service.turns_in_flight() == frozenset()

    def test_the_slot_is_released_after_a_failed_turn(self):
        """A `503` must not leave the conversation permanently locked."""
        effects = fake_effects(
            generate=AsyncMock(side_effect=RuntimeError("provider down"))
        )
        with Harness(effects=effects) as h:
            assert h.ask().status_code == 503
        assert ask_service.turns_in_flight() == frozenset()


class TestScopeHonesty:
    """Decision G. The response says what the answer drew on, and whether Maigie can search the learner's
    whole library — which it cannot, until a vector backend exists."""

    def test_the_response_reports_no_library_recall(self):
        with Harness() as h:
            scope = h.ask().json()["scope"]
        assert scope["libraryRecall"] is False

    def test_a_turn_with_no_context_names_no_sources(self):
        with Harness() as h:
            assert h.ask().json()["scope"]["sources"] == []

    def test_the_flag_comes_from_the_retrieval_service_not_a_literal(self):
        """So the day a vector backend lands this starts reporting `True` without anyone remembering to
        come back and change it."""
        with Harness(effects=fake_effects(library_recall=True)) as h:
            assert h.ask().json()["scope"]["libraryRecall"] is True


class TestActionsComeFromTheModelOnly:
    """Decision I. The `suggestedAction` this replaces was keyword matching over the learner's own words,
    published as the model's recommendation — §1's second clause violated outright, because it is a claim
    that is false."""

    def test_a_turn_with_no_tool_calls_reports_no_actions(self):
        with Harness() as h:
            assert h.ask().json()["actions"] == []

    def test_an_executed_action_is_reported(self):
        effects = fake_effects(
            generate=AsyncMock(
                return_value=(
                    "Made you a course.",
                    {"input_tokens": 1, "output_tokens": 1},
                    [
                        {
                            "type": "create_course",
                            "data": {"courseId": "c1"},
                            "result": {"status": "success"},
                        }
                    ],
                    [],
                )
            )
        )
        with Harness(effects=effects) as h:
            actions = h.ask().json()["actions"]
        assert actions == [
            {"type": "create_course", "status": "SUCCESS", "courseId": "c1"}
        ]

    def test_a_failed_action_is_still_reported(self):
        """Carried rather than filtered. The event frames and the components are both success-shaped, so a
        turn whose tool failed would otherwise look like a turn that used no tools."""
        effects = fake_effects(
            generate=AsyncMock(
                return_value=(
                    "I could not.",
                    {"input_tokens": 1, "output_tokens": 1},
                    [
                        {
                            "type": "create_course",
                            "data": {},
                            "result": {
                                "status": "error",
                                "message": "Topic limit reached",
                            },
                        }
                    ],
                    [],
                )
            )
        )
        with Harness(effects=effects) as h:
            actions = h.ask().json()["actions"]
        assert actions[0]["status"] == "FAILED"


class TestDurableRetry:
    def test_http_retry_uses_the_same_rate_limit_before_generation(self):
        with Harness(rate_allowed=False) as h:
            response = h.retry()
        assert response.status_code == 429
        h.effects.generate.assert_not_awaited()

    def test_retry_reuses_the_original_user_row_content_images_and_context(self):
        with Harness() as h:
            response = h.retry()
        assert response.status_code == 200
        assert h.messages == [], "retry must not create a second USER row"
        kwargs = h.effects.generate.await_args.kwargs
        assert kwargs["user_message"] == "Try this again"
        assert kwargs["image_url"] == "https://example.test/a.png"
        assert kwargs["context"] == {"goalId": "goal_1"}
        assert response.json()["attemptId"] == "attempt_retry"

    @pytest.mark.parametrize(
        "status,retryable,tool_side_effects",
        [
            ("SUCCEEDED", False, False),
            ("FAILED", False, False),
            ("FAILED", True, True),
            ("RUNNING", False, False),
        ],
    )
    def test_only_explicitly_retryable_terminal_side_effect_free_attempts_are_allowed(
        self, status, retryable, tool_side_effects
    ):
        with Harness() as h:
            h.prior_attempt.status = status
            h.prior_attempt.retryable = retryable
            h.prior_attempt.tool_side_effects = tool_side_effects
            response = h.retry()
        assert response.status_code == 409
        h.effects.generate.assert_not_awaited()

    def test_an_answered_attempt_is_rejected(self):
        with Harness() as h:
            h.retry_answered = True
            response = h.retry()
        assert response.status_code == 409

    def test_an_unknown_legacy_message_is_rejected(self):
        with Harness() as h:
            h.prior_attempt = None
            response = h.retry()
        assert response.status_code == 409

    def test_a_foreign_conversation_is_hidden(self):
        foreign = SimpleNamespace(id="sess_1", user_id="someone_else", title="Private")
        with Harness(session=foreign) as h:
            response = h.retry()
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_boundary_closes_before_post_generation_persistence():
    charging = asyncio.Event()
    release = asyncio.Event()

    async def consume(*_args):
        charging.set()
        await release.wait()

    effects = fake_effects(consume_credits=AsyncMock(side_effect=consume))
    task = asyncio.create_task(
        ask_service.answer(
            message="What is entropy?",
            user=USER,
            user_obj=USER,
            session=SimpleNamespace(id="sess_1"),
            user_message=SimpleNamespace(id="msg_user"),
            context=None,
            ask_mode=ask_service.ASK_MODE_WEBSOCKET,
            readers=fake_readers(),
            effects=effects,
            attempt_id="attempt_boundary",
            update_attempt=AsyncMock(),
        )
    )
    await charging.wait()

    assert ask_service.cancel_turn("attempt_boundary") is False
    assert not task.done()

    release.set()
    turn = await task
    assert turn.assistant_message.id == "msg_assistant"
    effects.complete_attempt.assert_awaited_once()
