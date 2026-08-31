"""The Ask Maigie pipeline's decisions, tested directly.

Every function here was previously a few lines buried inside `register_chat_websocket_routes`, a
1,900-line function with no tests. They were unreachable without driving a live WebSocket against a live
database and a live model, so none of them had ever been exercised in isolation — which is how a
retrieval gate, a score floor and a token divisor all ended up being load-bearing heuristics that
nobody could see.

They are the first things extracted into `ask_service` because they are pure: no socket, no database, no
provider. That makes them both the safest to move and the cheapest to pin. The stages that are *not* yet
moved are listed in `ask_service.STILL_IN_THE_HANDLER`, and the last test in this file asserts that
inventory stays honest.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.domains.intelligence.conversation import (
    ask_service,
    context_enrichment,
    tool_outcomes,
)


class TestAskContext:
    def test_it_reads_the_context_object_the_clients_send(self):
        context = ask_service.AskContext.from_client(
            {
                "sessionId": "sess_1",
                "courseId": "course_1",
                "topicId": "topic_1",
                "noteId": "note_1",
                "spaceId": "space_1",
                "reviewItemId": "review_1",
                "replyToMessageId": "msg_1",
            }
        )
        assert context.session_id == "sess_1"
        assert context.course_id == "course_1"
        assert context.topic_id == "topic_1"
        assert context.note_id == "note_1"
        assert context.space_id == "space_1"
        assert context.review_item_id == "review_1"
        assert context.reply_to_message_id == "msg_1"

    def test_an_absent_context_is_not_an_error(self):
        """The clients send a bare string when there is no context to attach."""
        context = ask_service.AskContext.from_client(None)
        assert context.session_id is None
        assert context.raw == {}

    def test_unmodelled_keys_survive_in_raw(self):
        """Enrichment still reads keys the dataclass does not model. Losing them silently would be the
        plan's silent-discard class, moved into the extraction itself."""
        context = ask_service.AskContext.from_client(
            {"noteContent": "...", "pageContext": "review"}
        )
        assert context.raw["noteContent"] == "..."
        assert context.raw["pageContext"] == "review"

    def test_raw_is_a_copy_not_the_callers_dict(self):
        original = {"sessionId": "sess_1"}
        context = ask_service.AskContext.from_client(original)
        original["sessionId"] = "mutated"
        assert context.raw["sessionId"] == "sess_1"

    def test_a_review_thread_is_recognised(self):
        assert ask_service.AskContext.from_client(
            {"reviewItemId": "r1"}
        ).is_review_thread
        assert not ask_service.AskContext.from_client({}).is_review_thread


class TestTokenEstimation:
    def test_it_counts_message_context_and_history_together(self):
        """All three reach the provider, so all three have to be paid for."""
        estimate = ask_service.estimate_prompt_tokens(
            message="a" * 40, context={"k": "v"}, history=[{"role": "user"}]
        )
        message_only = ask_service.estimate_prompt_tokens(message="a" * 40)
        assert estimate > message_only

    def test_a_turn_reserves_room_for_the_answer(self):
        """A learner one token under their cap must not be allowed to start a turn that blows past it."""
        message = "What is entropy?"
        assert ask_service.estimate_turn_tokens(message=message) == (
            ask_service.estimate_prompt_tokens(message=message) + 500
        )

    def test_an_empty_message_costs_nothing(self):
        assert ask_service.estimate_prompt_tokens(message="") == 0
        assert ask_service.estimate_prompt_tokens(message=None) == 0

    def test_the_estimate_is_the_same_arithmetic_in_both_places(self):
        """The pre-flight check and the fallback charge used to be two hand-written copies of this.

        Two copies of a billing calculation is one edit away from charging a learner against a number
        they were never checked against.
        """
        message, context, history = (
            "Explain the second law",
            {"courseId": "c1"},
            [{"role": "user"}],
        )
        assert ask_service.estimate_turn_tokens(
            message=message, context=context, history=history
        ) - 500 == ask_service.estimate_prompt_tokens(
            message=message, context=context, history=history
        )


class TestRetrievalGate:
    @pytest.mark.parametrize(
        "message",
        ["hi", "hello", "thanks", "ok", "?", "hmm", "what", "no"],
    )
    def test_a_trivial_message_is_not_worth_a_search(self, message):
        assert ask_service.should_retrieve(message) is False

    @pytest.mark.parametrize("message", ["hi there how are you", "hello again friend"])
    def test_a_greeting_with_padding_is_still_a_greeting(self, message):
        assert ask_service.should_retrieve(message) is False

    @pytest.mark.parametrize(
        "message",
        [
            "What did my thermodynamics notes say about entropy?",
            "Summarise the lecture on eigenvalues for me",
        ],
    )
    def test_a_real_question_is_worth_a_search(self, message):
        assert ask_service.should_retrieve(message) is True

    def test_a_short_message_is_skipped_even_if_it_is_not_a_known_pleasantry(self):
        """Fifteen characters is not enough to search against, whatever the words are."""
        assert ask_service.should_retrieve("why entropy?") is False

    def test_case_and_padding_do_not_defeat_the_gate(self):
        assert ask_service.should_retrieve("   THANKS   ") is False

    def test_an_empty_message_is_skipped(self):
        assert ask_service.should_retrieve("") is False
        assert ask_service.should_retrieve(None) is False


class TestRetrievalScoreFilter:
    def test_a_low_scoring_hit_is_dropped(self):
        """Passing noise to the model invites it to answer about something else entirely."""
        items = ask_service.relevant_retrieved_items(
            [
                {
                    "similarity": 0.2,
                    "objectType": "note",
                    "objectId": "n1",
                    "data": {"title": "X"},
                }
            ]
        )
        assert items == []

    def test_a_high_scoring_hit_is_rendered_with_its_id(self):
        items = ask_service.relevant_retrieved_items(
            [
                {
                    "similarity": 0.91,
                    "objectType": "note",
                    "objectId": "n1",
                    "data": {"title": "Thermo"},
                }
            ]
        )
        assert items == ["- NOTE: Thermo (ID: n1)"]

    def test_the_alternate_score_key_is_honoured(self):
        """The two retrieval paths disagree on the key name; dropping one would silently drop all its
        hits and look like "retrieval found nothing"."""
        items = ask_service.relevant_retrieved_items(
            [
                {
                    "score": 0.91,
                    "objectType": "course",
                    "objectId": "c1",
                    "data": {"title": "Physics"},
                }
            ]
        )
        assert items == ["- COURSE: Physics (ID: c1)"]

    def test_a_hit_exactly_on_the_floor_is_kept(self):
        items = ask_service.relevant_retrieved_items(
            [
                {
                    "similarity": ask_service.RETRIEVAL_SCORE_FLOOR,
                    "objectType": "note",
                    "objectId": "n1",
                    "data": {},
                }
            ]
        )
        assert items == ["- NOTE: Untitled (ID: n1)"]

    def test_no_results_is_not_an_error(self):
        assert ask_service.relevant_retrieved_items(None) == []
        assert ask_service.relevant_retrieved_items([]) == []


class TestExplicitViewGate:
    @pytest.mark.parametrize(
        "message",
        ["show my courses", "what are my goals", "list my notes", "SHOW MY SCHEDULE"],
    )
    def test_asking_to_see_data_counts(self, message):
        assert ask_service.wants_to_view_data(message) is True

    @pytest.mark.parametrize(
        "message", ["build me a study plan", "what is entropy?", "help me revise"]
    )
    def test_asking_for_work_does_not(self, message):
        assert ask_service.wants_to_view_data(message) is False

    def test_cards_are_rendered_when_the_learner_asked_to_see_their_data(self):
        assert (
            ask_service.should_render_query_components(
                message="show my courses", executed_actions=[]
            )
            is True
        )

    def test_cards_are_suppressed_when_the_turn_also_created_something(self):
        """The model calls `get_user_courses` to check context while creating a study plan. Rendering
        course cards then answers a question the learner did not ask, and buries the thing they did.
        """
        assert (
            ask_service.should_render_query_components(
                message="show my courses and make me a plan",
                executed_actions=[{"type": "create_study_plan"}],
            )
            is False
        )

    def test_cards_are_suppressed_when_the_learner_only_asked_for_work(self):
        assert (
            ask_service.should_render_query_components(
                message="make me a study plan", executed_actions=[]
            )
            is False
        )

    def test_a_read_only_tool_call_does_not_suppress_cards(self):
        assert (
            ask_service.should_render_query_components(
                message="show my courses",
                executed_actions=[{"type": "get_user_courses"}],
            )
            is True
        )


