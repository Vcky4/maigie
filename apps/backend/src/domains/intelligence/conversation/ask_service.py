"""One pipeline for an Ask Maigie turn, independent of the transport that carries it.

**Why this module exists** (plan Decision C). Ask Maigie has to be reachable over HTTP and over
WebSocket. If each transport builds its own prompt, assembles its own context, writes its own rows and
does its own accounting, the two drift — and the plan's §5.4 is what that drift looks like after a year:
the HTTP path had quietly stopped persisting anything, stopped routing through the provider layer and
stopped recording cost, and nobody noticed because the surface still answered. So everything above the
transport lives here, once, and streaming is a callback rather than a second code path.

**Why it is being filled in stages rather than written at once.** The pipeline lived inside
`register_chat_websocket_routes`, one ~2,100-line function that contained four different flows — the
personal ask turn, space-room chat, the AI greeting, and onboarding — sharing local variables
throughout. Moving that in one commit would be unreviewable, so it moves one seam at a time, with
`tests/test_chat_ws_frames.py` holding the observable frame contract still after each step.

**Three of those four flows have since been deleted rather than moved, and that changes the shape of
what is left.** Space-room chat, the greeting and onboarding could none of them run: every entry point
either returned nothing or raised on a signature the caller did not match, and each failure was caught
and fell through to ordinary chat. So there was no working behaviour to preserve, and `answer()` does
not have to take the union of four flows' parameters — only the personal turn's. The plan's record has
the detail; what matters here is that "still working" no longer applies to anything but one flow.

What has moved so far: the decisions that are **pure** — no database, no socket, no model. Those are the
ones that can be tested directly and were previously only reachable by driving a WebSocket, which is why
none of them had a test. What has not moved yet is named in `MOVED_SO_FAR` below, so the boundary is a
fact in the code rather than a claim in a document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from src.domains.identity.db_models import ModelPreference
from src.shared.database import get_session_factory

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
    "context cache keying",
    "page context instruction blocks",
    "context shaping (records to context keys)",
    "session pinning and authorization",
    "conversation titling",
    "new session row assembly",
    "context enrichment (branches, reads and cache)",
    "history isolation rules",
    "retrieval and memory recall",
    "tool outcomes (logs, events, components, refusals, background work)",
    "credit refusal (which cap, and what the learner is told)",
    "generation, and the order of the turn",
    "the persistence write",
    "the credit check and consumption",
    "the whole turn — answer() (Decision C)",
    # The stages above live in sibling modules rather than here — `context_enrichment` for everything
    # the prompt is built from, `tool_outcomes` for everything a tool call produced. Named in this
    # inventory anyway, because its job is to say what left the handler, not what landed in this file.
    # Each sibling is a coherent unit with one rule running through it: owner scoping for the reads,
    # "return the effect, do not perform it" for the outcomes.
    "owner-scoped context reads",
)
#: What is genuinely the transport's, and stays there. Not a to-do list: `answer()` exists, so this is
#: the boundary rather than the remainder.
STILL_IN_THE_HANDLER = (
    "accepting the socket and authenticating the upgrade",
    "the inbound frame demux (ping, plain text, JSON envelope)",
    "the connection's default session query",
    "saving and acknowledging the learner's own message",
    "the reply context block",
    "rendering every outbound frame",
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
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "yes",
        "no",
        "bye",
        "goodbye",
        "help",
        "?",
        "cool",
        "great",
        "nice",
        "good",
        "bad",
        "sure",
        "yep",
        "nope",
        "what",
        "why",
        "how",
        "when",
        "where",
        "who",
        "hm",
        "hmm",
        "ah",
        "oh",
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
        lines.append(
            f"- {object_type.upper()}: {data.get('title', 'Untitled')} (ID: {item.get('objectId')})"
        )
    return lines


# ===========================================================================
# Explicit-view gate
# ===========================================================================

#: Phrases that mean "show me my data" rather than "use my data to do something".
_EXPLICIT_VIEW_PHRASES = (
    "show my",
    "list my",
    "view my",
    "see my",
    "what are my",
    "show me my",
    "display my",
    "get my",
    "fetch my",
    "my courses",
    "my goals",
    "my schedule",
    "my notes",
    "my resources",
    "what courses",
    "what goals",
    "what schedule",
    "what notes",
    "show courses",
    "show goals",
    "show schedule",
    "show notes",
    "list courses",
    "list goals",
    "list schedule",
    "list notes",
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
# Input validation
# ===========================================================================

#: The longest message Ask Maigie will accept, in characters.
#:
#: Generous on purpose — a learner pasting an essay to ask about is the point of the surface, not abuse
#: of it. What this stops is the case with no legitimate shape: a payload large enough to blow the
#: prompt budget on its own, which then either fails at the provider or silently displaces every piece
#: of context that made the answer personal.
MESSAGE_MAX_LENGTH = 16_000

MESSAGE_REJECTED_EMPTY = "message_empty"
MESSAGE_REJECTED_TOO_LONG = "message_too_long"


@dataclass(frozen=True, slots=True)
class MessageRejection:
    """Why a message was not accepted as a turn at all."""

    code: str
    message: str


def validate_message(message: str | None) -> MessageRejection | None:
    """Check a message is worth starting a turn for, or say why not.

    **This must run before the learner's message row is written**, which is why it is its own function
    and not part of `answer()`: `answer()` receives an already-persisted user message, so a rejection
    inside it would leave the thread holding a row for a turn that never happened. On reload the learner
    would see their question with no reply and no explanation.

    Whitespace-only counts as empty. A message of spaces reaches the model as nothing and produces an
    answer to nothing, which is worse than a refusal.

    Length is measured on the raw message, not the assembled prompt. The prompt also carries history and
    page context, so this is not a token budget — it is the one part of the budget the learner controls
    directly, and the only part it is meaningful to refuse them on.
    """
    if not (message or "").strip():
        return MessageRejection(
            code=MESSAGE_REJECTED_EMPTY,
            message="Ask Maigie something and it will answer.",
        )
    if len(message or "") > MESSAGE_MAX_LENGTH:
        return MessageRejection(
            code=MESSAGE_REJECTED_TOO_LONG,
            message=(
                f"That message is too long — {len(message):,} characters, and the limit is "
                f"{MESSAGE_MAX_LENGTH:,}. Try asking about a shorter section."
            ),
        )
    return None


# ===========================================================================
# Credit refusal
# ===========================================================================


@dataclass(frozen=True, slots=True)
class CreditRefusal:
    """Why a turn was refused before it reached the model, and what to tell the learner.

    A value rather than a frame, so the HTTP path can turn it into a typed `403` body and the socket
    path into a `credit_limit_error` frame without either inventing wording the other does not use.
    """

    message: str
    tier: str
    is_daily_limit: bool


def credit_refusal(
    *, tier: str, estimated_tokens: int, credit_usage: dict[str, Any]
) -> CreditRefusal:
    """Decide which cap the learner hit and compose the refusal.

    **This text is a billing statement shown to a learner about their own account, and it had never
    been tested.** It was built inline between the availability check and the frame that carries it, so
    reaching it needed a live socket and a learner genuinely out of credits.

    **Daily and monthly are different refusals and must not be confused.** A daily cap resets tonight,
    so the message names the reset time and the learner waits. A monthly cap resets at period end, so
    the message names that date and the learner upgrades. Telling someone to wait until midnight for a
    monthly cap is wrong advice about their own money.

    The daily rule has three conditions and each excludes a real case: only the free tier has a daily
    cap at all; a `daily_limit` of zero means no daily cap is configured, not a cap of nothing; and the
    comparison includes this turn's estimate, because the question is whether *this* turn fits rather
    than whether the learner has already exceeded.
    """
    daily_limit = credit_usage.get("daily_limit", 0) or 0
    used_today = credit_usage.get("credits_used_today", 0) or 0
    is_daily = tier == "FREE" and daily_limit > 0 and (used_today + estimated_tokens > daily_limit)

    if is_daily:
        message = (
            f"Daily credit limit exceeded. You've used {used_today:,} "
            f"of {daily_limit:,} daily credits. "
            f"Resets in: {credit_usage.get('next_daily_reset', 'midnight')}. "
            f"Start a free trial for more credits, or refer friends to earn bonus credits!"
        )
    else:
        message = (
            f"Monthly credit limit exceeded. You've used {credit_usage['credits_used']:,} "
            f"of {credit_usage['hard_cap']:,} credits. "
            f"Period resets: {credit_usage['period_end']}. "
            f"Start a free trial for unlimited usage, or refer friends to earn bonus credits!"
        )

    return CreditRefusal(message=message, tier=tier, is_daily_limit=is_daily)


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


# ===========================================================================
# Context cache keying
# ===========================================================================

#: Context keys that must never be written to the cache.
#:
#: Two different reasons, and both matter:
#:
#: - **Derived per turn.** `pageContext` is a generated instruction block that depends on what the
#:   enrichment found — review mode reads differently from topic mode. `retrieved_items` is the result
#:   of a retrieval over *this* question. Caching either would replay one turn's reasoning into the next.
#: - **Supplied per turn by the client.** `content` and `noteContent` are pasted in with the message, so
#:   they are not a property of the ids at all. `topicResources` and `topicUploadedResources` are
#:   attached separately and can change without any id changing.
#:
#: The cache is keyed on ids only (see `context_cache_key_parts`), so anything here that got cached
#: would be served for a turn whose value differs — for up to the 300-second TTL. Adding a derived key
#: to enrichment without adding it here is the way that regresses, which is why this is a named
#: constant with a test rather than a set literal inline.
VOLATILE_CONTEXT_KEYS = frozenset(
    {
        "pageContext",
        "content",
        "noteContent",
        "retrieved_items",
        "topicResources",
        "topicUploadedResources",
    }
)

#: The identifiers whose values determine what enrichment fetches. Order is fixed because it is part of
#: the key.
_CONTEXT_CACHE_IDS = ("noteId", "topicId", "courseId", "reviewItemId")


def context_cache_key_parts(*, user_id: str, context: dict[str, Any] | None) -> list[str] | None:
    """The cache key parts for an enriched context, or `None` when there is nothing worth caching.

    Returns parts rather than a formatted key so this stays pure and the caller keeps ownership of the
    namespacing. The decision here is *what identifies a context*, which is the part that can be wrong.

    **Every id that changes what enrichment fetches has to be in here.** Two contexts that agree on
    these parts will share one cache entry for the TTL, so an id that affects the result but not the key
    serves one learner's topic as another's. `user_id` is a part for the same reason: these rows are
    per-learner and a key without it would cross accounts.

    Audited 2026-08-27 against the enrichment block: the only client-supplied ids it reads are these
    four. `examPrepId` and `spaceId` are carried on the context but not read by enrichment, so they are
    correctly absent — add them here in the same change that starts reading them.

    Returns `None` when no id is present, which is the case where enrichment has nothing to look up.
    A key built from four dashes would be a single shared entry for every context-free turn.
    """
    context = context or {}
    values = [context.get(name) for name in _CONTEXT_CACHE_IDS]
    if not any(values):
        return None
    return ["chat", "context", user_id, *[value or "-" for value in values]]


def cacheable_context(enriched: dict[str, Any]) -> dict[str, Any]:
    """Strip the per-turn values out of an enriched context before it is cached.

    See `VOLATILE_CONTEXT_KEYS` for why each one is excluded.
    """
    return {key: value for key, value in enriched.items() if key not in VOLATILE_CONTEXT_KEYS}


def merge_cached_context(context: dict[str, Any], cached: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay a cached enrichment onto the context the client just sent.

    **The client's context wins on conflict, and the order is deliberate.** The cached half holds
    fetched facts about ids — a topic's title, a course's description — while the incoming half holds
    what the learner is doing right now. If a key appears in both, the live one is the current truth and
    the cached one is up to 300 seconds stale.

    Returns a new dict; neither argument is mutated. A `None` cached value yields a copy of the context,
    so callers do not need to branch.
    """
    if not cached:
        return dict(context)
    return {**context, **cached}


