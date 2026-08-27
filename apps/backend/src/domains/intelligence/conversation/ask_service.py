"""One pipeline for an Ask Maigie turn, independent of the transport that carries it.

**Why this module exists** (plan Decision C). Ask Maigie has to be reachable over HTTP and over
WebSocket. If each transport builds its own prompt, assembles its own context, writes its own rows and
does its own accounting, the two drift — and the plan's §5.4 is what that drift looks like after a year:
the HTTP path had quietly stopped persisting anything, stopped routing through the provider layer and
stopped recording cost, and nobody noticed because the surface still answered. So everything above the
transport lives here, once, and streaming is a callback rather than a second code path.

**Why it is being filled in stages rather than written at once.** The pipeline currently lives inside
`register_chat_websocket_routes`, which is one 1,900-line function containing four different flows —
the personal ask turn, space-room chat, the AI greeting, and onboarding — sharing local variables
throughout. Only the first is Ask Maigie (plan §4.2 puts the others out of scope), and the other three
have to come out still working. Moving that in one commit would be unreviewable and unverifiable, so it
moves one seam at a time, with `tests/test_chat_ws_frames.py` holding the observable frame contract
still after each step.

What has moved so far: the decisions that are **pure** — no database, no socket, no model. Those are the
ones that can be tested directly and were previously only reachable by driving a WebSocket, which is why
none of them had a test. What has not moved yet is named in `MOVED_SO_FAR` below, so the boundary is a
fact in the code rather than a claim in a document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: An honest inventory of the extraction, kept here because a half-moved pipeline is easy to
#: misread as a finished one. Update it in the same commit that moves a stage.
MOVED_SO_FAR = (
    "token estimation",
    "retrieval gate",
    "explicit-view gate",
    "skill badges",
    "history formatting",
    "usage reconciliation and pricing",
    "assistant row assembly",
)
STILL_IN_THE_HANDLER = (
    "session resolution",
    "context enrichment",
    "retrieval call",
    "memory context",
    "generation",
    "tool/action loop",
    "the persistence write itself",
    "credit check and consumption",
)


# ===========================================================================
# Types
# ===========================================================================


@dataclass(frozen=True, slots=True)
class AskUsage:
    """What one generation cost, and who produced it.

    Every field is recorded on the `ChatMessage` row and in `LlmCostRecord`. Ask Maigie was the most
    token-hungry surface in the product and was unmetered for its entire life (plan §5.4), so this is
    not telemetry — it is the thing whose absence the plan exists to fix.
    """

    model_name: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    revenue_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class AskContext:
    """Where the learner is asking from.

    `raw` keeps the client's original context blob because enrichment reads keys this dataclass does
    not model yet — `content`, `noteContent`, `topicResources` and others accumulated over time. It is
    a deliberate escape hatch during the extraction, not a permanent part of the contract: each key that
    earns a field comes out of `raw`. Anything still reading `raw` at the end of Phase 2 is unfinished
    work, not a design.
    """

    session_id: str | None = None
    course_id: str | None = None
    topic_id: str | None = None
    exam_prep_id: str | None = None
    note_id: str | None = None
    space_id: str | None = None
    review_item_id: str | None = None
    reply_to_message_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_client(cls, context: dict[str, Any] | None) -> AskContext:
        """Read the context object the clients send alongside a message."""
        context = context or {}
        return cls(
            session_id=context.get("sessionId"),
            course_id=context.get("courseId"),
            topic_id=context.get("topicId"),
            exam_prep_id=context.get("examPrepId"),
            note_id=context.get("noteId"),
            space_id=context.get("spaceId"),
            review_item_id=context.get("reviewItemId"),
            reply_to_message_id=context.get("replyToMessageId"),
            raw=dict(context),
        )

    @property
    def is_review_thread(self) -> bool:
        """Review conversations stay isolated from general chat and from each other."""
        return bool(self.review_item_id)


# ===========================================================================
# Token estimation
# ===========================================================================

#: Characters per token. A rough divisor, and the pipeline's own comment has always said so. It is only
#: used to decide whether to *start* a turn; the real counts come back from the provider and are what
#: gets charged and recorded.
_CHARS_PER_TOKEN = 4

#: Reserved for the answer that has not been generated yet. Reserving nothing would let a learner one
#: token under their cap start a turn that then blows straight through it.
_RESERVED_OUTPUT_TOKENS = 500


def estimate_prompt_tokens(
    *,
    message: str,
    context: Any = None,
    history: Any = None,
) -> int:
    """Approximate the input size of a turn, for the pre-flight credit check.

    Deliberately the same arithmetic the handler used, including its crudeness. Making the estimate
    *better* here would change who gets refused, which is a product decision and not part of moving
    code from one file to another.
    """
    return (
        len(message or "") + len(str(context or "")) + len(str(history or ""))
    ) // _CHARS_PER_TOKEN


def estimate_turn_tokens(*, message: str, context: Any = None, history: Any = None) -> int:
    """Input estimate plus the reservation for the reply. What the credit check is given."""
    return (
        estimate_prompt_tokens(message=message, context=context, history=history)
        + _RESERVED_OUTPUT_TOKENS
    )


# ===========================================================================
# Retrieval gate
# ===========================================================================

#: Messages not worth a retrieval round trip. Retrieval costs a query and adds latency to the reply the
#: learner is waiting on, and "hi" has nothing to retrieve against.
_TRIVIAL_MESSAGES = frozenset(
    {
        "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "bye", "goodbye",
        "help", "?", "cool", "great", "nice", "good", "bad", "sure", "yep", "nope", "what", "why",
        "how", "when", "where", "who", "hm", "hmm", "ah", "oh",
    }
)

_GREETING_PREFIXES = ("hi ", "hello ", "hey ")

#: Below this many characters a message is treated as trivial regardless of content.
_MIN_LENGTH_FOR_RETRIEVAL = 15


def should_retrieve(message: str) -> bool:
    """Whether this message is worth searching the learner's material for.

    Extracted because it is a pure predicate that was unreachable without a live WebSocket, and because
    it is the kind of heuristic that gets quietly edited. It now has tests.
    """
    if not message:
        return False
    normalised = message.lower().strip()
    if len(message) <= _MIN_LENGTH_FOR_RETRIEVAL:
        return False
    if normalised in _TRIVIAL_MESSAGES:
        return False
    return not normalised.startswith(_GREETING_PREFIXES)


#: Retrieved items below this similarity are noise. Passing them to the model invites it to answer
#: about something the learner did not ask for.
RETRIEVAL_SCORE_FLOOR = 0.65


def relevant_retrieved_items(results: list[dict[str, Any]] | None) -> list[str]:
    """Filter retrieval hits by score and render them as prompt lines.

    Reads `similarity` or `score` because the two retrieval paths disagree on the key — recorded rather
    than normalised, since normalising it means changing a caller this extraction has not reached yet.
    """
    lines: list[str] = []
    for item in results or []:
        score = item.get("similarity") or item.get("score") or 0
        if score < RETRIEVAL_SCORE_FLOOR:
            continue
        data = item.get("data") or {}
        object_type = item.get("objectType", "unknown")
        lines.append(f"- {object_type.upper()}: {data.get('title', 'Untitled')} (ID: {item.get('objectId')})")
    return lines


# ===========================================================================
# Explicit-view gate
# ===========================================================================

#: Phrases that mean "show me my data" rather than "use my data to do something".
_EXPLICIT_VIEW_PHRASES = (
    "show my", "list my", "view my", "see my", "what are my", "show me my", "display my", "get my",
    "fetch my", "my courses", "my goals", "my schedule", "my notes", "my resources", "what courses",
    "what goals", "what schedule", "what notes", "show courses", "show goals", "show schedule",
    "show notes", "list courses", "list goals", "list schedule", "list notes",
)


def wants_to_view_data(message: str) -> bool:
    """Whether the learner asked to *see* their data, as opposed to the model looking it up."""
    normalised = (message or "").lower()
    return any(phrase in normalised for phrase in _EXPLICIT_VIEW_PHRASES)


def should_render_query_components(
    *, message: str, executed_actions: list[dict[str, Any]] | None
) -> bool:
    """Whether query results should be rendered as cards.

    Both conditions matter. The model calls `get_user_courses` to *check* something while creating a
    study plan, and rendering course cards then would answer a question the learner did not ask. So
    cards appear only when the learner asked to see the data and the turn did not also create or update
    something — in which case the created thing is the answer.
    """
    mutated = any(
        str(action.get("type", "")).startswith(("create_", "update_"))
        for action in executed_actions or []
    )
    return not mutated and wants_to_view_data(message)


# ===========================================================================
# Skill badges
# ===========================================================================


def build_skill_badges(
    *,
    executed_actions: list[dict[str, Any]] | None,
    query_results: list[dict[str, Any]] | None,
    tool_badge: Any,
    query_badge: Any,
) -> list[dict[str, str]]:
    """The badges shown under an answer, de-duplicated, in the order the work happened.

    `tool_badge` and `query_badge` are passed in rather than imported because the maps still live in
    `websocket_handler`. They move here when the generation stage does; injecting them keeps this
    function testable now without a circular import.
    """
    badges: list[dict[str, str]] = []
    seen: set[str] = set()

    for action in executed_actions or []:
        badge = tool_badge(action.get("type", ""))
        if badge and badge["id"] not in seen:
            badges.append(badge)
            seen.add(badge["id"])

    for result in query_results or []:
        badge = query_badge(result.get("query_type", ""))
        if badge and badge["id"] not in seen:
            badges.append(badge)
            seen.add(badge["id"])

    return badges


# ===========================================================================
# History
# ===========================================================================

#: How many past messages reach the prompt. Enough for "what did you just say" to work, few enough that
#: an old conversation does not dominate the token budget of a new question in it.
HISTORY_LIMIT = 12


def format_history(records: list[Any]) -> list[dict[str, Any]]:
    """Turn `ChatMessage` rows, oldest first, into the provider's history shape.

    Images go in as extra parts on the message that carried them, so a follow-up like "what does the
    third line of that diagram say" still has the diagram. `image_url` is read as a fallback because
    rows written before `image_urls` existed only have the singular column.
    """
    history: list[dict[str, Any]] = []
    for record in records:
        parts: list[Any] = [record.content]
        images = getattr(record, "image_urls", None) or []
        if not images and getattr(record, "image_url", None):
            images = [record.image_url]
        parts.extend(images)
        history.append(
            {
                "role": "user" if record.role == "USER" else "model",
                "parts": parts,
            }
        )
    return history


# ===========================================================================
# Usage reconciliation and cost
# ===========================================================================

#: What produced a turn, written to `ChatMessage.askMode` (migration 049).
#:
#: The column exists so that "Ask Maigie was unmetered for its entire life" (plan §5.4) cannot recur
#: *per surface* without showing up. An aggregate cost figure hides one surface bypassing accounting;
#: a per-row mode does not. Until this, nothing wrote the column.
ASK_MODE_WEBSOCKET = "ws"
ASK_MODE_HTTP = "http"


def resolve_usage(
    *,
    usage_info: dict[str, Any] | None,
    message: str,
    response: str,
    context: Any = None,
    history: Any = None,
    model_name: str,
    user_tier: str,
    cost_calculator: Any,
    revenue_calculator: Any,
) -> AskUsage:
    """Reconcile what the provider reported against what we estimated, and price it.

    **The fallback is the point.** Providers do not always return token counts, and when they do not,
    the turn still has to be charged. Charging it needs a number, and the only available one is the
    estimate — so the estimate has to be *the same* estimate the pre-flight credit check used, computed
    by the same function. It was two copies of the arithmetic before, in two places, which is a
    divergence that shows up as a learner being checked against one number and billed on another.

    Falls back only when **both** counts are zero. A provider reporting input tokens and no output
    tokens is reporting a real zero-output reply, not an absent measurement, and overwriting that with
    an estimate would invent output that did not happen.

    `cost_calculator` and `revenue_calculator` are injected for the same reason `build_skill_badges`
    takes its badge maps: it keeps this testable without importing the billing domain into a module the
    handler imports, and without a live pricing table in the test.
    """
    reported_input = (usage_info or {}).get("input_tokens", 0) or 0
    reported_output = (usage_info or {}).get("output_tokens", 0) or 0

    if reported_input == 0 and reported_output == 0:
        input_tokens = estimate_prompt_tokens(message=message, context=context, history=history)
        output_tokens = estimate_prompt_tokens(message=response)
        logger.debug(
            "Provider reported no token counts; falling back to the pre-flight estimate "
            "(input=%d output=%d)",
            input_tokens,
            output_tokens,
        )
    else:
        input_tokens = reported_input
        output_tokens = reported_output

    return AskUsage(
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_calculator(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_name=model_name,
        ),
        revenue_usd=revenue_calculator(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user_tier=user_tier,
        ),
    )


# ===========================================================================
# Persistence
# ===========================================================================


def build_assistant_row(
    *,
    session_id: str,
    user_id: str,
    content: str,
    usage: AskUsage,
    ask_mode: str,
    review_item_id: str | None = None,
    reply_to_message_id: str | None = None,
    components: list[dict[str, Any]] | None = None,
    suggestion_text: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Build the `ChatMessage` row for an assistant turn, in the repository's wire shape.

    Returned rather than written, so the caller owns the transaction and this stays testable without a
    database. Keys are the camelCase names `intelligence_repo._MESSAGE_MAP` allows; that map raises on
    an unknown key, so a typo here fails loudly instead of silently dropping a column.

    **Optional keys are omitted rather than set to `None`,** and the difference is not cosmetic. The
    repository maps what it is given; a key present with `None` overwrites, a key absent leaves the
    column at its default. `citations` in particular distinguishes three states — absent means grounding
    was not attempted, `[]` means it was attempted and found nothing, and a list means it was cited —
    and passing `None` explicitly would collapse the first two.

    **This function does not decide whether a turn is worth persisting.** A failed generation must not
    reach it at all; the handler's error branches return before this point, and
    `tests/test_chat_ws_frames.py::TestAFailedGenerationIsNotAnAnswer` is what holds that.
    """
    row: dict[str, Any] = {
        "sessionId": session_id,
        "userId": user_id,
        "reviewItemId": review_item_id,
        "role": "ASSISTANT",
        "content": content,
        "tokenCount": usage.total_tokens,
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "modelName": usage.model_name,
        "costUsd": usage.cost_usd,
        "revenueUsd": usage.revenue_usd,
        "askMode": ask_mode,
    }

    if reply_to_message_id:
        row["replyToMessageId"] = reply_to_message_id
    if components:
        row["componentData"] = components
    if suggestion_text:
        row["suggestionText"] = suggestion_text
    if citations is not None:
        row["citations"] = citations
    if truncated:
        row["truncated"] = True

    return row