class TestSkillBadges:
    @staticmethod
    def tool_badge(name):
        return {
            "courses": {
                "id": "courses",
                "name": "Course Management",
                "icon": "book-open",
            }
        }.get({"get_user_courses": "courses", "create_course": "courses"}.get(name, ""))

    @staticmethod
    def query_badge(query_type):
        return {
            "goals": {"id": "goals", "name": "Goal Management", "icon": "target"}
        }.get(query_type)

    def test_a_badge_is_built_from_an_executed_action(self):
        badges = ask_service.build_skill_badges(
            executed_actions=[{"type": "create_course"}],
            query_results=[],
            tool_badge=self.tool_badge,
            query_badge=self.query_badge,
        )
        assert badges == [
            {"id": "courses", "name": "Course Management", "icon": "book-open"}
        ]

    def test_one_skill_appears_once_however_many_times_it_was_used(self):
        """Two course tools in one turn is one skill, not two badges."""
        badges = ask_service.build_skill_badges(
            executed_actions=[{"type": "get_user_courses"}, {"type": "create_course"}],
            query_results=[],
            tool_badge=self.tool_badge,
            query_badge=self.query_badge,
        )
        assert len(badges) == 1

    def test_query_results_contribute_badges_too(self):
        badges = ask_service.build_skill_badges(
            executed_actions=[],
            query_results=[{"query_type": "goals"}],
            tool_badge=self.tool_badge,
            query_badge=self.query_badge,
        )
        assert badges == [{"id": "goals", "name": "Goal Management", "icon": "target"}]

    def test_an_unmapped_tool_contributes_nothing(self):
        assert (
            ask_service.build_skill_badges(
                executed_actions=[{"type": "something_new"}],
                query_results=[],
                tool_badge=self.tool_badge,
                query_badge=self.query_badge,
            )
            == []
        )

    def test_actions_come_before_query_badges(self):
        """Badge order is the order the work happened, which is what the learner watched."""
        badges = ask_service.build_skill_badges(
            executed_actions=[{"type": "create_course"}],
            query_results=[{"query_type": "goals"}],
            tool_badge=self.tool_badge,
            query_badge=self.query_badge,
        )
        assert [badge["id"] for badge in badges] == ["courses", "goals"]

    def test_nothing_used_means_no_badges(self):
        assert (
            ask_service.build_skill_badges(
                executed_actions=None,
                query_results=None,
                tool_badge=self.tool_badge,
                query_badge=self.query_badge,
            )
            == []
        )


def message_row(role, content, **kwargs):
    return SimpleNamespace(role=role, content=content, **kwargs)


class TestHistoryFormatting:
    def test_db_roles_become_provider_roles(self):
        history = ask_service.format_history(
            [
                message_row(
                    "USER", "What is entropy?", image_urls=None, image_url=None
                ),
                message_row("ASSISTANT", "Disorder.", image_urls=None, image_url=None),
            ]
        )
        assert [turn["role"] for turn in history] == ["user", "model"]

    def test_content_is_the_first_part(self):
        history = ask_service.format_history(
            [message_row("USER", "What is entropy?", image_urls=None, image_url=None)]
        )
        assert history[0]["parts"][0] == "What is entropy?"

    def test_images_travel_with_the_message_that_carried_them(self):
        """So "what does the third line of that diagram say" still has the diagram."""
        history = ask_service.format_history(
            [
                message_row(
                    "USER",
                    "What does this show?",
                    image_urls=["https://cdn/a.png", "https://cdn/b.png"],
                    image_url=None,
                )
            ]
        )
        assert history[0]["parts"] == [
            "What does this show?",
            "https://cdn/a.png",
            "https://cdn/b.png",
        ]

    def test_the_legacy_single_image_column_is_read_as_a_fallback(self):
        """Rows written before `imageUrls` existed only have the singular column. Ignoring it would
        drop the image out of the history of every old conversation."""
        history = ask_service.format_history(
            [
                message_row(
                    "USER",
                    "This one?",
                    image_urls=None,
                    image_url="https://cdn/old.png",
                )
            ]
        )
        assert history[0]["parts"] == ["This one?", "https://cdn/old.png"]

    def test_the_plural_column_wins_when_both_are_set(self):
        history = ask_service.format_history(
            [
                message_row(
                    "USER",
                    "?",
                    image_urls=["https://cdn/new.png"],
                    image_url="https://cdn/old.png",
                )
            ]
        )
        assert history[0]["parts"] == ["?", "https://cdn/new.png"]

    def test_an_empty_thread_formats_to_nothing(self):
        assert ask_service.format_history([]) == []

    def test_the_history_limit_is_a_named_constant(self):
        """It reaches the prompt's token budget, so it is not a magic number in a query."""
        assert isinstance(ask_service.HISTORY_LIMIT, int)
        assert ask_service.HISTORY_LIMIT > 0


class TestTheExtractionInventoryIsHonest:
    """A half-moved pipeline reads like a finished one. These keep the boundary a fact in the code.

    Delete this class when `STILL_IN_THE_HANDLER` is empty — that is what finishing looks like.
    """

    def test_every_stage_claimed_as_moved_is_actually_importable(self):
        """Values are `"attribute"` for this module, or `("module", "attribute")` for a stage that left
        the handler into a sibling. The inventory records what stopped being inline, which is not the
        same question as what lives in this file."""
        expected = {
            "token estimation": "estimate_turn_tokens",
            "retrieval gate": "should_retrieve",
            "explicit-view gate": "should_render_query_components",
            "skill badges": "build_skill_badges",
            "history formatting": "format_history",
            "usage reconciliation and pricing": "resolve_usage",
            "assistant row assembly": "build_assistant_row",
            "context cache keying": "context_cache_key_parts",
            "page context instruction blocks": "REVIEW_MODE_PAGE_CONTEXT",
            "context shaping (records to context keys)": "note_context_updates",
            "session pinning and authorization": "resolve_session_for_turn",
            "conversation titling": "should_retitle_session",
            "new session row assembly": "new_session_row",
            "owner-scoped context reads": (context_enrichment, "resolve_owned_topic"),
            "context enrichment (branches, reads and cache)": (
                context_enrichment,
                "enrich_context",
            ),
            "history isolation rules": (context_enrichment, "build_history"),
            "retrieval and memory recall": (context_enrichment, "attach_recall"),
            "tool outcomes (logs, events, components, refusals, background work)": (
                tool_outcomes,
                "collect_tool_outcomes",
            ),
            "credit refusal (which cap, and what the learner is told)": "credit_refusal",
            "generation, and the order of the turn": "AskEffects",
            "the persistence write": "production_effects",
            "the credit check and consumption": "TurnRefused",
            "the whole turn — answer() (Decision C)": "answer",
        }
        assert set(ask_service.MOVED_SO_FAR) == set(expected)
        for stage, target in expected.items():
            module, attribute = (
                target if isinstance(target, tuple) else (ask_service, target)
            )
            assert hasattr(
                module, attribute
            ), f"{stage} is claimed as moved but {module.__name__}.{attribute} is absent"

    def test_the_two_inventories_do_not_overlap(self):
        assert not set(ask_service.MOVED_SO_FAR) & set(ask_service.STILL_IN_THE_HANDLER)

    def test_answer_exists_and_the_handler_goes_through_it(self):
        """`answer()` is the destination (Decision C), and it landed 2026-08-28.

        This replaces `test_answer_is_not_yet_published`, which guarded against `answer()` becoming a
        facade over a pipeline still living in the handler. The guard now points the other way: the
        handler must reach the model *only* through `answer()`. A second `route_request` call from the
        transport is how the two-pipeline drift Decision C exists to prevent would come back.
        """
        import inspect
        from pathlib import Path

        import src.domains.intelligence.conversation.websocket_handler as handler

        assert hasattr(ask_service, "answer")
        source = Path(inspect.getsourcefile(handler)).read_text(encoding="utf-8")
        assert "route_request" not in source, (
            "websocket_handler calls the model directly again. Everything above the transport belongs "
            "in `ask_service.answer()` — see Decision C and plan §5.4 for what two pipelines drift into."
        )
        assert (
            "ask_service.answer(" in source
        ), "the handler no longer goes through answer()"

    def test_streaming_is_a_callback_rather_than_a_second_path(self):
        """Decision C in one assertion: `on_chunk` is optional, so HTTP passing `None` runs the same
        code as the socket passing a function. If it becomes required, or a second answer-shaped entry
        point appears, that has been abandoned."""
        import inspect

        assert (
            inspect.signature(ask_service.answer).parameters["on_chunk"].default is None
        )
        assert not [
            name
            for name in dir(ask_service)
            if name.startswith("answer") and name != "answer"
        ], "a second answer-shaped entry point exists; Decision C says there is one"