# ===========================================================================
# Page context — the per-mode instruction blocks
# ===========================================================================
#
# `pageContext` tells the model what kind of turn this is. It is prompt text, which makes it product
# copy that changes behaviour: the review block below encodes the entire spaced-repetition protocol,
# including the 0–5 quality scale that `complete_review` writes to the scheduler. Getting a number in
# that scale wrong changes when a learner sees a topic again.
#
# It lived inline, roughly 970 lines into a 2,000-line function, which is the worst place for text that
# product needs to iterate on. Named here so it can be read, diffed and tested without reading the
# handler — and so a change to the quality scale is a change to one visible constant.
#
# These are deliberately *not* templates. Each is one mode's instructions, and a shared template with
# mode flags would make every mode's copy a function of every other mode's.


#: Instructions for a spaced-repetition review turn.
#:
#: **The quality scale is a contract with the scheduler, not advice.** `complete_review` passes the
#: number through to the SM-2-style interval calculation, so the percentages here are what map a
#: learner's performance onto their next review date. Changing a boundary changes review schedules for
#: everyone.
#:
#: The one-at-a-time instruction is load-bearing for the same reason it is repeated and capitalised: a
#: model given "ask 3–5 questions" will list all of them in one message, which turns a review into a
#: worksheet and makes per-answer feedback impossible.
REVIEW_MODE_PAGE_CONTEXT = (
    "Review mode (spaced repetition): You are conducting a review for the topic below. "
    "1) Start with a brief, engaging summary of what the topic is about (2–3 sentences). "
    "2) Then ask 3–5 short quiz questions ONE AT A TIME. Do not list all questions at once. "
    "3) After each answer, give a brief explanation or feedback before asking the next question. "
    "4) Internally keep track of how many questions the user gets right vs wrong and their confidence level. "
    "5) When the user has answered all questions and you have given your final explanation, "
    "call the complete_review tool with a quality rating (0-5) based on their performance: "
    "0 = total blackout (0% correct), 1 = mostly wrong but recognised answers (≤20%), "
    "2 = mostly wrong but answers seemed easy once shown (≤40%), "
    "3 = correct but with serious difficulty (≈60%), "
    "4 = correct with minor hesitation (≈80%), 5 = perfect instant recall (100%). "
    "Also provide a brief score_summary like '4/5 correct, struggled with X'. "
    "After calling complete_review, tell the user their score and briefly explain what the "
    "quality rating means for their next review schedule (e.g. 'Next review in X days'). "
    "Do not ask the user to click any button; completion is automatic when you call complete_review."
)

