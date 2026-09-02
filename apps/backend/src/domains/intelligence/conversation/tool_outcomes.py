"""What a turn's tool calls produced, turned into things the caller can send and write.

**Nothing here sends a frame, writes a row or queues a task.** Every function returns a value and the
caller performs the effect — the same choice `ask_service.build_assistant_row` makes, for the same
reason: the decisions are what can be wrong, and they were previously reachable only by driving a live
socket against a live model that actually called a tool. So none of this had ever been tested.

The loop this replaces did five things per action, interleaved: wrote an `AIActionLog` row, sent a
credit-limit frame on the connection, sent an `event` frame to the user, queued a Celery task, and
formatted a component. Five effects in one branchy loop is why the ordering rules below were invisible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Actions that report themselves to the client with an `event` frame, and the wording each uses when
#: the tool did not supply one. Only these three do; every other action is reported by its component.
_ACTION_EVENTS: dict[str, str] = {
    "create_course": "Course created successfully!",
    "complete_review": "Review completed!",
    "update_course_outline": "Course outline updated!",
}

#: Actions whose `event` payload carries the course they acted on. Sent under both spellings because
#: the two clients disagree on which they read, and dropping either breaks one of them.
_ACTIONS_CARRYING_COURSE_ID = frozenset({"create_course", "update_course_outline"})

#: The Celery task that turns a `recommend_resources` tool call into actual recommendations.
RESOURCE_RECOMMENDATION_TASK = "resources.recommend_from_chat"


@dataclass(frozen=True, slots=True)
class ToolOutcomes:
    """Everything a turn's tool calls produced, in the order it should reach the learner.

    Four separate lists rather than one, because they are performed differently: rows are written,
    `events` and `components` go to the user's other tabs as well as this socket, `connection_errors` go
    only to the socket that asked, and `background_tasks` go to a queue. Collapsing them would force the
    caller to switch on content to decide how to send.
    """

    action_logs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    connection_errors: list[dict[str, Any]] = field(default_factory=list)
    background_tasks: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Query results — the "show me my data" components
# ---------------------------------------------------------------------------


def query_result_message(*, query_type: str, count: int) -> str:
    """The line above a list of the learner's own data.

    Singular strips the trailing "s" from the query type, which is why `query_type` is expected plural
    — `courses`, `goals`, `notes`. It is the tool's own name for what it returned, not something this
    function chooses.

    **The zero case is unreachable from the current caller** and is defined anyway: the loop only builds
    a component when `data` is non-empty, so "you don't have any yet" has never been rendered. Kept
    because a total function is easier to reason about than one with an implicit precondition, and
    because a caller that stops filtering empties should get a sentence rather than a crash.
    """
    if count == 0:
        return f"You don't have any {query_type} yet."
    if count == 1:
        return f"Here is your {query_type[:-1]}:"
    return f"Here are your {count} {query_type}:"


def build_query_components(
    *,
    message: str,
    executed_actions: list[dict[str, Any]] | None,
    query_results: list[dict[str, Any]] | None,
    format_list: Any,
) -> list[dict[str, Any]]:
    """Cards for the data the learner asked to see, or nothing.

    The gate is `ask_service.should_render_query_components`, and both halves of it matter: the learner
    must have asked to *see* the data, and the turn must not also have created or updated something — in
    which case the created thing is the answer and course cards would answer a question nobody asked.

    A result with no rows, or no component type to render it as, contributes nothing. `format_list` is
    injected because it is `component_response.format_list_component_response`, which is still an
    unimplemented stub returning `{}` — so this returns `[]` in production today, and the falsy check
    below is what stops empty dicts reaching the client as components.
    """
    from . import ask_service

    if not ask_service.should_render_query_components(
        message=message, executed_actions=executed_actions
    ):
        return []

    components: list[dict[str, Any]] = []
    for result in query_results or []:
        data = result.get("data") or []
        component_type = result.get("component_type") or ""
        if not (data and component_type):
            continue
        component = format_list(
            component_type=component_type,
            items=data,
            text=query_result_message(query_type=result.get("query_type", ""), count=len(data)),
        )
        if component:
            components.append(component)
    return components


# ---------------------------------------------------------------------------
# Executed actions
# ---------------------------------------------------------------------------


def build_action_log_row(
    *,
    message_id: str,
    action_type: str,
    action_data: dict[str, Any] | None,
    action_result: dict[str, Any],
) -> dict[str, Any]:
    """The `AIActionLog` row for one tool call, in the repository's wire shape.

    **Logged whether it succeeded or failed**, and the failure message is kept. This is the only record
    that a tool ran at all: the component and the event are both success-shaped, so a turn whose tool
    failed looks from the outside like a turn that used no tools.

    `actionData` defaults to `{}` rather than `None` because the column is the record of what the model
    asked for, and a null there cannot be told apart from a tool that takes no arguments.
    """
    succeeded = action_result.get("status") == "success"
    return {
        "messageId": message_id,
        "actionType": action_type,
        "actionData": action_data if action_data else {},
        "status": "SUCCESS" if succeeded else "FAILED",
        "error": (
            None if succeeded else action_result.get("message") or action_result.get("error")
        ),
    }


def action_event(*, action_type: str, action_result: dict[str, Any]) -> dict[str, Any] | None:
    """The `event` frame announcing a completed action, or `None` if this action does not announce.

    Only three actions do, and only on success. The frame is how the client knows to navigate or refetch
    — a created course has to appear in the sidebar, a completed review has to leave the queue — so a
    missing event is a UI that silently disagrees with the database.

    **`course_id` is sent under both spellings on purpose.** The two clients read different ones and
    neither reads both, so sending one would break the other. Recorded rather than normalised because
    normalising it is a coordinated change across three repositories, like the `is_final` case.
    """
    if action_result.get("status") != "success":
        return None
    default_message = _ACTION_EVENTS.get(action_type)
    if default_message is None:
        return None

    payload: dict[str, Any] = {
        "status": "success",
        "action": action_type,
        "message": action_result.get("message", default_message),
    }
    if action_type in _ACTIONS_CARRYING_COURSE_ID:
        course_id = action_result.get("course_id") or action_result.get("courseId")
        payload["course_id"] = course_id
        payload["courseId"] = course_id
    return {"type": "event", "payload": payload}


def credit_limit_frame(
    *, action_type: str, action_result: dict[str, Any], upgrade_deep_link: str
) -> dict[str, Any] | None:
    """The refusal frame for a tool that exhausted the learner's allowance, or `None`.

    Only `create_course` reports this, because course generation is the only tool that spends enough to
    be refused on its own. The turn's own allowance was already checked before generation; this is the
    *tool* running out part-way through a turn that was affordable when it started.

    `is_daily_limit` and `show_referral_option` are replaced by `windowResetsAt` — one window, one
    remedy, and a timestamp instead of a category. See `ask_service.CreditRefusal`.
    """
    if action_type != "create_course" or not action_result.get("credit_limit_error"):
        return None
    return {
        "type": "credit_limit_error",
        "message": action_result.get("message", "Usage limit reached."),
        "tier": action_result.get("tier", "FREE"),
        "windowResetsAt": action_result.get("windowResetsAt"),
        "blocked": True,
        "upgradeDeepLink": upgrade_deep_link,
    }


def resource_recommendation_task(
    *, user_id: str, action_data: dict[str, Any] | None
) -> tuple[str, dict[str, Any]] | None:
    """The background job a `recommend_resources` call needs, as `(task_name, kwargs)`.

    Queued rather than run inline because recommendation searches external sources, and a learner should
    not wait on that to read the rest of their answer.
    """
    data = action_data or {}
    return (
        RESOURCE_RECOMMENDATION_TASK,
        {
            "user_id": user_id,
            "query": data.get("query", ""),
            "topic_id": data.get("topicId"),
            "course_id": data.get("courseId"),
            "limit": data.get("limit", 10),
        },
    )


def collect_tool_outcomes(
    *,
    message: str,
    user_id: str,
    user_message_id: str,
    executed_actions: list[dict[str, Any]] | None,
    query_results: list[dict[str, Any]] | None,
    format_list: Any,
    format_action: Any,
    upgrade_deep_link: str,
) -> ToolOutcomes:
    """Everything a turn's tool calls produced, gathered without performing any of it.

    **Query components come before action components**, which is the order the learner reads: what they
    asked to see, then what changed as a result.

    **An action refused for credit contributes its log row and its refusal, and nothing else** — no
    event and no component. That is preserved from the loop, where it was a bare `continue`, and it is
    correct: a course that was not created must not appear as a card, and the refusal is the only honest
    thing to show.

    `format_list` and `format_action` are injected because both are still unimplemented stubs in
    `component_response`. That means **`components` is empty in production today** — the tool loop
    reaches the client as events only. Injecting them keeps this testable now and makes implementing
    them a change in one place.
    """
    outcomes = ToolOutcomes(
        components=build_query_components(
            message=message,
            executed_actions=executed_actions,
            query_results=query_results,
            format_list=format_list,
        )
    )

    for action in executed_actions or []:
        action_type = action["type"]
        action_data = action["data"]
        action_result = action["result"]

        outcomes.action_logs.append(
            build_action_log_row(
                message_id=user_message_id,
                action_type=action_type,
                action_data=action_data,
                action_result=action_result,
            )
        )

        refusal = credit_limit_frame(
            action_type=action_type,
            action_result=action_result,
            upgrade_deep_link=upgrade_deep_link,
        )
        if refusal:
            outcomes.connection_errors.append(refusal)
            continue

        event = action_event(action_type=action_type, action_result=action_result)
        if event:
            outcomes.events.append(event)

        if action_type == "recommend_resources":
            task = resource_recommendation_task(user_id=user_id, action_data=action_data)
            if task:
                outcomes.background_tasks.append(task)

        component = format_action(
            action_type=action_type,
            action_result=action_result,
            action_data=action_data,
            user_id=user_id,
            db=None,
        )
        if component:
            outcomes.components.append(component)

    return outcomes