# ---------------------------------------------------------------------------
# Usage reconciliation and pricing
# ---------------------------------------------------------------------------


def fake_cost(*, input_tokens, output_tokens, model_name):
    """Priced per token so a test can tell input from output, unlike a flat rate."""
    return round(input_tokens * 0.001 + output_tokens * 0.01, 6)


def fake_revenue(*, input_tokens, output_tokens, user_tier):
    multiplier = 2 if user_tier == "FREE" else 1
    return round((input_tokens + output_tokens) * 0.002 * multiplier, 6)


def resolve(usage_info, **overrides):
    kwargs = {
        "usage_info": usage_info,
        "message": "What is entropy?",
        "response": "Entropy is a measure of disorder.",
        "context": None,
        "history": None,
        "model_name": "gemini-3.5-flash",
        "user_tier": "FREE",
        "cost_calculator": fake_cost,
        "revenue_calculator": fake_revenue,
    }
    kwargs.update(overrides)
    return ask_service.resolve_usage(**kwargs)


class TestUsageReconciliation:
    """What the learner is charged must be what the provider reported, or the same estimate they were
    checked against — never a third number."""

    def test_reported_counts_are_used_as_given(self):
        usage = resolve({"input_tokens": 120, "output_tokens": 45})
        assert (usage.input_tokens, usage.output_tokens) == (120, 45)
        assert usage.total_tokens == 165

    def test_both_zero_falls_back_to_the_estimate(self):
        usage = resolve({"input_tokens": 0, "output_tokens": 0})
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

    def test_the_fallback_is_the_same_arithmetic_as_the_preflight_check(self):
        """The defect this guards: two copies of the estimate, so a learner is checked against one
        number and billed on another."""
        message = "What is entropy?"
        usage = resolve({"input_tokens": 0, "output_tokens": 0}, message=message)
        assert usage.input_tokens == ask_service.estimate_prompt_tokens(
            message=message, context=None, history=None
        )

    def test_a_missing_usage_dict_falls_back_rather_than_raising(self):
        usage = resolve(None)
        assert usage.input_tokens > 0

    def test_a_real_zero_output_is_not_overwritten(self):
        """A provider reporting input and no output is reporting a real empty reply. Estimating over it
        would invent output that did not happen."""
        usage = resolve({"input_tokens": 99, "output_tokens": 0})
        assert (usage.input_tokens, usage.output_tokens) == (99, 0)

    def test_cost_and_revenue_come_from_the_injected_calculators(self):
        usage = resolve({"input_tokens": 100, "output_tokens": 10})
        assert usage.cost_usd == fake_cost(
            input_tokens=100, output_tokens=10, model_name="gemini-3.5-flash"
        )
        assert usage.revenue_usd == fake_revenue(
            input_tokens=100, output_tokens=10, user_tier="FREE"
        )

    def test_the_tier_reaches_the_revenue_calculator(self):
        free = resolve({"input_tokens": 100, "output_tokens": 10}, user_tier="FREE")
        paid = resolve(
            {"input_tokens": 100, "output_tokens": 10}, user_tier="PREMIUM_MONTHLY"
        )
        assert free.revenue_usd != paid.revenue_usd

    def test_the_model_name_is_carried_through(self):
        usage = resolve(
            {"input_tokens": 1, "output_tokens": 1}, model_name="openai:gpt-4o-mini"
        )
        assert usage.model_name == "openai:gpt-4o-mini"


# ---------------------------------------------------------------------------
# Assistant row assembly
# ---------------------------------------------------------------------------


def a_usage(**overrides):
    kwargs = {
        "model_name": "gemini-3.5-flash",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.5,
        "revenue_usd": 1.0,
    }
    kwargs.update(overrides)
    return ask_service.AskUsage(**kwargs)


def a_row(**overrides):
    kwargs = {
        "session_id": "sess_1",
        "user_id": "user_1",
        "content": "Entropy is a measure of disorder.",
        "usage": a_usage(),
        "ask_mode": ask_service.ASK_MODE_WEBSOCKET,
        "answer_scope": ask_service.AnswerScope(
            sources=("note",), library_recall=False
        ),
    }
    kwargs.update(overrides)
    return ask_service.build_assistant_row(**kwargs)


class TestAssistantRowAssembly:
    def test_the_required_columns_are_always_present(self):
        row = a_row()
        for key in (
            "sessionId",
            "userId",
            "role",
            "content",
            "tokenCount",
            "inputTokens",
            "outputTokens",
            "modelName",
            "costUsd",
            "revenueUsd",
            "askMode",
            "answerScope",
        ):
            assert key in row, f"{key} missing"
        assert row["role"] == "ASSISTANT"
        assert row["answerScope"] == {
            "sources": ["note"],
            "libraryRecall": False,
        }

    def test_token_count_is_the_sum_and_not_recomputed_from_content(self):
        row = a_row(usage=a_usage(input_tokens=7, output_tokens=11))
        assert row["tokenCount"] == 18

    def test_ask_mode_is_written(self):
        """The column landed with migration 049 and had no writer, so per-surface metering was
        impossible — which is the whole reason it exists."""
        assert a_row()["askMode"] == "ws"
        assert a_row(ask_mode=ask_service.ASK_MODE_HTTP)["askMode"] == "http"

    def test_the_two_ask_modes_are_distinct(self):
        assert ask_service.ASK_MODE_WEBSOCKET != ask_service.ASK_MODE_HTTP

    def test_optional_keys_are_omitted_rather_than_set_to_none(self):
        """The repository maps what it is given: a key present with `None` overwrites, an absent key
        leaves the column alone."""
        row = a_row()
        for key in (
            "replyToMessageId",
            "componentData",
            "suggestionText",
            "citations",
            "truncated",
        ):
            assert key not in row, f"{key} should be omitted when not supplied"

    def test_components_are_included_when_present(self):
        row = a_row(components=[{"type": "course_card"}])
        assert row["componentData"] == [{"type": "course_card"}]

    def test_empty_components_are_omitted(self):
        assert "componentData" not in a_row(components=[])

    def test_suggestion_text_is_included_when_present(self):
        assert (
            a_row(suggestion_text="Try a quiz next.")["suggestionText"]
            == "Try a quiz next."
        )

    def test_reply_target_is_included_when_present(self):
        assert a_row(reply_to_message_id="msg_9")["replyToMessageId"] == "msg_9"

    def test_review_item_id_is_always_carried_even_when_none(self):
        """Review threads stay isolated, so this column is set explicitly rather than omitted."""
        assert a_row()["reviewItemId"] is None
        assert a_row(review_item_id="rev_3")["reviewItemId"] == "rev_3"

    def test_absent_citations_and_empty_citations_are_different_rows(self):
        """`None` means grounding was not attempted; `[]` means it ran and found nothing. Collapsing
        them would make every historical row look like a failed search."""
        assert "citations" not in a_row(citations=None)
        assert a_row(citations=[])["citations"] == []

    def test_citations_are_included_when_present(self):
        cites = [{"url": "https://example.org", "title": "Entropy"}]
        assert a_row(citations=cites)["citations"] == cites

    def test_truncated_is_only_written_when_true(self):
        """The column defaults to false in the database, so writing false is noise; writing true is
        the only thing that carries information."""
        assert "truncated" not in a_row(truncated=False)
        assert a_row(truncated=True)["truncated"] is True

    def test_every_key_is_one_the_repository_allows(self):
        """`map_fields` raises on an unknown key, so a typo here is a write that fails at runtime.
        Checked against the real map rather than a copy of it."""
        from src.domains.intelligence.repository import IntelligenceRepository

        allowed = set(IntelligenceRepository._MESSAGE_MAP)
        row = a_row(
            components=[{"type": "x"}],
            suggestion_text="s",
            reply_to_message_id="m",
            citations=[],
            truncated=True,
            review_item_id="r",
        )
        assert set(row) <= allowed, f"not in _MESSAGE_MAP: {set(row) - allowed}"