# The space-room instruction block lived here. It went with space-room chat, which could not run:
# `_get_circle_group_for_session` returned `None` unconditionally, so no turn was ever classified as a
# room turn and this text never reached a model. Its one interesting line — "not the user's private
# study history" — was a privacy boundary rather than a style note, and if room chat is ever built the
# boundary has to be re-established on both sides: in the instructions *and* structurally, by keeping
# personal retrieval and long-term memory out of a shared room (see `context_enrichment.attach_recall`,
# which enforced exactly that and no longer needs to).


# ===========================================================================
# Context shaping — records to context keys
# ===========================================================================
#
# Enrichment is two jobs: fetch rows for the ids the client sent, then decide which of their fields
# become prompt context. The fetching stays in the handler for now; the deciding is here, because it is
# the half that is pure and the half that gets edited.
#
# Each returns the keys to merge rather than mutating a context in place. The handler had four blocks
# writing directly into one shared dict across 300 lines, so which branch produced which key was only
# discoverable by reading all of them. A returned dict is also what makes these testable with plain
# namespaces instead of ORM instances.
#
# **These deliberately do not share a single implementation.** The four branches disagree in small ways
# that look like bugs and may be, but they are load-bearing until something proves otherwise — see
# `review_context_updates` for the one worth knowing about. Unifying them would be a behaviour change
# dressed as a refactor, and none of it can be verified without a live database.


def _topic_chain_updates(
    *,
    topic: Any,
    module: Any = None,
    course: Any = None,
    include_topic_id: bool = True,
) -> dict[str, Any]:
    """The topic → module → course chain, shared by the three branches that agree on it.

    `include_topic_id` is `False` when the caller already has the id from the client's context and is
    only filling in the titles: writing it back would be a no-op at best, and at worst would overwrite
    a client value with a fetched one that came from following the id in the first place.

    Course fields require *both* a module and a course, because a course is reached through a module
    here. `moduleTitle` is set as soon as there is a module, whether or not the course resolved.
    """
    updates: dict[str, Any] = {}
    if include_topic_id:
        updates["topicId"] = topic.id
    updates["topicTitle"] = topic.title
    updates["topicContent"] = topic.content or ""

    if module:
        updates["moduleTitle"] = module.title
        if course:
            updates["courseId"] = course.id
            updates["courseTitle"] = course.title
            updates["courseDescription"] = course.description or ""

    return updates


