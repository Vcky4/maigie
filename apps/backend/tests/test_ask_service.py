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