# ---------------------------------------------------------------------------
# Context cache keying
# ---------------------------------------------------------------------------


class TestContextCacheKeyParts:
    """A key missing an id that changes the result serves one learner's context as another's.

    The TTL is 300 seconds, so a collision is not a momentary glitch — it is five minutes of a topic
    being answered with a different topic's facts.
    """

    def test_no_ids_means_nothing_to_cache(self):
        assert ask_service.context_cache_key_parts(user_id="u1", context={}) is None

    def test_a_missing_context_is_not_an_error(self):
        assert ask_service.context_cache_key_parts(user_id="u1", context=None) is None

    def test_context_with_only_non_id_keys_is_not_cached(self):
        """`content` is pasted in per turn, so it identifies nothing and must not create an entry."""
        parts = ask_service.context_cache_key_parts(
            user_id="u1", context={"content": "some pasted text"}
        )
        assert parts is None

    @pytest.mark.parametrize(
        "id_name", ["noteId", "topicId", "courseId", "reviewItemId"]
    )
    def test_each_id_alone_produces_a_key(self, id_name):
        parts = ask_service.context_cache_key_parts(
            user_id="u1", context={id_name: "x1"}
        )
        assert parts is not None
        assert "x1" in parts

    @pytest.mark.parametrize(
        "id_name", ["noteId", "topicId", "courseId", "reviewItemId"]
    )
    def test_changing_any_single_id_changes_the_key(self, id_name):
        """The collision test. Every id in the key must move the key on its own."""
        base = {"noteId": "n", "topicId": "t", "courseId": "c", "reviewItemId": "r"}
        other = {**base, id_name: "CHANGED"}
        assert ask_service.context_cache_key_parts(
            user_id="u1", context=base
        ) != ask_service.context_cache_key_parts(user_id="u1", context=other)

    def test_the_user_is_part_of_the_key(self):
        """Enriched context is per-learner. A key without the user crosses accounts."""
        context = {"topicId": "t1"}
        assert ask_service.context_cache_key_parts(
            user_id="u1", context=context
        ) != ask_service.context_cache_key_parts(user_id="u2", context=context)

    def test_absent_ids_are_placeheld_so_positions_do_not_shift(self):
        """Without a placeholder, {"topicId": "x"} and {"noteId": "x"} would build the same parts."""
        by_note = ask_service.context_cache_key_parts(
            user_id="u1", context={"noteId": "x"}
        )
        by_topic = ask_service.context_cache_key_parts(
            user_id="u1", context={"topicId": "x"}
        )
        assert by_note != by_topic

    def test_the_key_is_namespaced(self):
        parts = ask_service.context_cache_key_parts(
            user_id="u1", context={"topicId": "t"}
        )
        assert parts[:2] == ["chat", "context"]

    def test_it_is_stable_for_the_same_input(self):
        context = {"topicId": "t", "courseId": "c"}
        assert ask_service.context_cache_key_parts(
            user_id="u1", context=context
        ) == ask_service.context_cache_key_parts(user_id="u1", context=context)

    def test_unread_ids_are_deliberately_absent(self):
        """`examPrepId` and `spaceId` ride on the context but enrichment does not read them, so they
        must not be in the key. This test is the reminder to add them here in the same change that
        starts reading them — it is documenting the audit, not asserting they are unimportant.
        """
        parts = ask_service.context_cache_key_parts(
            user_id="u1", context={"examPrepId": "p1", "spaceId": "s1"}
        )
        assert parts is None


class TestCacheableContext:
    def test_volatile_keys_are_stripped(self):
        enriched = {
            "topicId": "t1",
            "topicTitle": "Entropy",
            "pageContext": "Review mode instructions...",
            "content": "pasted",
            "noteContent": "note body",
            "retrieved_items": ["a"],
            "topicResources": [{"id": "r"}],
            "topicUploadedResources": [{"id": "u"}],
        }
        result = ask_service.cacheable_context(enriched)
        assert result == {"topicId": "t1", "topicTitle": "Entropy"}

    def test_fetched_facts_survive(self):
        enriched = {
            "courseTitle": "Thermo",
            "courseDescription": "d",
            "moduleTitle": "m",
        }
        assert ask_service.cacheable_context(enriched) == enriched

    def test_the_input_is_not_mutated(self):
        enriched = {"topicId": "t", "pageContext": "x"}
        ask_service.cacheable_context(enriched)
        assert "pageContext" in enriched

    def test_every_volatile_key_is_actually_excluded(self):
        enriched = dict.fromkeys(ask_service.VOLATILE_CONTEXT_KEYS, "value")
        assert ask_service.cacheable_context(enriched) == {}

    def test_page_context_is_volatile(self):
        """Named explicitly because it is the one that looks like a fetched fact and is not: it is a
        generated instruction block that differs between review mode and topic mode."""
        assert "pageContext" in ask_service.VOLATILE_CONTEXT_KEYS


class TestMergeCachedContext:
    def test_cached_facts_are_overlaid(self):
        merged = ask_service.merge_cached_context(
            {"topicId": "t"}, {"topicTitle": "Entropy"}
        )
        assert merged == {"topicId": "t", "topicTitle": "Entropy"}

    def test_the_cached_value_wins_on_conflict(self):
        """Deliberate and worth pinning: the cached half holds fetched facts about ids, so on a key
        collision it is the enrichment result rather than a client guess."""
        merged = ask_service.merge_cached_context(
            {"topicTitle": "client guess"}, {"topicTitle": "fetched"}
        )
        assert merged["topicTitle"] == "fetched"

    def test_no_cache_yields_a_copy_of_the_context(self):
        context = {"topicId": "t"}
        assert ask_service.merge_cached_context(context, None) == context

    def test_an_empty_cache_entry_is_treated_as_no_cache(self):
        context = {"topicId": "t"}
        assert ask_service.merge_cached_context(context, {}) == context

    def test_neither_argument_is_mutated(self):
        context = {"topicId": "t"}
        cached = {"topicTitle": "Entropy"}
        ask_service.merge_cached_context(context, cached)
        assert context == {"topicId": "t"}
        assert cached == {"topicTitle": "Entropy"}

    def test_the_result_is_a_new_object(self):
        context = {"topicId": "t"}
        assert ask_service.merge_cached_context(context, None) is not context


# ---------------------------------------------------------------------------
# Page context instruction blocks
# ---------------------------------------------------------------------------


class TestReviewModePageContext:
    """Prompt text that drives the scheduler. Pinned because it was 970 lines inside a 2,000-line
    function, where nobody would notice a boundary changing."""

    def test_it_names_the_complete_review_tool(self):
        """The turn ends by calling a tool. If the instruction stops naming it, the review never
        completes and the topic is never rescheduled — a silent failure, because the conversation still
        reads normally."""
        assert "complete_review" in ask_service.REVIEW_MODE_PAGE_CONTEXT

    @pytest.mark.parametrize("rating", ["0", "1", "2", "3", "4", "5"])
    def test_every_point_on_the_quality_scale_is_defined(self, rating):
        """The scale is a contract with the interval calculation. A missing point is a rating the model
        has to guess at, and the guess changes a learner's next review date."""
        assert f"{rating} =" in ask_service.REVIEW_MODE_PAGE_CONTEXT

    def test_it_asks_for_one_question_at_a_time(self):
        """Without this a model lists all questions in one message, which makes per-answer feedback
        impossible and turns the review into a worksheet."""
        assert "ONE AT A TIME" in ask_service.REVIEW_MODE_PAGE_CONTEXT

    def test_it_asks_for_a_score_summary(self):
        assert "score_summary" in ask_service.REVIEW_MODE_PAGE_CONTEXT

    def test_it_tells_the_model_not_to_ask_for_a_button_press(self):
        """There is no button. Completion is the tool call, so inviting a click strands the learner."""
        assert "button" in ask_service.REVIEW_MODE_PAGE_CONTEXT

    def test_it_is_a_constant_and_not_a_format_string(self):
        """No interpolation, so it cannot be accidentally fed learner text — which would be a prompt
        injection seam in the instruction half of the prompt."""
        assert "{" not in ask_service.REVIEW_MODE_PAGE_CONTEXT