def review_context_updates(
    *, review: Any, topic: Any, module: Any = None, course: Any = None
) -> dict[str, Any]:
    """Context for a spaced-repetition review turn.

    **This branch gates `moduleTitle` differently from every other one, and the difference is real.**
    Here the whole module-and-course block is behind `if module and course`, so a topic whose module
    resolved but whose course did not contributes *no* `moduleTitle` — whereas `_topic_chain_updates`
    would set it. Preserved rather than unified: it is only reachable when a module exists without its
    course, which suggests broken catalogue data, and nothing here can tell whether some downstream
    prompt depends on the difference. Worth resolving deliberately, with a live database, rather than
    silently while moving code.

    `nextReviewAt` is stringified defensively because the column is a `DateTime` but the value has
    arrived as a string before; `hasattr(..., "isoformat")` is the original check and is kept.
    """
    updates: dict[str, Any] = {
        "pageContext": REVIEW_MODE_PAGE_CONTEXT,
        "topicId": review.topic_id,
        "topicTitle": topic.title,
        "topicContent": topic.content or "",
        "reviewItemId": review.id,
        "nextReviewAt": (
            review.next_review_at.isoformat()
            if hasattr(review.next_review_at, "isoformat")
            else str(review.next_review_at)
        ),
    }

    if module and course:
        updates["courseId"] = course.id
        updates["courseTitle"] = course.title
        updates["courseDescription"] = course.description or ""
        updates["moduleTitle"] = module.title

    return updates


def note_context_updates(
    *,
    note: Any,
    topic: Any = None,
    module: Any = None,
    course: Any = None,
    direct_course: Any = None,
) -> dict[str, Any]:
    """Context for a note turn.

    **Every caller must have fetched `note` as its owner** — the note's title, summary and full body go
    into the prompt, so an unowned note here is a disclosure. That is enforced at the read, through
    `personal_learning_repo.find_note(note_id, user_id)`, and guarded by
    `tests/test_chat_context_authorization.py`. This function cannot check it and does not try; it is
    noted so the requirement travels with the code.

    A note reaches a course two ways and they are mutually exclusive: through its topic's module, or
    directly via `note.course_id` when it has no topic. `direct_course` is the second, and it is only
    consulted when there is no topic.
    """
    updates: dict[str, Any] = {
        "noteTitle": note.title,
        "noteContent": note.content or "",
        "noteSummary": note.summary or "",
    }

    if topic:
        updates.update(_topic_chain_updates(topic=topic, module=module, course=course))
    elif direct_course:
        updates["courseId"] = direct_course.id
        updates["courseTitle"] = direct_course.title
        updates["courseDescription"] = direct_course.description or ""

    return updates


def topic_context_updates(
    *,
    topic: Any,
    module: Any = None,
    course: Any = None,
    include_topic_id: bool = True,
) -> dict[str, Any]:
    """Context for a topic turn, and for the fallback where a `noteId` turns out to be a topic id.

    That fallback exists because clients have sent topic ids in `noteId`. It is a real path, not a
    defensive one, which is why it resolves the topic and then looks for the learner's latest note on it.
    """
    return _topic_chain_updates(
        topic=topic, module=module, course=course, include_topic_id=include_topic_id
    )


def course_context_updates(*, course: Any) -> dict[str, Any]:
    """Context for a course turn.

    No `courseId`: the caller already has it from the client's context, which is how the course was
    found.
    """
    return {
        "courseTitle": course.title,
        "courseDescription": course.description or "",
    }


def format_topic_user_notes(notes: list[Any]) -> str:
    """Render a learner's notes on a topic as one markdown block for the prompt.

    Each note becomes an `## <title>` heading with its body, separated by horizontal rules so the model
    can tell one note from the next — without a separator, two notes read as one document and a
    contradiction between them looks like a single confused note.

    An untitled note gets "Note" rather than an empty heading, and a note with no body is kept as a
    bare heading: a title alone still tells the model what the learner thought worth recording. Entries
    that are blank after stripping are dropped, so an empty note does not contribute a stray rule.

    **Every note passed here must belong to the asking learner.** The caller's query filters on
    `user_id`; see `note_context_updates` and `tests/test_chat_context_authorization.py`.

    Returns `""` for no notes, which the caller treats as "nothing to add" — an empty string is not
    written to the context.
    """
    blocks: list[str] = []
    for note in notes or []:
        head = (getattr(note, "title", None) or "Note").strip()
        body = (getattr(note, "content", None) or "").strip()
        blocks.append(f"## {head}\n{body}" if body else f"## {head}")

    return "\n\n---\n\n".join(block for block in blocks if block.strip())


# ===========================================================================
# Session resolution
# ===========================================================================
#
# Which conversation a turn belongs to, and whether the learner may write to it. First of the impure
# stages, and it moves in two halves for a reason: the *decision* is what can be wrong, and the
# *fetches* are what need a database. The decision is here, driven by injected readers, so a
# non-member being let into a room is now a failing test rather than a code review.
#
# The connection's default-session query stays in the handler for now — it is a SQLAlchemy statement
# over `ChatSession` with no branching in it, so moving it buys nothing until `answer()` needs it.
# `STILL_IN_THE_HANDLER` says so rather than claiming session resolution is finished.


#: The title a conversation is created with, before its first message names it.
#:
#: Not a placeholder that nothing replaces — `should_retitle_session` and `derive_session_title` below
#: are the replacement, and they are the reason this is a named constant: the retitle gate has to
#: recognise the untouched default, so the two would silently disagree if the literal appeared twice.
NEW_CONVERSATION_TITLE = "New Chat"

#: Session kinds. `general` is Ask Maigie's personal conversation, and now the only kind this surface
#: creates. Space-room sessions are still created by the `learning_spaces` domain and still carry
#: `is_space_room`, which is why the connection's default-session query keeps filtering on it — without
#: that filter a learner's personal conversation could resolve to a room's session.
SESSION_TYPE_GENERAL = "general"


def new_session_row(user_id: str) -> dict[str, Any]:
    """The row for a learner's personal Ask Maigie conversation, in the repository's wire shape.

    Returned rather than written, like `build_assistant_row`, so the caller owns the transaction. Keys
    are the camelCase names `intelligence_repo._map_chat_session` allows.

    `isSpaceRoom` is explicit rather than left to a column default because the connection's
    default-session query filters on it — a session created without it would be invisible to the query
    that is supposed to find it again, and the learner would get a new conversation on every connect.
    """
    return {
        "userId": user_id,
        "title": NEW_CONVERSATION_TITLE,
        "isSpaceRoom": False,
        "sessionType": SESSION_TYPE_GENERAL,
    }


