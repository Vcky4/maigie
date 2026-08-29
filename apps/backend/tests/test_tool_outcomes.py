"""What a turn's tool calls produce, and in what order.

None of this had ever been tested. It was one branchy loop inside `register_chat_websocket_routes` doing
five effects per action — writing an `AIActionLog` row, sending a credit-limit frame on the connection,
sending an `event` frame to the user, queueing a Celery task, formatting a component — so reaching it
needed a live socket, a live database and a live model that actually decided to call a tool.

Interleaving five effects is also why its ordering rules were invisible. Two of them matter and are
pinned below: query components come before action components, and an action refused for credit
contributes its refusal and nothing else.
"""

from __future__ import annotations

import pytest

from src.domains.intelligence.conversation import tool_outcomes

DEEP_LINK = "maigie://purchase"


def action(kind, *, status="success", data=None, **result):
    return {"type": kind, "data": data or {}, "result": {"status": status, **result}}


def fake_list(*, component_type, items, text):
    return {"type": "component", "componentType": component_type, "items": items, "text": text}


def fake_action_component(*, action_type, action_result, action_data, user_id, db):
    return {"type": "component", "componentType": f"{action_type}Card"}


def collect(**overrides):
    kwargs = {
        "message": "show my courses",
        "user_id": "user_1",
        "user_message_id": "msg_1",
        "executed_actions": [],
        "query_results": [],
        "format_list": fake_list,
        "format_action": fake_action_component,
        "purchase_deep_link": DEEP_LINK,
    }
    kwargs.update(overrides)
    return tool_outcomes.collect_tool_outcomes(**kwargs)


class TestQueryResultMessage:
    def test_one_row_reads_singular(self):
        assert (
            tool_outcomes.query_result_message(query_type="courses", count=1)
            == "Here is your course:"
        )

    def test_many_rows_are_counted(self):
        assert (
            tool_outcomes.query_result_message(query_type="goals", count=4)
            == "Here are your 4 goals:"
        )

    def test_the_zero_case_is_defined_even_though_the_caller_cannot_reach_it(self):
        """The loop only builds a component for non-empty data, so this has never been rendered. Defined
        anyway: a caller that stops filtering empties should get a sentence, not a crash."""
        assert "don't have any" in tool_outcomes.query_result_message(query_type="notes", count=0)


class TestQueryComponents:
    def test_asking_to_see_data_produces_a_card(self):
        outcomes = collect(
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c1"}]}
            ]
        )
        assert outcomes.components[0]["componentType"] == "CourseList"

    def test_a_turn_that_also_created_something_renders_no_cards(self):
        """The created thing is the answer. Course cards here would answer a question nobody asked."""
        outcomes = collect(
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c1"}]}
            ],
            executed_actions=[action("create_course", course_id="c9")],
        )
        assert not any(c.get("componentType") == "CourseList" for c in outcomes.components)

    def test_a_message_that_did_not_ask_to_see_data_renders_no_cards(self):
        outcomes = collect(
            message="make me a study plan",
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c1"}]}
            ],
        )
        assert outcomes.components == []

    def test_an_empty_result_set_contributes_nothing(self):
        outcomes = collect(
            query_results=[{"query_type": "courses", "component_type": "CourseList", "data": []}]
        )
        assert outcomes.components == []

    def test_a_result_with_no_component_type_contributes_nothing(self):
        outcomes = collect(
            query_results=[{"query_type": "courses", "component_type": "", "data": [{"id": "c"}]}]
        )
        assert outcomes.components == []

    def test_a_formatter_returning_nothing_contributes_nothing(self):
        """`format_list_component_response` is still an unimplemented stub returning `{}`. Without this
        check an empty dict would reach the client as a component."""
        outcomes = collect(
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c"}]}
            ],
            format_list=lambda **_: {},
        )
        assert outcomes.components == []