# ---------------------------------------------------------------------------
# Context shaping
# ---------------------------------------------------------------------------


def a_topic(**kwargs):
    return SimpleNamespace(
        **{"id": "top_1", "title": "Entropy", "content": "body", **kwargs}
    )


def a_module(**kwargs):
    return SimpleNamespace(**{"id": "mod_1", "title": "Thermodynamics", **kwargs})


def a_course(**kwargs):
    return SimpleNamespace(
        **{"id": "crs_1", "title": "Physics", "description": "A course", **kwargs}
    )


def a_note(**kwargs):
    return SimpleNamespace(
        **{
            "id": "note_1",
            "title": "My note",
            "content": "note body",
            "summary": "a summary",
            **kwargs,
        }
    )


def a_review(**kwargs):
    return SimpleNamespace(
        **{
            "id": "rev_1",
            "topic_id": "top_1",
            "next_review_at": "2026-09-01T00:00:00",
            **kwargs,
        }
    )


class TestReviewContextUpdates:
    def test_it_carries_the_review_instructions(self):
        updates = ask_service.review_context_updates(review=a_review(), topic=a_topic())
        assert updates["pageContext"] == ask_service.REVIEW_MODE_PAGE_CONTEXT

    def test_the_topic_id_comes_from_the_review_not_the_topic(self):
        """The review owns the link. A topic fetched by another route could disagree, and the review's
        view is the one the scheduler will write back against."""
        updates = ask_service.review_context_updates(
            review=a_review(topic_id="from_review"), topic=a_topic(id="from_topic")
        )
        assert updates["topicId"] == "from_review"

    def test_next_review_at_is_isoformatted_when_it_is_a_datetime(self):
        from datetime import datetime

        updates = ask_service.review_context_updates(
            review=a_review(next_review_at=datetime(2026, 9, 1, 12, 30)),
            topic=a_topic(),
        )
        assert updates["nextReviewAt"] == "2026-09-01T12:30:00"

    def test_next_review_at_survives_already_being_a_string(self):
        updates = ask_service.review_context_updates(
            review=a_review(next_review_at="2026-09-01"), topic=a_topic()
        )
        assert updates["nextReviewAt"] == "2026-09-01"

    def test_course_fields_need_both_module_and_course(self):
        updates = ask_service.review_context_updates(
            review=a_review(), topic=a_topic(), module=a_module(), course=a_course()
        )
        assert updates["courseId"] == "crs_1"
        assert updates["moduleTitle"] == "Thermodynamics"

    def test_a_module_without_a_course_contributes_nothing(self):
        """This branch differs from the topic chain, deliberately preserved. Pinned so that if someone
        unifies them the change is visible as a failing test rather than a silent prompt change.
        """
        updates = ask_service.review_context_updates(
            review=a_review(), topic=a_topic(), module=a_module(), course=None
        )
        assert "moduleTitle" not in updates
        assert "courseId" not in updates

    def test_a_missing_content_becomes_an_empty_string(self):
        updates = ask_service.review_context_updates(
            review=a_review(), topic=a_topic(content=None)
        )
        assert updates["topicContent"] == ""


class TestNoteContextUpdates:
    def test_the_note_fields_are_always_present(self):
        updates = ask_service.note_context_updates(note=a_note())
        assert updates["noteTitle"] == "My note"
        assert updates["noteContent"] == "note body"
        assert updates["noteSummary"] == "a summary"

    def test_missing_body_and_summary_become_empty_strings(self):
        updates = ask_service.note_context_updates(
            note=a_note(content=None, summary=None)
        )
        assert updates["noteContent"] == ""
        assert updates["noteSummary"] == ""

    def test_a_topic_linked_note_carries_the_topic_chain(self):
        updates = ask_service.note_context_updates(
            note=a_note(), topic=a_topic(), module=a_module(), course=a_course()
        )
        assert updates["topicId"] == "top_1"
        assert updates["moduleTitle"] == "Thermodynamics"
        assert updates["courseId"] == "crs_1"

    def test_a_module_without_a_course_still_gives_a_module_title(self):
        """The topic chain differs from the review branch here. Both are pinned."""
        updates = ask_service.note_context_updates(
            note=a_note(), topic=a_topic(), module=a_module(), course=None
        )
        assert updates["moduleTitle"] == "Thermodynamics"
        assert "courseId" not in updates

    def test_a_direct_course_is_used_when_there_is_no_topic(self):
        updates = ask_service.note_context_updates(
            note=a_note(), topic=None, direct_course=a_course(id="direct")
        )
        assert updates["courseId"] == "direct"

    def test_a_direct_course_is_ignored_when_there_is_a_topic(self):
        """The two routes to a course are mutually exclusive: through the topic's module, or directly.
        A note with both must not have its topic's course overwritten by the direct one.
        """
        updates = ask_service.note_context_updates(
            note=a_note(),
            topic=a_topic(),
            module=a_module(),
            course=a_course(id="via_topic"),
            direct_course=a_course(id="direct"),
        )
        assert updates["courseId"] == "via_topic"

    def test_a_bare_note_contributes_no_topic_or_course_keys(self):
        updates = ask_service.note_context_updates(note=a_note())
        assert set(updates) == {"noteTitle", "noteContent", "noteSummary"}


class TestTopicContextUpdates:
    def test_the_topic_id_is_included_by_default(self):
        assert ask_service.topic_context_updates(topic=a_topic())["topicId"] == "top_1"

    def test_the_topic_id_can_be_withheld(self):
        """The topic branch already has the id from the client's context — it is how the topic was
        found — so writing it back is at best a no-op."""
        updates = ask_service.topic_context_updates(
            topic=a_topic(), include_topic_id=False
        )
        assert "topicId" not in updates
        assert updates["topicTitle"] == "Entropy"

    def test_the_course_needs_a_module(self):
        """A course is reached through a module here, so a course without one is not attached."""
        updates = ask_service.topic_context_updates(
            topic=a_topic(), module=None, course=a_course()
        )
        assert "courseId" not in updates


class TestCourseContextUpdates:
    def test_it_returns_the_title_and_description(self):
        updates = ask_service.course_context_updates(course=a_course())
        assert updates == {"courseTitle": "Physics", "courseDescription": "A course"}

    def test_a_missing_description_becomes_an_empty_string(self):
        assert (
            ask_service.course_context_updates(course=a_course(description=None))[
                "courseDescription"
            ]
            == ""
        )

    def test_it_does_not_write_the_course_id(self):
        """The caller already has it; that is how the course was found."""
        assert "courseId" not in ask_service.course_context_updates(course=a_course())