#: Why a learner was refused a session. Codes rather than strings, so the reason can be tested and the
#: wording can change without a test changing with it.
SESSION_DENIED_PINNED_OWNER = "pinned_session_forbidden"
SESSION_DENIED_LOOKUP_FAILED = "session_lookup_failed"

#: The messages the clients render, kept verbatim from the handler.
#:
#: The two differ in kind, not just in wording. One is about permission, and repeating the turn will
#: refuse again. The other is not a refusal at all — nothing was denied, the conversation could not be
#: reached — and its message says so, because retrying is exactly what the learner should do.
#:
#: Two room-membership codes lived here until space-room chat was removed. They are gone with it.
SESSION_DENIAL_MESSAGES: dict[str, str] = {
    SESSION_DENIED_PINNED_OWNER: "You are not allowed to access this chat session.",
    SESSION_DENIED_LOOKUP_FAILED: (
        "That conversation could not be opened just now. Please try sending your message again."
    ),
}

#: Denials the learner can do something about by retrying. Only the lookup failure is transient; a
#: permission refusal will refuse again for as long as the permission stands.
RETRYABLE_SESSION_DENIALS = frozenset({SESSION_DENIED_LOOKUP_FAILED})


@dataclass(frozen=True, slots=True)
class SessionResolution:
    """Which session a turn writes to, or why it may not be written at all.

    `denial` and `session` are mutually exclusive in practice: a refused turn has nowhere to go, and the
    caller must return before it saves anything. Holding both on one object rather than raising keeps
    the refusal a value the HTTP path can turn into a `403` and the socket path into an `error` frame,
    without either transport catching an exception the other one throws.
    """

    session: Any = None
    denial: str | None = None

    @property
    def allowed(self) -> bool:
        return self.denial is None

    @property
    def retryable(self) -> bool:
        """Whether sending the same message again might work.

        The clients already render `error` frames with a `retryable` flag from the failed-generation
        path, so a refused turn can say which kind it is without any new frame vocabulary.
        """
        return self.denial in RETRYABLE_SESSION_DENIALS


async def resolve_session_for_turn(
    *,
    requested_session_id: str | None,
    current_session: Any,
    user_id: str,
    find_session: Any,
) -> SessionResolution:
    """Resolve the session a turn belongs to and authorise the learner for it.

    `find_session(session_id)` is injected so the authorisation rule can be tested without a database or
    a socket.

    **The ownership check is the whole point of this function.** A session id arrives from the client on
    every message and is used to switch conversations mid-connection, so an unchecked id is a read and a
    write into someone else's thread. Ask Maigie is the personal, one-to-one surface, so there is one
    rule: the learner must own the conversation.

    **There used to be a second rule, for space-room chat, and it is gone with the flow it served.**
    A room was authorised by group membership and was checked *first*, because a room's
    `ChatSession.user_id` is whoever created it. Both halves rested on `_get_circle_group_for_session`,
    which returned `None` unconditionally, so no session was ever a room and the room rule never ran.
    Removed rather than left in place: an authorisation branch that has never executed is not a
    safeguard, it is an untested path that reads like one.

    **A lookup failure refuses the turn. It used to fall back silently, and that was a defect**
    (plan §5.5.12). The handler wrapped the lookup in `except Exception: pass` and carried on with
    whatever session the connection was already on — so on a transient database error the learner's
    question and Maigie's answer were persisted, metered and charged **in a conversation they did not
    pin**, with nothing said. The pinned thread showed a gap, so the learner would ask again, and the
    duplicate charge landed somewhere nobody would look for it.

    Refusing needs no new frame vocabulary: both clients already render `error` with a `retryable` flag,
    which the failed-generation path put there. `SESSION_DENIED_LOOKUP_FAILED` is marked retryable
    because it is transient, which is what makes refusing better than falling back rather than merely
    louder — the learner is told to send it again, which is exactly what they should do.

    **A session id that resolves to nothing is not this case** and still falls back to the current
    session. Nothing failed there; the id is simply stale, and refusing every stale id would break a
    client holding a reference to a deleted conversation.
    """
    session = current_session

    if requested_session_id:
        try:
            pinned = await find_session(requested_session_id)
            if pinned:
                if pinned.user_id == user_id:
                    session = pinned
                else:
                    return SessionResolution(denial=SESSION_DENIED_PINNED_OWNER)
        except Exception as error:  # noqa: BLE001 — any read failure means the same thing here
            logger.warning(
                "Pinned session %s could not be resolved for user %s; refusing the turn rather "
                "than writing it to session %s: %s",
                requested_session_id,
                user_id,
                getattr(current_session, "id", None),
                error,
            )
            return SessionResolution(denial=SESSION_DENIED_LOOKUP_FAILED)

    return SessionResolution(session=session)


# ---------------------------------------------------------------------------
# Naming a conversation
# ---------------------------------------------------------------------------

#: How much of the first message becomes the title. Long enough to tell two questions about the same
#: topic apart, short enough for a history row.
TITLE_MAX_LENGTH = 50


def derive_session_title(message: str) -> str:
    """Name a conversation after the message that started it.

    Whitespace is collapsed before truncating, which is load-bearing rather than tidy: a pasted question
    arrives with newlines and runs of spaces, and truncating that at 50 characters can spend the whole
    title on blank space. Collapsing first means the 50 characters are 50 characters of words.

    Truncation is marked with an ellipsis so a clipped title is visibly clipped — a title ending
    mid-word with no marker reads as a learner who mistyped.
    """
    cleaned = " ".join((message or "").strip().split())
    if len(cleaned) > TITLE_MAX_LENGTH:
        return cleaned[:TITLE_MAX_LENGTH] + "..."
    return cleaned


