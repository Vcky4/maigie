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
    "context cache keying",
    "page context instruction blocks",
    "context shaping (records to context keys)",
)
STILL_IN_THE_HANDLER = (
    "session resolution",
    "the context fetches themselves",
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

#: Base instructions for a turn inside a shared space room.
_SPACE_ROOM_PAGE_CONTEXT = (
    "You are participating in a shared learning space chat room. "
    "Respond with the space's discussion in mind, not the user's private study history. "
    "Keep responses collaborative and suitable for the whole room."
)

#: Appended when the learner is replying to a specific room message.
_SPACE_ROOM_REPLY_SUFFIX = " When replyContext is present, respond to that specific room message."


def space_room_page_context(*, has_reply_target: bool = False) -> str:
    """Instructions for a turn in a shared space room.

    **"not the user's private study history" is a privacy boundary, not a style note.** A space room is
    shared, so the personal context that makes Ask Maigie useful one-to-one would be a disclosure here.
    The handler enforces the same boundary structurally by skipping retrieval for room turns; this is
    the half of it the model is told.

    Currently unreachable: `chat_helpers._get_circle_group_for_session` is unimplemented and returns
    `None`, so no turn is ever classified as a room turn. Space rooms are out of scope for the Ask
    Maigie plan (§4.2) and this is extracted as-is rather than fixed — but note that a room turn has
    never run, so this text has never reached a model.
    """
    if has_reply_target:
        return _SPACE_ROOM_PAGE_CONTEXT + _SPACE_ROOM_REPLY_SUFFIX
    return _SPACE_ROOM_PAGE_CONTEXT


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