class TestFormatTopicUserNotes:
    def test_each_note_becomes_a_heading_and_body(self):
        rendered = ask_service.format_topic_user_notes(
            [
                a_note(title="First", content="one"),
                a_note(title="Second", content="two"),
            ]
        )
        assert "## First\none" in rendered
        assert "## Second\ntwo" in rendered

    def test_notes_are_separated_by_a_rule(self):
        """Without a separator two notes read as one document, so a contradiction between them looks
        like a single confused note."""
        rendered = ask_service.format_topic_user_notes(
            [a_note(title="A", content="x"), a_note(title="B", content="y")]
        )
        assert "\n\n---\n\n" in rendered

    def test_a_single_note_has_no_separator(self):
        assert "---" not in ask_service.format_topic_user_notes(
            [a_note(title="Only", content="x")]
        )

    def test_an_untitled_note_gets_a_placeholder_heading(self):
        assert ask_service.format_topic_user_notes(
            [a_note(title=None, content="x")]
        ).startswith("## Note")

    def test_a_note_with_no_body_is_kept_as_a_bare_heading(self):
        """A title alone still tells the model what the learner thought worth recording."""
        assert ask_service.format_topic_user_notes(
            [a_note(title="Just a title", content=None)]
        ) == ("## Just a title")

    def test_no_notes_renders_as_empty(self):
        assert ask_service.format_topic_user_notes([]) == ""

    def test_none_is_tolerated(self):
        assert ask_service.format_topic_user_notes(None) == ""

    def test_whitespace_is_stripped_from_titles_and_bodies(self):
        rendered = ask_service.format_topic_user_notes(
            [a_note(title="  T  ", content="  b  ")]
        )
        assert rendered == "## T\nb"


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------
#
# The first tests in this file covering an *impure* stage. `find_session` is injected so the one
# authorisation rule Ask Maigie has — the learner must own the conversation — is checkable without a
# database or a socket. A second rule for space-room membership was removed with space-room chat; it had
# never executed, because the group lookup it depended on returned `None` unconditionally.


def a_session(session_id="sess_1", user_id="user_1", title=None):
    return SimpleNamespace(id=session_id, user_id=user_id, title=title)


async def no_session(_session_id):
    return None


def resolve_kwargs(**overrides):
    kwargs = {
        "requested_session_id": None,
        "current_session": a_session(),
        "user_id": "user_1",
        "find_session": no_session,
    }
    kwargs.update(overrides)
    return kwargs


class TestSessionResolution:
    @pytest.mark.asyncio
    async def test_no_pinned_id_keeps_the_connections_session(self):
        current = a_session("sess_current")
        resolution = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(current_session=current)
        )
        assert resolution.allowed
        assert resolution.session is current

    @pytest.mark.asyncio
    async def test_a_learner_may_pin_their_own_conversation(self):
        pinned = a_session("sess_pinned", user_id="user_1")

        async def find(session_id):
            return pinned if session_id == "sess_pinned" else None

        resolution = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(requested_session_id="sess_pinned", find_session=find)
        )
        assert resolution.allowed
        assert resolution.session is pinned

    @pytest.mark.asyncio
    async def test_pinning_someone_elses_conversation_is_refused(self):
        """The session id comes from the client on every message. Unchecked, it is a read and a write
        into another learner's thread."""

        async def find(_session_id):
            return a_session("sess_theirs", user_id="user_2")

        resolution = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(requested_session_id="sess_theirs", find_session=find)
        )
        assert not resolution.allowed
        assert resolution.denial == ask_service.SESSION_DENIED_PINNED_OWNER
        assert (
            resolution.session is None
        ), "a refused turn must have nowhere to be written"

    @pytest.mark.asyncio
    async def test_a_missing_pinned_session_falls_back_rather_than_refusing(self):
        """An id that resolves to nothing is not an authorization failure. Preserved from the handler:
        the turn continues on the session the connection was already on."""
        current = a_session("sess_current")
        resolution = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(requested_session_id="sess_gone", current_session=current)
        )
        assert resolution.allowed
        assert resolution.session is current

    @pytest.mark.asyncio
    async def test_a_lookup_failure_refuses_rather_than_using_another_conversation(
        self,
    ):
        """The §5.5.12 fix. A raising lookup used to fall back to the current session, so the turn was
        persisted, metered and charged in a conversation the learner did not pin — and the pinned thread
        showed a gap, so they would ask again and pay twice."""
        current = a_session("sess_current")

        async def find(_session_id):
            raise RuntimeError("database is down")

        resolution = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(
                requested_session_id="sess_pinned",
                current_session=current,
                find_session=find,
            )
        )
        assert not resolution.allowed
        assert resolution.denial == ask_service.SESSION_DENIED_LOOKUP_FAILED
        assert resolution.session is None, (
            "the fallback session is exactly what must not be used — a turn written there is "
            "unfindable by the learner who sent it"
        )

    @pytest.mark.asyncio
    async def test_a_lookup_failure_is_the_only_retryable_refusal(self):
        """A permission refusal will refuse again, so telling the learner to retry would be a lie. A
        read failure is transient, which is what makes refusing better than falling back and not merely
        louder: the learner is told to do the one thing that will work."""

        async def find(_session_id):
            raise RuntimeError("database is down")

        transient = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(requested_session_id="sess_pinned", find_session=find)
        )
        assert transient.retryable

        async def find_theirs(_session_id):
            return a_session("sess_theirs", user_id="user_2")

        forbidden = await ask_service.resolve_session_for_turn(
            **resolve_kwargs(
                requested_session_id="sess_theirs", find_session=find_theirs
            )
        )
        assert not forbidden.retryable

    @pytest.mark.asyncio
    async def test_an_allowed_turn_is_not_marked_retryable(self):
        """`retryable` is a property of a refusal. A successful turn has nothing to retry, and a client
        reading the flag off a non-refusal must not see `True`."""
        resolution = await ask_service.resolve_session_for_turn(**resolve_kwargs())
        assert resolution.allowed
        assert not resolution.retryable

    def test_every_denial_code_has_a_message(self):
        """The handler indexes `SESSION_DENIAL_MESSAGES` by the code, so a code without one is a
        `KeyError` on the refusal path — the path least likely to be exercised by hand.
        """
        codes = {
            ask_service.SESSION_DENIED_PINNED_OWNER,
            ask_service.SESSION_DENIED_LOOKUP_FAILED,
        }
        assert set(ask_service.SESSION_DENIAL_MESSAGES) == codes
        assert all(ask_service.SESSION_DENIAL_MESSAGES[code] for code in codes)

    def test_only_known_codes_are_retryable(self):
        assert ask_service.RETRYABLE_SESSION_DENIALS <= set(
            ask_service.SESSION_DENIAL_MESSAGES
        )

    def test_a_permission_refusal_does_not_invite_a_retry(self):
        """Marking a permission refusal retryable would have the client offer a button that cannot
        work."""
        assert ask_service.SESSION_DENIED_PINNED_OWNER not in (
            ask_service.RETRYABLE_SESSION_DENIALS
        )


class TestNewSessionRow:
    def test_it_is_a_personal_general_conversation(self):
        row = ask_service.new_session_row("user_1")
        assert row["userId"] == "user_1"
        assert row["sessionType"] == ask_service.SESSION_TYPE_GENERAL

    def test_is_space_room_is_written_explicitly(self):
        """The connection's default-session query filters on it. A session created without it is
        invisible to the query meant to find it again, so the learner gets a new conversation on every
        connect."""
        assert row_has_false(ask_service.new_session_row("user_1"), "isSpaceRoom")

    def test_it_starts_on_the_default_title(self):
        assert (
            ask_service.new_session_row("user_1")["title"]
            == ask_service.NEW_CONVERSATION_TITLE
        )


def row_has_false(row, key):
    return key in row and row[key] is False


# ---------------------------------------------------------------------------
# Naming a conversation
# ---------------------------------------------------------------------------


class TestDeriveSessionTitle:
    def test_a_short_question_becomes_its_own_title(self):
        assert (
            ask_service.derive_session_title("What is entropy?") == "What is entropy?"
        )

    def test_whitespace_is_collapsed_before_truncating(self):
        """A pasted question arrives with newlines and runs of spaces. Truncate first and the title can
        be 50 characters of blank space."""
        assert (
            ask_service.derive_session_title("What   is\n\n  entropy?")
            == "What is entropy?"
        )

    def test_a_long_question_is_clipped_and_marked(self):
        title = ask_service.derive_session_title("e" * 200)
        assert len(title) == ask_service.TITLE_MAX_LENGTH + 3
        assert title.endswith("...")

    def test_a_title_exactly_on_the_limit_is_not_marked(self):
        title = ask_service.derive_session_title("e" * ask_service.TITLE_MAX_LENGTH)
        assert title == "e" * ask_service.TITLE_MAX_LENGTH
        assert not title.endswith("...")

    def test_a_blank_message_yields_nothing(self):
        assert ask_service.derive_session_title("   \n ") == ""