def session_needs_a_title(
    *,
    current_title: str | None,
    message: str,
    is_review_thread: bool,
) -> bool:
    """Whether this conversation is a candidate for naming, judged without a query.

    Split from `should_retitle_session` so the caller can skip the `count(*)` when the answer is already
    no. That is not a micro-optimisation: the count runs per turn on a conversation that already has a
    name, which is every turn after the first, forever.

    Three conditions, each excluding a specific way of getting this wrong:

    - **Only an untouched title.** A conversation the learner renamed, or one already named by its first
      message, keeps its name. Without this the title would follow the most recent question rather than
      identify the thread, so the history panel would rename rows under the learner as they typed.
    - **Never a review thread.** Review conversations are addressed by their review item and are not
      listed as conversations at all, so a title on one is invisible work.
    - **Never a blank message.** An empty title is worse than the default: the default at least says
      "new".
    """
    if is_review_thread:
        return False
    if current_title not in (None, "", NEW_CONVERSATION_TITLE):
        return False
    return bool((message or "").strip())


def should_retitle_session(
    *,
    current_title: str | None,
    user_message_count: int,
    message: str,
    is_review_thread: bool,
) -> bool:
    """Whether this turn is the one that names the conversation.

    `session_needs_a_title`, plus the condition that needs a query: **only the first user message**,
    counted after the message is saved, so `1` means this one. The count is over `USER` rows in the
    thread rather than messages seen on this connection, because that is the only figure that survives a
    reconnect mid-conversation — count in memory and a learner who reconnects gets their fourth question
    as the thread's name.

    `user_message_count` is passed in rather than queried because the query belongs to the caller's
    transaction. The judgement is here; the counting is not.
    """
    if not session_needs_a_title(
        current_title=current_title, message=message, is_review_thread=is_review_thread
    ):
        return False
    return user_message_count == 1


# ===========================================================================
# Skill badges — the maps
# ===========================================================================
#
# Lifted out of the handler when generation moved. They were injected into
# `build_skill_badges` so that this module stayed importable without a cycle while the maps still
# lived beside `route_request`; now that the whole turn is here, the injection has nothing left to
# decouple. `build_skill_badges` still takes them as arguments, because that keeps it pure and its
# tests independent of what the product currently calls a skill.

TOOL_SKILL_MAP: dict[str, dict[str, str]] = {
    # Course Management
    "get_user_courses": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "create_course": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "update_course_outline": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "delete_course": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    # Note Taking
    "get_user_notes": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "create_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "retake_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "add_summary_to_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "add_tags_to_note": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    # Goal Management
    "get_user_goals": {"id": "goals", "name": "Goal Management", "icon": "target"},
    "create_goal": {"id": "goals", "name": "Goal Management", "icon": "target"},
    # Scheduling
    "get_user_schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "check_schedule_conflicts": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "create_schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    # Resources
    "get_user_resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
    "recommend_resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
    # Memory & Profile
    "get_my_profile": {"id": "memory", "name": "Memory", "icon": "user"},
    "save_user_fact": {"id": "memory", "name": "Memory", "icon": "user"},
    "complete_review": {"id": "memory", "name": "Spaced Repetition", "icon": "refresh-cw"},
    "email_user": {"id": "email", "name": "Email", "icon": "mail"},
    # Planning
    "create_study_plan": {"id": "planning", "name": "Study Planning", "icon": "map"},
    "get_learning_insights": {"id": "planning", "name": "Learning Insights", "icon": "bar-chart"},
    "get_pending_nudges": {"id": "planning", "name": "Smart Nudges", "icon": "bell"},
    # Document Generation
    "generate_document": {
        "id": "documents",
        "name": "Document Generation",
        "icon": "file-arrow-down",
    },
}

QUERY_TYPE_SKILL_MAP: dict[str, dict[str, str]] = {
    "courses": {"id": "courses", "name": "Course Management", "icon": "book-open"},
    "goals": {"id": "goals", "name": "Goal Management", "icon": "target"},
    "schedule": {"id": "scheduling", "name": "Scheduling", "icon": "calendar"},
    "notes": {"id": "notes", "name": "Note Taking", "icon": "file-text"},
    "resources": {"id": "resources", "name": "Resource Finder", "icon": "search"},
}


def tool_skill_badge(tool_name: str) -> dict[str, str] | None:
    """Map a tool/action name to a skill badge for the frontend."""
    return TOOL_SKILL_MAP.get(tool_name)


def query_type_skill_badge(query_type: str) -> dict[str, str] | None:
    """Map a query result type to a skill badge."""
    return QUERY_TYPE_SKILL_MAP.get(query_type)


# ===========================================================================
# Model preference
# ===========================================================================


async def read_model_preference(user_id: str, capability: str = "chat") -> tuple[str, str] | None:
    """Fetch the user's model preference for a given capability from the DB.

    Returns a (provider, model_id) tuple if a preference is set, else None.
    """
    try:
        factory = get_session_factory()
        async with factory() as session:
            stmt = select(ModelPreference).where(
                ModelPreference.user_id == user_id,
                ModelPreference.capability == capability,
            )
            result = await session.execute(stmt)
            pref = result.scalar_one_or_none()
        if pref and pref.provider and pref.model_id:
            return (pref.provider, pref.model_id)
    except Exception as e:
        logger.debug("Failed to fetch model preference for user %s: %s", user_id, e)
    return None


# ===========================================================================
# One turn, end to end
# ===========================================================================
#
# Decision C, finally. Everything above the transport is one function, and streaming is a callback
# rather than a second code path — because the alternative is an HTTP path that quietly drifts from the
# streaming one, which is what §5.4 documents after a year of it.