class TestActionLogs:
    def test_a_successful_action_is_logged(self):
        row = tool_outcomes.build_action_log_row(
            message_id="msg_1",
            action_type="create_course",
            action_data={"title": "Physics"},
            action_result={"status": "success"},
        )
        assert row["status"] == "SUCCESS"
        assert row["error"] is None
        assert row["actionData"] == {"title": "Physics"}

    def test_a_failed_action_is_logged_with_its_message(self):
        """The only record that a tool ran at all. The component and the event are both success-shaped,
        so a turn whose tool failed otherwise looks like a turn that used no tools."""
        row = tool_outcomes.build_action_log_row(
            message_id="msg_1",
            action_type="create_course",
            action_data=None,
            action_result={"status": "error", "message": "Topic limit reached"},
        )
        assert row["status"] == "FAILED"
        assert row["error"] == "Topic limit reached"

    def test_absent_action_data_is_an_empty_dict_not_null(self):
        """The column records what the model asked for; a null cannot be told apart from a tool that
        takes no arguments."""
        row = tool_outcomes.build_action_log_row(
            message_id="msg_1",
            action_type="complete_review",
            action_data=None,
            action_result={"status": "success"},
        )
        assert row["actionData"] == {}

    def test_every_action_is_logged_including_a_refused_one(self):
        outcomes = collect(
            executed_actions=[
                action("create_course", status="error", credit_limit_error=True),
                action("complete_review"),
            ]
        )
        assert [row["actionType"] for row in outcomes.action_logs] == [
            "create_course",
            "complete_review",
        ]


class TestActionEvents:
    @pytest.mark.parametrize("kind", ["create_course", "complete_review", "update_course_outline"])
    def test_the_three_announcing_actions_announce(self, kind):
        """The frame is how the client knows to navigate or refetch — a created course has to appear in
        the sidebar. A missing event is a UI that silently disagrees with the database."""
        event = tool_outcomes.action_event(action_type=kind, action_result={"status": "success"})
        assert event["payload"]["action"] == kind

    def test_another_action_does_not_announce(self):
        assert (
            tool_outcomes.action_event(
                action_type="create_note", action_result={"status": "success"}
            )
            is None
        )

    def test_a_failed_action_does_not_announce(self):
        assert (
            tool_outcomes.action_event(
                action_type="create_course", action_result={"status": "error"}
            )
            is None
        )

    def test_the_course_id_is_sent_under_both_spellings(self):
        """The two clients read different ones and neither reads both, so sending one breaks the other."""
        event = tool_outcomes.action_event(
            action_type="create_course",
            action_result={"status": "success", "course_id": "c1"},
        )
        assert event["payload"]["course_id"] == "c1"
        assert event["payload"]["courseId"] == "c1"

    def test_the_camel_case_course_id_is_read_as_a_fallback(self):
        event = tool_outcomes.action_event(
            action_type="update_course_outline",
            action_result={"status": "success", "courseId": "c2"},
        )
        assert event["payload"]["course_id"] == "c2"

    def test_an_action_with_no_course_carries_none(self):
        event = tool_outcomes.action_event(
            action_type="complete_review", action_result={"status": "success"}
        )
        assert "course_id" not in event["payload"]

    def test_the_tools_own_message_wins_over_the_default(self):
        event = tool_outcomes.action_event(
            action_type="complete_review",
            action_result={"status": "success", "message": "5/5 correct."},
        )
        assert event["payload"]["message"] == "5/5 correct."


