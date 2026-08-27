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

from types import SimpleNamespace

import pytest

from src.domains.intelligence.conversation import ask_service


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
        context = ask_service.AskContext.from_client({"noteContent": "...", "pageContext": "review"})
        assert context.raw["noteContent"] == "..."
        assert context.raw["pageContext"] == "review"

    def test_raw_is_a_copy_not_the_callers_dict(self):
        original = {"sessionId": "sess_1"}
        context = ask_service.AskContext.from_client(original)
        original["sessionId"] = "mutated"
        assert context.raw["sessionId"] == "sess_1"

    def test_a_review_thread_is_recognised(self):
        assert ask_service.AskContext.from_client({"reviewItemId": "r1"}).is_review_thread
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
        message, context, history = "Explain the second law", {"courseId": "c1"}, [{"role": "user"}]
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
            [{"similarity": 0.2, "objectType": "note", "objectId": "n1", "data": {"title": "X"}}]
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
            [{"score": 0.91, "objectType": "course", "objectId": "c1", "data": {"title": "Physics"}}]
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
        "message", ["show my courses", "what are my goals", "list my notes", "SHOW MY SCHEDULE"]
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
        course cards then answers a question the learner did not ask, and buries the thing they did."""
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
                message="show my courses", executed_actions=[{"type": "get_user_courses"}]
            )
            is True
        )


class TestSkillBadges:
    @staticmethod
    def tool_badge(name):
        return {"courses": {"id": "courses", "name": "Course Management", "icon": "book-open"}}.get(
            {"get_user_courses": "courses", "create_course": "courses"}.get(name, "")
        )

    @staticmethod
    def query_badge(query_type):
        return {"goals": {"id": "goals", "name": "Goal Management", "icon": "target"}}.get(query_type)

    def test_a_badge_is_built_from_an_executed_action(self):
        badges = ask_service.build_skill_badges(
            executed_actions=[{"type": "create_course"}],
            query_results=[],
            tool_badge=self.tool_badge,
            query_badge=self.query_badge,
        )
        assert badges == [{"id": "courses", "name": "Course Management", "icon": "book-open"}]

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
                message_row("USER", "What is entropy?", image_urls=None, image_url=None),
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
            [message_row("USER", "This one?", image_urls=None, image_url="https://cdn/old.png")]
        )
        assert history[0]["parts"] == ["This one?", "https://cdn/old.png"]

    def test_the_plural_column_wins_when_both_are_set(self):
        history = ask_service.format_history(
            [
                message_row(
                    "USER", "?", image_urls=["https://cdn/new.png"], image_url="https://cdn/old.png"
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
        expected = {
            "token estimation": "estimate_turn_tokens",
            "retrieval gate": "should_retrieve",
            "explicit-view gate": "should_render_query_components",
            "skill badges": "build_skill_badges",
            "history formatting": "format_history",
            "usage reconciliation and pricing": "resolve_usage",
            "assistant row assembly": "build_assistant_row",
            "context cache keying": "context_cache_key_parts",
        }
        assert set(ask_service.MOVED_SO_FAR) == set(expected)
        for stage, attribute in expected.items():
            assert hasattr(ask_service, attribute), f"{stage} is claimed as moved but {attribute} is absent"

    def test_the_two_inventories_do_not_overlap(self):
        assert not set(ask_service.MOVED_SO_FAR) & set(ask_service.STILL_IN_THE_HANDLER)

    def test_answer_is_not_yet_published(self):
        """`answer()` is the destination (Decision C). Until the impure stages move it would be a
        facade over a pipeline that still lives elsewhere, and a facade is how two pipelines start."""
        assert not hasattr(ask_service, "answer"), (
            "ask_service.answer now exists. Remove this test, and make sure "
            "STILL_IN_THE_HANDLER reflects what genuinely moved."
        )


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
        paid = resolve({"input_tokens": 100, "output_tokens": 10}, user_tier="PREMIUM_MONTHLY")
        assert free.revenue_usd != paid.revenue_usd

    def test_the_model_name_is_carried_through(self):
        usage = resolve({"input_tokens": 1, "output_tokens": 1}, model_name="openai:gpt-4o-mini")
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
        ):
            assert key in row, f"{key} missing"
        assert row["role"] == "ASSISTANT"

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
        for key in ("replyToMessageId", "componentData", "suggestionText", "citations", "truncated"):
            assert key not in row, f"{key} should be omitted when not supplied"

    def test_components_are_included_when_present(self):
        row = a_row(components=[{"type": "course_card"}])
        assert row["componentData"] == [{"type": "course_card"}]

    def test_empty_components_are_omitted(self):
        assert "componentData" not in a_row(components=[])

    def test_suggestion_text_is_included_when_present(self):
        assert a_row(suggestion_text="Try a quiz next.")["suggestionText"] == "Try a quiz next."

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

    @pytest.mark.parametrize("id_name", ["noteId", "topicId", "courseId", "reviewItemId"])
    def test_each_id_alone_produces_a_key(self, id_name):
        parts = ask_service.context_cache_key_parts(user_id="u1", context={id_name: "x1"})
        assert parts is not None
        assert "x1" in parts

    @pytest.mark.parametrize("id_name", ["noteId", "topicId", "courseId", "reviewItemId"])
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
        by_note = ask_service.context_cache_key_parts(user_id="u1", context={"noteId": "x"})
        by_topic = ask_service.context_cache_key_parts(user_id="u1", context={"topicId": "x"})
        assert by_note != by_topic

    def test_the_key_is_namespaced(self):
        parts = ask_service.context_cache_key_parts(user_id="u1", context={"topicId": "t"})
        assert parts[:2] == ["chat", "context"]

    def test_it_is_stable_for_the_same_input(self):
        context = {"topicId": "t", "courseId": "c"}
        assert ask_service.context_cache_key_parts(
            user_id="u1", context=context
        ) == ask_service.context_cache_key_parts(user_id="u1", context=context)

    def test_unread_ids_are_deliberately_absent(self):
        """`examPrepId` and `spaceId` ride on the context but enrichment does not read them, so they
        must not be in the key. This test is the reminder to add them here in the same change that
        starts reading them — it is documenting the audit, not asserting they are unimportant."""
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
        enriched = {"courseTitle": "Thermo", "courseDescription": "d", "moduleTitle": "m"}
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
        merged = ask_service.merge_cached_context({"topicId": "t"}, {"topicTitle": "Entropy"})
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