class TurnRefused(Exception):  # noqa: N818 — named for what happened, not for being an error
    """The turn was refused before the model ran.

    Nothing was generated, nothing is charged, and **no assistant row is written**. Carries the refusal
    so the socket can render a `credit_limit_error` frame and Phase 3's `/ask` a typed `403` from the
    same words.
    """

    def __init__(self, refusal: CreditRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


class TurnFailed(Exception):  # noqa: N818 — same reason
    """Generation failed, so there is no answer.

    **This exists so that a failure cannot be mistaken for an answer** (plan §1). Raising rather than
    returning is the point: there is no path from here to the persistence write, so a provider outage
    cannot end up in the learner's history as something Maigie said. No credits are consumed either,
    because consumption happens after the write this skips.
    """

    def __init__(self, *, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AskEffects:
    """Everything a turn does to the world, injected.

    Sixteen of them, which is not a design smell but a measurement: this is what one Ask Maigie turn
    touches. Bundled for the same reason as `ContextReaders` — the alternative is a call site that is
    mostly plumbing and a test that must name every effect to override one.

    **Every entry is something that writes, charges, queues or calls a provider.** The decisions are all
    above, in this module and its two siblings, which is what makes `answer()` testable without a
    database, a socket or a model.
    """

    create_message: Any
    """(data) -> row. Writes a `ChatMessage`."""

    create_action_log: Any
    """(data) -> None. Writes an `AIActionLog`."""

    generate: Any
    """(**kwargs) -> (text, usage, actions, query_rows). The model call."""

    resolve_tier: Any
    """(user_id, personal_tier) -> str. Effective tier for the request."""

    model_preference: Any
    """(user_id, capability) -> (provider, model) | None."""

    fallback_model_name: Any
    """() -> str. The model name to record when the provider does not report one."""

    check_credits: Any
    """(user_obj, tokens) -> (available, warning). Raises `SubscriptionLimitError` on a hard cap."""

    credit_usage: Any
    """(user_obj) -> dict. Read only on the refusal path, to compose the message."""

    consume_credits: Any
    """(user_obj, tokens, operation) -> result. After generation, on real counts."""

    cost_calculator: Any
    revenue_calculator: Any

    queue_task: Any
    """(name, kwargs) -> None. Background work a tool asked for."""

    format_list: Any
    format_action: Any
    tool_badge: Any
    query_badge: Any

    extract_suggestion: Any
    """(text) -> (content, suggestion | None). Splits a trailing suggestion off an answer."""

    purchase_deep_link: str
    """Where a refused learner is sent to buy credits. A value, not a callable."""


@dataclass(frozen=True, slots=True)
class AskTurn:
    """What one completed turn produced. Only built when there is an answer."""

    assistant_message: Any
    content: str
    usage: AskUsage
    outcomes: Any
    suggestion_text: str | None = None
    skills_used: list[dict[str, str]] = field(default_factory=list)
    credit_result: Any = None
    enriched_context: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


async def answer(
    *,
    message: str,
    user: Any,
    user_obj: Any,
    session: Any,
    user_message: Any,
    context: dict[str, Any] | None,
    ask_mode: str,
    readers: Any,
    effects: AskEffects,
    cache: Any = None,
    image_url: str | None = None,
    on_chunk: Any = None,
    on_progress: Any = None,
) -> AskTurn:
    """Answer one turn, over any transport.

    `on_chunk(chunk, is_final)` is how a transport streams. **Passing `None` must produce the same
    answer as passing a callback** — that is the whole of Decision C, and it is why the callback is an
    argument rather than a branch. HTTP passes `None`; the socket passes a function that writes `stream`
    frames.

    The order is not arbitrary and two parts of it are load-bearing:

    - **Credits are checked before generation and consumed after.** Before, because the alternative
      charged a live model call to a learner over their cap, streamed them the whole answer, and only
      then refused — money spent, nothing recorded. After, because only then are the real token counts
      known.
    - **The assistant row is written after the tool outcomes are collected but before anything is sent.**
      The row is what the learner sees on reload, so it has to exist before the frames that claim it
      does.

    `user_message` is saved by the caller, not here, and that is deliberate: the socket acknowledges the
    learner's message with `message_saved` for id correlation *before* enrichment runs, so the bubble
    stops being optimistic as early as possible. `answer()` needs its id for the action log and the
    reply preview.

    Raises `TurnRefused` when the learner has no credits and `TurnFailed` when generation fails. Both
    leave no assistant row and consume nothing.
    """
    from . import context_enrichment, tool_outcomes

    review_item_id = (context or {}).get("reviewItemId")

    history = await context_enrichment.build_history(
        session_id=session.id,
        user_id=user.id,
        review_item_id=review_item_id,
        readers=readers,
    )
    enriched = await context_enrichment.enrich_context(
        context=context, user_id=user.id, readers=readers, cache=cache
    )
    enriched = await context_enrichment.attach_recall(
        context=enriched, message=message, user_id=user.id, readers=readers
    )

    # --- credits, before the model runs -----------------------------------
    estimated = estimate_turn_tokens(message=message, context=enriched, history=history)
    available, _warning = await effects.check_credits(user_obj, estimated)
    if not available:
        raise TurnRefused(
            credit_refusal(
                tier=str(user_obj.tier) if user_obj.tier else "FREE",
                estimated_tokens=estimated,
                credit_usage=await effects.credit_usage(user_obj),
            )
        )

    # --- generation --------------------------------------------------------
    tier = await effects.resolve_tier(
        user.id, str(user.tier) if getattr(user, "tier", None) else None
    )
    preference = await effects.model_preference(user.id, "chat")

    response_text, usage_info, executed_actions, query_results = await effects.generate(
        user_id=user.id,
        user_tier=tier,
        model_preference=preference,
        history=history,
        user_message=message,
        context=enriched,
        user_name=getattr(user, "name", None),
        image_url=image_url,
        progress_callback=on_progress,
        stream_callback=on_chunk,
    )

    # --- what the tools produced ------------------------------------------
    outcomes = tool_outcomes.collect_tool_outcomes(
        message=message,
        user_id=user.id,
        user_message_id=user_message.id,
        executed_actions=executed_actions,
        query_results=query_results,
        format_list=effects.format_list,
        format_action=effects.format_action,
        purchase_deep_link=effects.purchase_deep_link,
    )
    for row in outcomes.action_logs:
        await effects.create_action_log(data=row)
    for task_name, task_kwargs in outcomes.background_tasks:
        effects.queue_task(task_name, task_kwargs)

    # --- price it, charge it ----------------------------------------------
    clean = (response_text or "").strip()
    usage = resolve_usage(
        usage_info=usage_info,
        message=message,
        response=clean,
        context=enriched,
        history=history,
        model_name=(usage_info or {}).get("model_name") or effects.fallback_model_name(),
        user_tier=str(user_obj.tier) if user_obj.tier else "FREE",
        cost_calculator=effects.cost_calculator,
        revenue_calculator=effects.revenue_calculator,
    )

    credit_result = None
    try:
        credit_result = await effects.consume_credits(user_obj, usage.total_tokens, "chat_message")
    except Exception as error:  # noqa: BLE001 — the turn succeeded; the charge is not the answer
        # Deliberately not fatal. The learner has their answer and the row is about to be written; a
        # failure to *record* the charge must not retract it, and it is already logged as a real
        # accounting gap rather than swallowed.
        logger.warning("Credit consumption failed after a completed turn: %s", error)

    # --- persist -----------------------------------------------------------
    main_content, suggestion_text = clean, None
    if outcomes.components and clean:
        # Only split when there are components to display the suggestion *after*. With no components
        # the suggestion is just the answer's last sentence and splitting it would reorder prose.
        main_content, suggestion_text = effects.extract_suggestion(clean)

    skills_used = build_skill_badges(
        executed_actions=executed_actions,
        query_results=query_results,
        tool_badge=effects.tool_badge,
        query_badge=effects.query_badge,
    )

    assistant_message = await effects.create_message(
        data=build_assistant_row(
            session_id=session.id,
            user_id=user.id,
            content=main_content,
            usage=usage,
            ask_mode=ask_mode,
            review_item_id=(enriched or {}).get("reviewItemId") or review_item_id,
            reply_to_message_id=user_message.id,
            components=outcomes.components,
            suggestion_text=suggestion_text,
        )
    )

    return AskTurn(
        assistant_message=assistant_message,
        content=main_content,
        usage=usage,
        outcomes=outcomes,
        suggestion_text=suggestion_text,
        skills_used=skills_used,
        credit_result=credit_result,
        enriched_context=enriched,
        history=history,
    )


def production_effects() -> AskEffects:
    """The real effects. Imported lazily because they cross domains and this module is imported early.

    Not memoized, unlike `context_enrichment.production_readers`: `generate` closes over the router
    instance, and `get_llm_router()` is the thing whose availability changes when the LLM layer is
    reconfigured. Building the bundle per connection keeps that resolution honest.
    """
    from src.domains.billing.services.cost_calculator import calculate_ai_cost, calculate_revenue
    from src.domains.billing.services.credit_consumption_service import (
        PURCHASE_DEEP_LINK,
        check_credit_availability,
        consume_credits,
        get_credit_usage,
    )
    from src.domains.intelligence.reasoning.llm.adapter_registry import (
        get_feature_flag_service,
        get_llm_router,
    )
    from src.domains.intelligence.reasoning.llm.registry import LlmTask, default_model_for
    from src.domains.intelligence.repository import intelligence_repo

    from .chat_helpers import _extract_suggestion
    from .component_response import (
        format_action_component_response,
        format_list_component_response,
    )

    async def generate(**kwargs: Any):
        # `usage_scope` and `space_id` are pinned to personal here rather than threaded through
        # `answer()`, because space-room chat was removed and personal is the only scope this surface
        # has. A future shared scope adds a parameter; it does not resurrect a branch.
        from src.domains.intelligence.reasoning.llm.feature_flags import PERSONAL_SCOPE

        return await get_llm_router().route_request(
            task=LlmTask.CHAT_TOOLS_SESSION,
            usage_scope=PERSONAL_SCOPE,
            space_id=None,
            **kwargs,
        )

    async def resolve_tier(user_id: str, personal_tier: str | None) -> str:
        from src.domains.intelligence.reasoning.llm.feature_flags import PERSONAL_SCOPE

        return await get_feature_flag_service().effective_tier_for_request(
            user_id=user_id, scope=PERSONAL_SCOPE, personal_tier=personal_tier
        )

    async def check_credits(user_obj: Any, tokens: int):
        return await check_credit_availability(user_obj, tokens, db_client=None, space_id=None)

    async def charge(user_obj: Any, tokens: int, operation: str):
        return await consume_credits(
            user_obj, tokens, operation=operation, db_client=None, space_id=None
        )

    def queue_task(name: str, kwargs: dict[str, Any]) -> None:
        from src.core.celery_app import celery_app

        celery_app.send_task(name, kwargs=kwargs, ignore_result=True)

    return AskEffects(
        create_message=intelligence_repo.create_message,
        create_action_log=intelligence_repo.create_action_log,
        generate=generate,
        resolve_tier=resolve_tier,
        model_preference=lambda user_id, capability: read_model_preference(
            user_id, capability=capability
        ),
        fallback_model_name=lambda: default_model_for(LlmTask.CHAT_TOOLS_USAGE_FALLBACK),
        check_credits=check_credits,
        credit_usage=get_credit_usage,
        consume_credits=charge,
        cost_calculator=calculate_ai_cost,
        revenue_calculator=calculate_revenue,
        queue_task=queue_task,
        format_list=format_list_component_response,
        format_action=format_action_component_response,
        tool_badge=tool_skill_badge,
        query_badge=query_type_skill_badge,
        extract_suggestion=_extract_suggestion,
        purchase_deep_link=PURCHASE_DEEP_LINK,
    )