def retitle_kwargs(**overrides):
    kwargs = {
        "current_title": ask_service.NEW_CONVERSATION_TITLE,
        "user_message_count": 1,
        "message": "What is entropy?",
        "is_review_thread": False,
    }
    kwargs.update(overrides)
    return kwargs


class TestSessionTitleGate:
    def test_the_first_message_of_an_unnamed_conversation_names_it(self):
        assert ask_service.should_retitle_session(**retitle_kwargs())

    def test_a_named_conversation_keeps_its_name(self):
        """Otherwise the title follows the most recent question rather than identifying the thread, and
        the history panel renames rows under the learner as they type."""
        assert not ask_service.should_retitle_session(
            **retitle_kwargs(current_title="Thermodynamics revision")
        )

    @pytest.mark.parametrize("title", [None, "", "New Chat"])
    def test_an_untouched_title_is_recognised_in_all_its_forms(self, title):
        assert ask_service.should_retitle_session(**retitle_kwargs(current_title=title))

    def test_a_later_message_does_not_rename(self):
        assert not ask_service.should_retitle_session(
            **retitle_kwargs(user_message_count=4)
        )

    def test_a_review_thread_is_never_titled(self):
        assert not ask_service.should_retitle_session(
            **retitle_kwargs(is_review_thread=True)
        )

    def test_a_blank_message_does_not_title(self):
        """An empty title is worse than the default; the default at least says 'new'."""
        assert not ask_service.should_retitle_session(**retitle_kwargs(message="   "))

    def test_the_cheap_gate_agrees_with_the_full_one_on_everything_but_the_count(self):
        """The caller runs the cheap gate first so a named conversation does not pay for a `count(*)` on
        every turn, forever. If the two disagree on a non-count condition, that skip is wrong.
        """
        for override in (
            {"current_title": "Named already"},
            {"is_review_thread": True},
            {"message": " "},
        ):
            kwargs = retitle_kwargs(**override)
            cheap = ask_service.session_needs_a_title(
                current_title=kwargs["current_title"],
                message=kwargs["message"],
                is_review_thread=kwargs["is_review_thread"],
            )
            assert cheap is False
            assert ask_service.should_retitle_session(**kwargs) is False

    def test_the_cheap_gate_passes_where_only_the_count_can_refuse(self):
        kwargs = retitle_kwargs(user_message_count=9)
        assert ask_service.session_needs_a_title(
            current_title=kwargs["current_title"],
            message=kwargs["message"],
            is_review_thread=kwargs["is_review_thread"],
        )
        assert not ask_service.should_retitle_session(**kwargs)


# ---------------------------------------------------------------------------
# Credit refusal
# ---------------------------------------------------------------------------
#
# A billing statement shown to a learner about their own account, built inline between the availability
# check and the frame carrying it — so reaching it needed a live socket and a learner genuinely out of
# credits. It had never been tested.


def usage(**overrides):
    base = {
        "daily_limit": 5_000,
        "credits_used_today": 4_900,
        "credits_used": 40_000,
        "hard_cap": 100_000,
        "period_end": "2026-09-01",
        "next_daily_reset": "midnight",
    }
    base.update(overrides)
    return base


class TestCreditRefusal:
    def test_a_free_learner_over_their_daily_cap_is_told_it_resets(self):
        """A daily cap resets tonight, so the learner waits. Naming the reset is the actionable part."""
        refusal = ask_service.credit_refusal(
            tier="FREE", estimated_tokens=500, credit_usage=usage()
        )
        assert refusal.is_daily_limit
        assert "Daily credit limit" in refusal.message
        assert "midnight" in refusal.message

    def test_a_monthly_cap_names_the_period_end_instead(self):
        """A monthly cap needs an upgrade, not patience. Telling someone to wait until midnight for a
        monthly cap is wrong advice about their own money."""
        refusal = ask_service.credit_refusal(
            tier="FREE",
            estimated_tokens=10,
            credit_usage=usage(credits_used_today=0),
        )
        assert not refusal.is_daily_limit
        assert "Monthly credit limit" in refusal.message
        assert "2026-09-01" in refusal.message

    def test_a_paid_learner_never_gets_the_daily_message(self):
        """Only the free tier has a daily cap. A paid learner whose daily numbers happen to look
        exceeded is over their monthly allowance, not their daily one."""
        refusal = ask_service.credit_refusal(
            tier="PLUS_MONTHLY", estimated_tokens=500, credit_usage=usage()
        )
        assert not refusal.is_daily_limit

    def test_no_configured_daily_cap_is_not_a_cap_of_zero(self):
        """`daily_limit == 0` means no daily cap exists. Read as a cap, every free learner would be
        refused on their first turn."""
        refusal = ask_service.credit_refusal(
            tier="FREE",
            estimated_tokens=1,
            credit_usage=usage(daily_limit=0, credits_used_today=0),
        )
        assert not refusal.is_daily_limit

    def test_this_turns_estimate_counts_towards_the_daily_cap(self):
        """The question is whether *this* turn fits, not whether the learner has already exceeded. A
        learner just under their cap must not be allowed to start a turn that blows through it.
        """
        under = usage(daily_limit=5_000, credits_used_today=4_000)
        assert not ask_service.credit_refusal(
            tier="FREE", estimated_tokens=500, credit_usage=under
        ).is_daily_limit
        assert ask_service.credit_refusal(
            tier="FREE", estimated_tokens=1_500, credit_usage=under
        ).is_daily_limit

    def test_exactly_on_the_daily_cap_is_allowed_through(self):
        """`>` not `>=`: a turn that exactly fills the remaining allowance is affordable."""
        refusal = ask_service.credit_refusal(
            tier="FREE",
            estimated_tokens=1_000,
            credit_usage=usage(daily_limit=5_000, credits_used_today=4_000),
        )
        assert not refusal.is_daily_limit

    def test_the_numbers_are_thousands_separated(self):
        """Six-figure credit counts are unreadable without it, and this is a number the learner is being
        asked to reason about."""
        refusal = ask_service.credit_refusal(
            tier="FREE",
            estimated_tokens=10,
            credit_usage=usage(credits_used_today=0, credits_used=40_000),
        )
        assert "40,000" in refusal.message

    def test_a_missing_daily_reset_still_produces_a_sentence(self):
        """The reset time comes from the billing service and is not guaranteed. Absent, the message says
        "midnight" rather than "None"."""
        credit_usage = usage()
        del credit_usage["next_daily_reset"]
        refusal = ask_service.credit_refusal(
            tier="FREE", estimated_tokens=500, credit_usage=credit_usage
        )
        assert "None" not in refusal.message
        assert "midnight" in refusal.message

    def test_the_tier_is_carried_through_for_the_client(self):
        refusal = ask_service.credit_refusal(
            tier="FREE", estimated_tokens=500, credit_usage=usage()
        )
        assert refusal.tier == "FREE"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidateMessage:
    def test_a_real_question_is_accepted(self):
        assert ask_service.validate_message("What is entropy?") is None

    def test_an_empty_message_is_refused(self):
        rejection = ask_service.validate_message("")
        assert rejection.code == ask_service.MESSAGE_REJECTED_EMPTY

    @pytest.mark.parametrize("blank", ["   ", "\n\n", "\t", " \r\n "])
    def test_whitespace_only_is_empty(self, blank):
        """A message of spaces reaches the model as nothing and produces an answer to nothing, which is
        worse than a refusal."""
        assert (
            ask_service.validate_message(blank).code
            == ask_service.MESSAGE_REJECTED_EMPTY
        )

    def test_none_is_refused_rather_than_raising(self):
        assert ask_service.validate_message(None) is not None

    def test_a_message_on_the_limit_is_accepted(self):
        assert (
            ask_service.validate_message("e" * ask_service.MESSAGE_MAX_LENGTH) is None
        )

    def test_a_message_over_the_limit_is_refused(self):
        rejection = ask_service.validate_message(
            "e" * (ask_service.MESSAGE_MAX_LENGTH + 1)
        )
        assert rejection.code == ask_service.MESSAGE_REJECTED_TOO_LONG

    def test_the_refusal_says_both_numbers(self):
        """A limit the learner cannot see is a limit they cannot work around."""
        rejection = ask_service.validate_message(
            "e" * (ask_service.MESSAGE_MAX_LENGTH + 500)
        )
        assert f"{ask_service.MESSAGE_MAX_LENGTH:,}" in rejection.message
        assert f"{ask_service.MESSAGE_MAX_LENGTH + 500:,}" in rejection.message

    def test_length_is_measured_on_the_raw_message_not_the_stripped_one(self):
        """Whitespace still costs prompt budget, so padding is not a way past the limit."""
        padded = "e" * (ask_service.MESSAGE_MAX_LENGTH - 10) + " " * 100
        assert (
            ask_service.validate_message(padded).code
            == ask_service.MESSAGE_REJECTED_TOO_LONG
        )

    def test_the_limit_is_generous_enough_for_a_pasted_essay(self):
        """A learner pasting something long to ask about is the point of the surface, not abuse of it.
        This pins intent: if the limit is ever tightened to a few hundred characters, that is a product
        change and this test should be the thing that argues with it."""
        assert ask_service.MESSAGE_MAX_LENGTH >= 8_000