class TestCreditRefusal:
    def test_a_course_refused_for_credit_produces_a_refusal(self):
        frame = tool_outcomes.credit_limit_frame(
            action_type="create_course",
            action_result={"status": "error", "credit_limit_error": True},
            purchase_deep_link=DEEP_LINK,
        )
        assert frame["type"] == "credit_limit_error"
        assert frame["blocked"] is True
        assert frame["purchaseDeepLink"] == DEEP_LINK

    def test_another_failing_action_is_not_a_credit_refusal(self):
        assert (
            tool_outcomes.credit_limit_frame(
                action_type="create_goal",
                action_result={"status": "error", "credit_limit_error": True},
                purchase_deep_link=DEEP_LINK,
            )
            is None
        )

    def test_a_successful_course_is_not_a_refusal(self):
        assert (
            tool_outcomes.credit_limit_frame(
                action_type="create_course",
                action_result={"status": "success"},
                purchase_deep_link=DEEP_LINK,
            )
            is None
        )

    def test_a_refused_action_contributes_its_refusal_and_nothing_else(self):
        """Preserved from a bare `continue` in the loop, and it is correct: a course that was not
        created must not appear as a card, and the refusal is the only honest thing to show."""
        outcomes = collect(
            executed_actions=[action("create_course", status="error", credit_limit_error=True)]
        )
        assert len(outcomes.connection_errors) == 1
        assert outcomes.events == []
        assert outcomes.components == []
        assert len(outcomes.action_logs) == 1

    def test_a_refusal_goes_on_the_connection_not_to_the_user(self):
        """Separate lists because they are sent differently: this refusal belongs to the socket that
        asked, where an `event` goes to the learner's other tabs too."""
        outcomes = collect(
            executed_actions=[action("create_course", status="error", credit_limit_error=True)]
        )
        assert outcomes.connection_errors and not outcomes.events


class TestBackgroundWork:
    def test_a_recommendation_is_queued_rather_than_run(self):
        outcomes = collect(
            executed_actions=[
                action("recommend_resources", data={"query": "entropy", "topicId": "t1"})
            ]
        )
        name, kwargs = outcomes.background_tasks[0]
        assert name == tool_outcomes.RESOURCE_RECOMMENDATION_TASK
        assert kwargs["query"] == "entropy"
        assert kwargs["topic_id"] == "t1"
        assert kwargs["user_id"] == "user_1"

    def test_the_limit_has_a_default(self):
        _, kwargs = tool_outcomes.resource_recommendation_task(user_id="user_1", action_data={})
        assert kwargs["limit"] == 10

    def test_no_other_action_queues_work(self):
        outcomes = collect(executed_actions=[action("create_course")])
        assert outcomes.background_tasks == []


class TestOrdering:
    def test_query_components_come_before_action_components(self):
        """The order the learner reads: what they asked to see, then what changed as a result.

        The action has to be a non-mutating one — `complete_review` rather than anything `create_*` —
        because a turn that created something suppresses query cards entirely. Which is the gate two
        tests above, and the reason this test would otherwise pass for the wrong reason.
        """
        outcomes = collect(
            message="show my courses",
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c1"}]}
            ],
            executed_actions=[action("complete_review")],
        )
        assert [c["componentType"] for c in outcomes.components] == [
            "CourseList",
            "complete_reviewCard",
        ]

    def test_events_keep_the_order_the_work_happened_in(self):
        outcomes = collect(executed_actions=[action("complete_review"), action("create_course")])
        assert [e["payload"]["action"] for e in outcomes.events] == [
            "complete_review",
            "create_course",
        ]

    def test_a_turn_with_no_tool_calls_produces_nothing(self):
        outcomes = collect(message="what is entropy?")
        assert outcomes.action_logs == []
        assert outcomes.events == []
        assert outcomes.components == []
        assert outcomes.connection_errors == []
        assert outcomes.background_tasks == []

    def test_nothing_is_sent_or_written_by_collecting(self):
        """The whole design: every effect is the caller's. A formatter that raised would be the only way
        this function could touch the world, and it does not catch, so that surfaces."""
        called: list[str] = []

        def noisy_list(**_kwargs):
            called.append("list")
            return None

        def noisy_action(**_kwargs):
            called.append("action")
            return None

        collect(
            query_results=[
                {"query_type": "courses", "component_type": "CourseList", "data": [{"id": "c"}]}
            ],
            executed_actions=[action("complete_review")],
            format_list=noisy_list,
            format_action=noisy_action,
        )
        assert called == ["list", "action"], "only the injected formatters should have been called"