# ---------------------------------------------------------------------------
# One turn at a time, per conversation
# ---------------------------------------------------------------------------
#
# Plan §4.5.13. Both clients disable the composer while a turn is in flight, so a second concurrent turn
# needs a client bug, a second tab or a script — and every consequence is silent. Two turns on one
# session interleave their history reads, so the second builds its prompt from a thread missing the
# first's question; both write assistant rows in provider-response order; and both charge.


class TestTurnInFlight:
    @pytest.mark.asyncio
    async def test_a_session_can_answer(self):
        async with ask_service.turn_in_flight("sess_1"):
            assert "sess_1" in ask_service.turns_in_flight()

    @pytest.mark.asyncio
    async def test_the_slot_is_released_afterwards(self):
        async with ask_service.turn_in_flight("sess_1"):
            pass
        assert "sess_1" not in ask_service.turns_in_flight()

    @pytest.mark.asyncio
    async def test_a_second_turn_on_the_same_session_is_refused(self):
        async with ask_service.turn_in_flight("sess_1"):
            with pytest.raises(ask_service.TurnAlreadyInFlight):
                async with ask_service.turn_in_flight("sess_1"):
                    raise AssertionError("the second turn should not have started")

    @pytest.mark.asyncio
    async def test_a_different_session_is_unaffected(self):
        """The guard is per conversation, not per learner. A learner with two conversations open is
        doing something reasonable."""
        async with ask_service.turn_in_flight("sess_1"), ask_service.turn_in_flight(
            "sess_2"
        ):
            assert ask_service.turns_in_flight() == frozenset({"sess_1", "sess_2"})

    @pytest.mark.asyncio
    async def test_a_failed_turn_frees_the_slot(self):
        """Released in a `finally`. Otherwise one provider timeout locks the learner out of their own
        conversation until the process restarts — a worse failure than the one this prevents.
        """
        with pytest.raises(RuntimeError):
            async with ask_service.turn_in_flight("sess_1"):
                raise RuntimeError("provider down")
        assert "sess_1" not in ask_service.turns_in_flight()

    @pytest.mark.asyncio
    async def test_a_cancelled_turn_frees_the_slot(self):
        with pytest.raises(asyncio.CancelledError):
            async with ask_service.turn_in_flight("sess_1"):
                raise asyncio.CancelledError
        assert "sess_1" not in ask_service.turns_in_flight()

    @pytest.mark.asyncio
    async def test_a_refused_second_turn_does_not_release_the_first(self):
        """The refusal must not free a slot it never held, or the guard would let the *third* turn in
        while the first is still running."""
        async with ask_service.turn_in_flight("sess_1"):
            with pytest.raises(ask_service.TurnAlreadyInFlight):
                async with ask_service.turn_in_flight("sess_1"):
                    pass
            assert "sess_1" in ask_service.turns_in_flight()

    @pytest.mark.asyncio
    async def test_the_refusal_is_retryable(self):
        """Unlike a validation refusal, the same message works once the turn in flight finishes."""
        busy = ask_service.TurnAlreadyInFlight("sess_1")
        assert busy.rejection.code == ask_service.MESSAGE_REJECTED_TURN_IN_FLIGHT
        assert "still answering" in busy.rejection.message

    @pytest.mark.asyncio
    async def test_concurrent_turns_are_serialised_by_refusal_not_by_queueing(self):
        """**Refused, not queued**, and this is the decision. A queued turn answers a question whose
        context has moved — the learner has since asked something else or navigated away from the page
        the context described — so it is a correct answer to a question nobody is still asking.
        """
        started: list[str] = []

        async def turn(name: str):
            try:
                async with ask_service.turn_in_flight("sess_1"):
                    started.append(name)
                    await asyncio.sleep(0.01)
            except ask_service.TurnAlreadyInFlight:
                started.append(f"{name}:refused")

        await asyncio.gather(turn("a"), turn("b"))
        assert sorted(started) in (["a", "b:refused"], ["a:refused", "b"])


# ---------------------------------------------------------------------------
# Scope honesty
# ---------------------------------------------------------------------------
#
# Decision G. Retrieval v1 is budgeted excerpting over what the client put in scope, not library-wide
# recall, and `rag_service.available` is False until a vector backend exists. An answer from one topic's
# notes and an answer from a whole library are different claims; rendering them identically asserts the
# stronger one.


class TestDescribeScope:
    def test_an_answer_with_no_context_names_nothing(self):
        scope = ask_service.describe_scope(context=None)
        assert scope.sources == []
        assert not scope.grounded

    def test_the_material_that_reached_the_prompt_is_named(self):
        scope = ask_service.describe_scope(
            context={"topicTitle": "Entropy", "courseTitle": "Physics"}
        )
        assert scope.sources == ["topic", "course"]

    def test_sources_run_most_specific_first(self):
        """The order a learner reads their own context in — the note they are looking at before the
        course it belongs to."""
        scope = ask_service.describe_scope(
            context={
                "courseTitle": "Physics",
                "noteTitle": "My note",
                "topicTitle": "Entropy",
            }
        )
        assert scope.sources == ["note", "topic", "course"]

    def test_library_recall_is_false_by_default(self):
        """**The load-bearing field.** While this is false, a client must not render "I could not find
        anything about that" as a statement about the learner's library."""
        assert (
            ask_service.describe_scope(context={"topicTitle": "T"}).library_recall
            is False
        )

    def test_library_recall_is_reported_when_the_backend_exists(self):
        scope = ask_service.describe_scope(
            context={"topicTitle": "T"}, library_recall=True
        )
        assert scope.library_recall is True

    def test_retrieval_and_memory_are_named_as_sources(self):
        scope = ask_service.describe_scope(
            context={
                "retrieved_items": ["- NOTE: x"],
                "memory_context": "They revise at night.",
            }
        )
        assert "a search of your material" in scope.sources
        assert "what Maigie remembers about you" in scope.sources

    def test_pasted_text_is_named_as_pasted(self):
        """Distinguishable from a note that was fetched, because the learner can tell whether Maigie read
        their note or only the paragraph they sent."""
        assert (
            "the text you pasted"
            in ask_service.describe_scope(context={"content": "a paragraph"}).sources
        )

    def test_an_empty_value_is_not_a_source(self):
        """Enrichment writes `""` for a note with no body. Naming that would claim Maigie read something
        it did not."""
        scope = ask_service.describe_scope(
            context={"topicUserNotes": "", "topicTitle": "Entropy"}
        )
        assert scope.sources == ["topic"]

    def test_grounded_means_it_drew_on_the_learners_material(self):
        assert ask_service.describe_scope(context={"noteTitle": "n"}).grounded
        assert not ask_service.describe_scope(context={"sessionId": "s"}).grounded

    def test_an_id_alone_is_not_a_source(self):
        """`topicId` on the context means the client asked; `topicTitle` means enrichment succeeded. If a
        read was refused the two differ, and the honest answer is what the model actually saw.
        """
        assert ask_service.describe_scope(context={"topicId": "topic_1"}).sources == []
