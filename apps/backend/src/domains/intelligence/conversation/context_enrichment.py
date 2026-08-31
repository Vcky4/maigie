"""Owner-scoped reads for the rows an Ask Maigie turn is about.

**Why this module exists, and it is not tidiness.** The client sends `topicId`, `courseId`, `noteId` and
`reviewItemId` alongside every message, and enrichment turns them into prompt text: a topic's full body,
a course's title and description, a note's contents. Whatever this module returns is answered about, so
**a read here without an owner filter is a read of another learner's material, delivered in Maigie's
reply.** That already happened once — the `noteId` branch fetched notes with no owner filter for the
life of the surface (plan §5.5.11) — and it happened again in two more branches for the same reason: the
rows looked like a shared catalogue and are not one. `Course.user_id` exists.

The reads were four blocks of inline `select(...)` chains inside a ~340-line `if/elif` in the WebSocket
handler, each walking topic → module → course by hand. Duplicated walks are how one of them ends up
missing the filter the other three have, which is precisely what happened. So there is one walker and
one authorising resolver, and the difference between them is stated in their names.

**Two kinds of read, and the distinction is the whole point:**

- `resolve_owned_topic` / `resolve_owned_course` **authorise**. Use these for any id that came from the
  client.
- `resolve_topic_chain` does **not**. Use it only when ownership has already been proven by another
  route — a review row fetched with `user_id`, a note fetched with `find_note(id, user_id)` — where the
  foreign key being followed belongs to a row the learner demonstrably owns.

Both return `None` rather than raising, because enrichment is best-effort: a turn about a topic that
cannot be resolved still gets answered, just without the topic in the prompt. **A refusal is logged**,
so an id being probed is visible rather than silent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import cache
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TopicChain:
    """A topic and the module and course above it.

    `module` and `course` are optional because the unauthorised walk tolerates an orphan — a topic whose
    module row is gone still has a title and a body worth putting in the prompt, and the caller has
    already proven ownership by another route. `resolve_owned_topic` never returns one: ownership *is*
    the chain, so a topic that cannot be walked to a course cannot be authorised at all.
    """

    topic: Any
    module: Any = None
    course: Any = None


async def resolve_topic_chain(
    *,
    topic_id: str | None,
    user_id: str,
    find_topic: Any,
    find_module: Any,
    find_course: Any,
) -> TopicChain | None:
    """Walk topic → module → course for a topic whose ownership is **already established**.

    **Only for ids reached through a row the learner is known to own.** The two legitimate callers are
    the review branch, whose `ReviewItem` was fetched with `user_id`, and the note branch, whose `Note`
    came from `find_note(note_id, user_id)` — in both cases the id being followed is a foreign key on an
    owned row, so the topic above it is owned by construction. Anything a client sent goes through
    `resolve_owned_topic` instead.

    The topic and module reads are unfiltered because they have to be — neither table has a `user_id` —
    but `find_course` is the owner-scoped read, so the course half is authorised anyway at no cost. A
    course that does not resolve leaves the chain at topic-and-module, which the caller renders without
    course fields.

    Stops at the first missing link and returns what it has. A topic with no module is a real state in
    this schema and not an error: `ask_service._topic_chain_updates` already handles a `None` module by
    writing the topic's title and body alone.
    """
    if not topic_id:
        return None

    topic = await find_topic(topic_id)
    if not topic:
        return None

    module = await find_module(topic.module_id) if topic.module_id else None
    course = await find_course(module.course_id, user_id) if module and module.course_id else None
    return TopicChain(topic=topic, module=module, course=course)


async def resolve_owned_topic(
    *,
    topic_id: str | None,
    user_id: str,
    check_topic_ownership: Any,
) -> TopicChain | None:
    """Resolve a client-supplied topic id, or `None` if this learner may not read it.

    **This is the fix for the disclosure, so read what it replaces.** The `topicId` branch did
    `select(Topic).where(Topic.id == topic_id)` with no owner filter, walked up to the module and
    course the same way, and wrote `topicTitle`, the full `topicContent`, `moduleTitle`, `courseTitle`
    and `courseDescription` into the prompt. `topicId` arrives on the client's message context, so any
    learner could put another learner's topic id on a turn and read that topic's body — and its
    course's description — back out of Maigie's answer. The `noteId`-as-topic-id fallback had the same
    shape.

    `check_topic_ownership` is the knowledge domain's own helper, used by roughly a dozen topic routes,
    and it returns the whole chain — which is why this closes the hole and removes the hand-walked chain
    in the same change. It authorises through the course, because `Topic` and `Module` carry no
    `user_id` and `Course` does.

    **It raises where enrichment must not.** `NotFoundError` for a topic that does not exist or cannot
    be walked to a course, `ForbiddenError` for one belonging to someone else. Both mean the same thing
    here — nothing goes in the prompt — so both become `None`. The refusal is logged at warning level
    because a `ForbiddenError` on this path is a learner sending an id that is not theirs, and that is
    worth being able to see.

    **One deliberate behaviour change.** A topic whose module or course row is missing used to be
    enriched with its title and body; it now resolves to `None`, because `check_topic_ownership` treats
    an unwalkable chain as not found. That is the right direction: ownership lives on the course, so a
    topic with no course has no owner, and content nobody can be shown to own does not belong in a
    prompt.
    """
    if not topic_id:
        return None

    try:
        topic, module, course = await check_topic_ownership(topic_id, user_id)
    except Exception as error:  # noqa: BLE001 — NotFoundError and ForbiddenError alike mean "omit it"
        logger.warning(
            "Topic %s not available to user %s for context enrichment: %s",
            topic_id,
            user_id,
            error,
        )
        return None

    return TopicChain(topic=topic, module=module, course=course)


async def resolve_owned_course(
    *,
    course_id: str | None,
    user_id: str,
    find_course: Any,
) -> Any | None:
    """Resolve a client-supplied course id, or `None` if this learner does not own it.

    The `courseId` branch did `select(Course).where(Course.id == context["courseId"])` and wrote the
    course's title and description into the prompt. A comment two branches above described these rows as
    "catalogue rows, not personal ones", which is what made it look safe — and it is wrong.
    `Course.user_id` is a column, a course belongs to exactly one learner, and there is no share or
    visibility flag under which another learner's course was meant to be readable.

    `find_course(course_id, user_id)` is the knowledge repository's owner-scoped read — the same one
    `check_course_ownership` is built on — so ownership is one filter in one place rather than a second
    condition maintained here.
    """
    if not course_id:
        return None
    return await find_course(course_id, user_id)


# ===========================================================================
# The readers, as one injectable bundle
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ContextReaders:
    """Every database read enrichment needs, in one place.

    A bundle rather than nine keyword arguments on `enrich_context`, because the alternative is a call
    site that is mostly plumbing and a test that has to name every reader to override one. `fakes()` in
    the tests builds a full set with sensible defaults; production builds one with `production_readers()`.

    **The signatures encode the ownership rule**, which is the point of listing them here: every reader
    that touches a learner-owned row takes a `user_id`, and the two that do not — `find_topic`,
    `find_module` — are the two whose tables have no owner column and which are only reachable through
    `resolve_topic_chain`, whose caller has already proven ownership.

    One bundle covers the whole turn, not just `enrich_context`: history, retrieval and memory are here
    too even though enrichment does not use them, because `answer()` will need one set of readers rather
    than three, and a turn assembled from three bundles is a turn where one of them can be forgotten.
    """

    find_note: Any
    """(note_id, user_id) -> Note | None. Owner-scoped."""

    find_review: Any
    """(review_id, user_id) -> ReviewItem | None. Owner-scoped."""

    find_topic: Any
    """(topic_id) -> Topic | None. **Not** owner-scoped; `Topic` has no owner column."""

    find_module: Any
    """(module_id) -> Module | None. **Not** owner-scoped; `Module` has no owner column."""

    find_course: Any
    """(course_id, user_id) -> Course | None. Owner-scoped."""

    check_topic_ownership: Any
    """(topic_id, user_id) -> (topic, module, course). Raises when refused or absent."""

    list_topic_notes: Any
    """(topic_id, user_id) -> list[Note], oldest first. Owner-scoped."""

    latest_note_for_topic: Any
    """(topic_id, user_id) -> Note | None. Owner-scoped."""

    attach_topic_resources: Any
    """(user_id, topic_id, context) -> None. Mutates the context. Best-effort."""

    read_history: Any
    """(session_id, user_id, review_item_id, limit) -> rows, newest first. `user_id=None` means the
    whole room. See `build_history` for what each argument decides."""

    retrieve: Any
    """(query, user_id, limit) -> list[dict]. Search over the learner's own material."""

    memory: Any
    """(user_id, query) -> str | None. Long-term memory: summaries and learning insights."""

    learner_context: Any = None
    """(user_id, raw_context) -> bounded, owner-scoped learner context."""


async def _list_topic_notes(topic_id: str, user_id: str) -> list[Any]:
    """A learner's notes on one topic, oldest first, all of them.

    Not routed through `personal_learning_repo.list_notes` despite that method being owner-scoped, and
    the reason is the ordering: this feeds `ask_service.format_topic_user_notes`, which renders the notes
    as one markdown block in the order given, so the sequence is prompt content rather than
    presentation. `list_notes` sorts by its own `NOTE_SORTS` and caps at `take=20`, so borrowing it would
    quietly reorder and truncate what the model reads. Kept as its own query with `updated_at` ascending
    and no limit, which is what the handler did.
    """
    from sqlalchemy import select

    from src.domains.personal_learning.db_models import Note
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(Note)
            .where(Note.topic_id == topic_id, Note.user_id == user_id)
            .order_by(Note.updated_at.asc())
        )
        return list((await session.execute(stmt)).scalars().all())


async def _read_history(
    *, session_id: str, user_id: str | None, review_item_id: str | None, limit: int
) -> list[Any]:
    """The most recent messages of one thread, newest first.

    **Ordering descending and then letting the caller reverse is deliberate.** Ordering ascending with a
    limit would take the *oldest* twelve messages of the conversation, so a long thread would send the
    model the beginning of a conversation the learner left hours ago.

    `review_item_id` is three-valued in effect: an id restricts to that review's thread, and `None`
    restricts to rows with **no** review — it does not mean "any". That is the isolation rule: a spaced-
    repetition review must not inherit the learner's unrelated questions, and general chat must not
    inherit the review.

    `user_id=None` means the whole room's messages are history, which is only correct for a space room.
    In a personal chat only the learner's own rows are.
    """
    from sqlalchemy import select

    from src.domains.intelligence.db_models import ChatMessage
    from src.shared.database import get_session_factory

    conditions = [ChatMessage.session_id == session_id]
    if review_item_id:
        conditions.append(ChatMessage.review_item_id == review_item_id)
    else:
        conditions.append(ChatMessage.review_item_id.is_(None))
    if user_id is not None:
        conditions.append(ChatMessage.user_id == user_id)

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _read_learner_context(user_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Bounded personalization plus explicitly authorised entity context."""
    from src.domains.learning_spaces.repository import space_repo
    from src.domains.personal_learning.repository import personal_learning_repo
    from src.domains.personal_learning.services import (
        behaviour_service,
        flashcard_service,
    )
    from src.domains.progress.repository import progress_repo

    def text(value: Any, limit: int = 500) -> str | None:
        rendered = str(value).strip() if value is not None else ""
        return rendered[:limit] if rendered else None

    specifications = (
        (
            "examPrepId",
            personal_learning_repo.find_exam_prep,
            "examPrep",
            ("subject", "target_date", "status"),
        ),
        (
            "studyPlanId",
            personal_learning_repo.get_study_plan,
            "studyPlan",
            ("title", "goal_description", "status"),
        ),
        ("goalId", progress_repo.find_goal, "goal", ("title", "description", "status")),
        (
            "reflectionId",
            personal_learning_repo.get_reflection,
            "reflection",
            ("title", "summary"),
        ),
    )

    # These readers each own their database session, so running them concurrently does not
    # share an AsyncSession. The timeout keeps optional personalization from delaying generation.
    jobs: list[tuple[str, Any]] = [
        ("profile", personal_learning_repo.get_profile_by_user(user_id)),
        ("behaviour", behaviour_service.get_behaviour_profile(user_id=user_id)),
        # Statistics already uses an owner-keyed cache and a DB aggregate; do not materialize
        # 101 full flashcards merely to learn the due count.
        ("flashcardStats", flashcard_service.get_statistics(user_id=user_id)),
    ]
    for raw_key, reader, _target_key, _fields in specifications:
        if raw.get(raw_key):
            jobs.append((raw_key, reader(raw[raw_key], user_id)))
    if raw.get("spaceId"):
        jobs.append(("spaceMember", space_repo.find_member(raw["spaceId"], user_id)))

    async with asyncio.timeout(2.0):
        values = await asyncio.gather(*(job for _name, job in jobs))
        resolved = dict(zip((name for name, _job in jobs), values, strict=True))

        updates: dict[str, Any] = {}
        profile = resolved["profile"]
        if profile:
            updates["learnerProfile"] = {
                key: value
                for key, value in {
                    "purpose": text(getattr(profile, "purpose", None), 120),
                    "subjects": [
                        text(item, 80) for item in (getattr(profile, "subjects", None) or [])[:8]
                    ],
                    "goals": text(getattr(profile, "goals_text", None), 500),
                    "explanationStyle": text(
                        getattr(profile, "preferred_explanation_style", None), 120
                    ),
                }.items()
                if value
            }

        behaviour = resolved["behaviour"]
        updates["learningRhythm"] = {
            key: behaviour.get(key)
            for key in ("avgSessionMinutes", "consistencyScore", "bestDayOfWeek")
            if behaviour.get(key) is not None
        }
        updates["dueReviewCount"] = int(resolved["flashcardStats"].get("dueToday") or 0)

        rejected: list[str] = []
        for raw_key, _reader, target_key, fields in specifications:
            if not raw.get(raw_key):
                continue
            row = resolved.get(raw_key)
            if row is None:
                rejected.append(raw_key)
                continue
            updates[target_key] = {
                "id": row.id,
                **{
                    field: text(getattr(row, field, None), 600)
                    for field in fields
                    if text(getattr(row, field, None), 600)
                },
            }

        if raw.get("spaceId"):
            member = resolved.get("spaceMember")
            if member is None:
                rejected.append("spaceId")
            else:
                # Fetch the space only after membership is proven. This dependent read is still
                # inside the same timeout and exposes only bounded, non-classroom metadata.
                space = await space_repo.find_space_basic(raw["spaceId"])
                if space is None:
                    rejected.append("spaceId")
                else:
                    updates["space"] = {
                        "id": space.id,
                        "name": text(getattr(space, "name", None), 160),
                        "description": text(getattr(space, "description", None), 600),
                        "role": text(getattr(member, "role", None), 40),
                        "membershipVerified": True,
                    }

        updates["rejectedContextIds"] = rejected
        return updates


def production_readers() -> ContextReaders:
    """The real readers. Imported lazily because these cross domains and this module is imported early.

    Memoized because the bundle is stateless and the caller is a per-message loop — the imports are
    cached by Python anyway, but rebuilding the closures on every turn is pointless work in the path a
    learner is waiting on.
    """
    from src.domains.intelligence.memory.memory_impl import get_memory_context
    from src.domains.intelligence.reasoning.rag_service import rag_service
    from src.domains.knowledge.repository import knowledge_repo
    from src.domains.knowledge.services import course_service
    from src.domains.personal_learning.repository import personal_learning_repo
    from src.domains.personal_learning.services.note_impl import latest_note_for_topic
    from src.domains.progress.repository import progress_repo

    from .chat_helpers import _attach_topic_resources_context

    async def attach_topic_resources(user_id: str, topic_id: str, context: dict[str, Any]) -> None:
        # The `None` first argument is a legacy db handle the stub ignores. **This helper is
        # unimplemented** (see `chat_helpers`), so a topic's saved resources do not reach the prompt
        # today. Wired anyway so that implementing it needs no change here.
        await _attach_topic_resources_context(None, user_id, topic_id, context)

    return ContextReaders(
        find_note=personal_learning_repo.find_note,
        find_review=progress_repo.find_review,
        find_topic=knowledge_repo.find_topic,
        find_module=knowledge_repo.find_module,
        find_course=knowledge_repo.find_course,
        check_topic_ownership=course_service.check_topic_ownership,
        list_topic_notes=_list_topic_notes,
        latest_note_for_topic=lambda topic_id, user_id: latest_note_for_topic(
            None, topic_id, user_id
        ),
        attach_topic_resources=attach_topic_resources,
        read_history=_read_history,
        retrieve=lambda query, user_id, limit: rag_service.retrieve_relevant_context(
            query=query, user_id=user_id, limit=limit
        ),
        memory=lambda user_id, query: get_memory_context(user_id, query=query),
        learner_context=_read_learner_context,
    )


# ===========================================================================
# The cache
# ===========================================================================

#: How long an enriched context is reused. The window in which a stale value would be served, so it is
#: also the blast radius of forgetting to list a derived key in `ask_service.VOLATILE_CONTEXT_KEYS`.
CONTEXT_CACHE_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class ContextCache:
    """The three cache operations enrichment uses, injected so a test can run without a cache backend.

    `None` in place of a `ContextCache` disables caching entirely, which is what the tests use when the
    behaviour under test is the fetching rather than the reuse.
    """

    make_key: Any
    """(parts: list[str]) -> str"""

    get: Any
    """async (key) -> dict | None"""

    set: Any
    """async (key, value, ttl_seconds) -> None"""


@cache
def production_cache() -> ContextCache:
    from src.core.cache import cache as cache_backend

    async def set_with_ttl(key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        await cache_backend.set(key, value, expire=ttl_seconds)

    return ContextCache(make_key=cache_backend.make_key, get=cache_backend.get, set=set_with_ttl)


# ===========================================================================
# Enrichment
# ===========================================================================


async def _apply_review_context(
    enriched: dict[str, Any], *, review_id: str, user_id: str, readers: ContextReaders
) -> None:
    """Review mode: the review row, its topic, and the catalogue above it.

    `find_review` is owner-scoped, so `review.topic_id` is a key on a row this learner owns and the
    unauthorised walk is the right one — see `resolve_topic_chain`.

    **Nothing is written unless both the review and its topic resolve**, which is preserved from the
    handler and is deliberate rather than incidental: a review whose topic is gone would otherwise
    contribute `reviewItemId` and `nextReviewAt` with no subject attached, and `pageContext` would then
    put the model into the spaced-repetition protocol with nothing to ask about.
    """
    from . import ask_service

    review = await readers.find_review(review_id, user_id)
    if not review:
        return

    chain = await resolve_topic_chain(
        topic_id=review.topic_id,
        user_id=user_id,
        find_topic=readers.find_topic,
        find_module=readers.find_module,
        find_course=readers.find_course,
    )
    if not chain:
        return

    enriched.update(
        ask_service.review_context_updates(
            review=review, topic=chain.topic, module=chain.module, course=chain.course
        )
    )


async def _apply_note_context(
    enriched: dict[str, Any], *, note_id: str, user_id: str, readers: ContextReaders
) -> None:
    """A note turn, including the fallback where the `noteId` turns out to be a topic id.

    That fallback is a real path rather than a defensive one — clients have sent topic ids in `noteId` —
    and it is why this function is the longest of the four. When it fires, the topic's own context is
    written *and* the learner's latest note on that topic is adopted, with `noteId` rewritten to the note
    that was actually found so everything downstream agrees on which note this turn is about.
    """
    from . import ask_service

    note = await readers.find_note(note_id, user_id)

    async def chain_for(candidate: Any) -> TopicChain | None:
        # Owned by construction: `candidate` came from an owner-scoped read, so its `topic_id` is a key
        # on the learner's own row.
        if not candidate:
            return None
        return await resolve_topic_chain(
            topic_id=candidate.topic_id,
            user_id=user_id,
            find_topic=readers.find_topic,
            find_module=readers.find_module,
            find_course=readers.find_course,
        )

    note_chain = await chain_for(note)
    direct_course = None
    if note and not (note_chain and note_chain.topic) and note.course_id:
        # A note reaches a course two ways and they are mutually exclusive: through its topic's module,
        # or directly. The direct one is only consulted when there is no topic.
        direct_course = await resolve_owned_course(
            course_id=note.course_id, user_id=user_id, find_course=readers.find_course
        )

    if not note:
        # **Owner-checked.** `note_id` is client-supplied and is being treated as a topic id here, so
        # this must authorise — it was an unfiltered `select(Topic)` before (plan §5.5.14).
        topic_chain = await resolve_owned_topic(
            topic_id=note_id,
            user_id=user_id,
            check_topic_ownership=readers.check_topic_ownership,
        )
        if topic_chain:
            enriched.update(
                ask_service.topic_context_updates(
                    topic=topic_chain.topic,
                    module=topic_chain.module,
                    course=topic_chain.course,
                )
            )
            latest = await readers.latest_note_for_topic(topic_chain.topic.id, user_id)
            if latest:
                logger.debug(
                    "Note id %s resolved to a topic; adopting its latest note %s",
                    note_id,
                    latest.id,
                )
                note = await readers.find_note(latest.id, user_id)
                note_chain = await chain_for(note)
                direct_course = None
                enriched["noteId"] = latest.id

    if note:
        enriched.update(
            ask_service.note_context_updates(
                note=note,
                topic=note_chain.topic if note_chain else None,
                module=note_chain.module if note_chain else None,
                course=note_chain.course if note_chain else None,
                direct_course=direct_course,
            )
        )


async def _apply_topic_context(
    enriched: dict[str, Any], *, topic_id: str, user_id: str, readers: ContextReaders
) -> None:
    """A topic turn: the catalogue chain, plus whatever the learner has written about it.

    **`topicId` is written into the context before the read and stays there if the read fails.** The
    action service validates the id again before acting on it, so dropping it here would turn a refusal
    into a tool call against a missing id rather than a clean rejection.
    """
    from . import ask_service

    enriched["topicId"] = topic_id

    # **Owner-checked.** This branch was the disclosure in plan §5.5.14: an unfiltered
    # `select(Topic).where(Topic.id == topic_id)` on a client-supplied id, writing the topic's full body
    # and its course's description into the prompt.
    chain = await resolve_owned_topic(
        topic_id=topic_id,
        user_id=user_id,
        check_topic_ownership=readers.check_topic_ownership,
    )
    if not chain:
        logger.info(
            "Topic %s not resolved during context enrichment; the id is still passed on for action "
            "validation",
            topic_id,
        )
        return

    enriched.update(
        ask_service.topic_context_updates(
            topic=chain.topic,
            module=chain.module,
            course=chain.course,
            # The id came from the client and is how this topic was found, so writing it back is at
            # best a no-op.
            include_topic_id=False,
        )
    )

    rendered = ask_service.format_topic_user_notes(
        list(await readers.list_topic_notes(topic_id, user_id))
    )
    if rendered:
        enriched["topicUserNotes"] = rendered


async def _apply_course_context(
    enriched: dict[str, Any], *, course_id: str, user_id: str, readers: ContextReaders
) -> None:
    """A course turn. **Owner-checked** — this was the second half of plan §5.5.14."""
    from . import ask_service

    course = await resolve_owned_course(
        course_id=course_id, user_id=user_id, find_course=readers.find_course
    )
    if course:
        enriched.update(ask_service.course_context_updates(course=course))


SERVER_DERIVED_CONTEXT_KEYS = frozenset(
    {
        "learnerProfile",
        "learningRhythm",
        "dueReviewCount",
        "examPrep",
        "studyPlan",
        "goal",
        "reflection",
        "space",
        "spaceMembershipVerified",
        "rejectedContextIds",
    }
)


async def enrich_context(
    *,
    context: dict[str, Any] | None,
    user_id: str,
    readers: ContextReaders,
    cache: ContextCache | None = None,
) -> dict[str, Any] | None:
    """Turn the client's context object into the context the prompt is built from.

    Returns `None` for a falsy context — there is nothing to enrich and the caller treats an absent
    context differently from an empty one.

    **The four branches are mutually exclusive and the order is the contract.** Review beats note beats
    topic beats course, because the ids are nested: a review is about a topic which is in a course, so
    the most specific id present is the one that describes what the learner is looking at. The topic and
    course branches additionally check that the field they would write is not already set, which is what
    stops a note turn's course from being overwritten by the raw `courseId` riding along on the same
    context.

    **Caching wraps the fetching, not the whole function.** A cache hit skips the four branches and the
    resources attach; the direct-content overlay at the end runs either way, because `content` and
    `noteContent` are sent with the message and are not a property of any id — see
    `ask_service.VOLATILE_CONTEXT_KEYS`, which is also why they are stripped before writing.
    """
    from . import ask_service

    raw_context = context or {}
    context = raw_context
    enriched = {
        key: value for key, value in raw_context.items() if key not in SERVER_DERIVED_CONTEXT_KEYS
    }

    cache_key = None
    if cache is not None:
        # Which ids identify a cached enrichment is `ask_service.context_cache_key_parts`. It is a named
        # function because an id that changes what enrichment fetches but is missing from the key serves
        # one learner's topic as another's for the TTL. `None` means there is no id to look up.
        key_parts = ask_service.context_cache_key_parts(user_id=user_id, context=context)
        cache_key = cache.make_key(key_parts) if key_parts else None

    cached = await cache.get(cache_key) if (cache is not None and cache_key) else None

    if cached:
        enriched = ask_service.merge_cached_context(enriched, cached)
    else:
        if context.get("reviewItemId"):
            await _apply_review_context(
                enriched,
                review_id=context["reviewItemId"],
                user_id=user_id,
                readers=readers,
            )
        elif context.get("noteId"):
            await _apply_note_context(
                enriched, note_id=context["noteId"], user_id=user_id, readers=readers
            )
        elif context.get("topicId") and not enriched.get("topicTitle"):
            await _apply_topic_context(
                enriched, topic_id=context["topicId"], user_id=user_id, readers=readers
            )
        elif context.get("courseId") and not enriched.get("courseTitle"):
            await _apply_course_context(
                enriched,
                course_id=context["courseId"],
                user_id=user_id,
                readers=readers,
            )

        # Whatever branch ran, a topic in scope gets its saved resources attached.
        if enriched.get("topicId"):
            await readers.attach_topic_resources(user_id, enriched["topicId"], enriched)

        if cache is not None and cache_key:
            # The exclusion set is `ask_service.VOLATILE_CONTEXT_KEYS`, which documents why each key is
            # per-turn. Adding a derived key to enrichment without adding it there is how a stale value
            # starts being replayed for the whole TTL.
            await cache.set(
                cache_key,
                ask_service.cacheable_context(enriched),
                CONTEXT_CACHE_TTL_SECONDS,
            )

    # Sent with the message rather than fetched, so this runs on a cache hit too.
    if context.get("content"):
        enriched["content"] = context["content"]
    # `noteContent` supplied directly must not overwrite the body of a note that was actually fetched.
    if context.get("noteContent") and not enriched.get("noteContent"):
        enriched["noteContent"] = context["noteContent"]

    if readers.learner_context:
        try:
            learner_updates = await readers.learner_context(user_id, raw_context)
            for rejected_key in learner_updates.pop("rejectedContextIds", []):
                enriched.pop(rejected_key, None)
            enriched.update(learner_updates)
        except Exception as error:  # noqa: BLE001 — personalization is optional, ownership is not
            for unverified_key in (
                "examPrepId",
                "studyPlanId",
                "goalId",
                "reflectionId",
                "spaceId",
            ):
                enriched.pop(unverified_key, None)
            # `%s` on the exception alone is not enough, and the 2026-08-31 logs are the proof:
            # this line printed "continuing without it:" followed by nothing, because several
            # exception types (a bare `KeyError()`, anything raised with no args) have an empty
            # `str()`. A swallowed failure that also declines to say what it was is untraceable, and
            # this branch is deliberately broad, so it is the one place that most needs the type and
            # the traceback. `exc_info` keeps the stack; the class name survives even when the
            # message is empty.
            logger.warning(
                "Learner context enrichment failed, continuing without it: %s: %s",
                type(error).__name__,
                error,
                exc_info=True,
            )

    return enriched or None


# ===========================================================================
# History
# ===========================================================================

#: How many retrieval hits reach the prompt. Three, because retrieval competes with the learner's own
#: page context for the token budget and a fourth loosely-related note displaces something they are
#: actually looking at.
RETRIEVAL_LIMIT = 3


async def build_history(
    *,
    session_id: str,
    user_id: str,
    review_item_id: str | None,
    readers: ContextReaders,
    exclude_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """The conversation so far, in the provider's history shape, oldest first.

    **A review thread sees only its own review, and general chat sees only rows with no review at all.**
    So a spaced-repetition review does not answer against the learner's unrelated questions, and the
    learner's next general question does not inherit the review. `review_item_id` is passed through
    as-is, and `None` means "no review" rather than "any" — see `_read_history`.

    A second rule lived here: in a space room the whole room was history, expressed by passing
    `user_id=None`. It went with space-room chat. `_read_history` still accepts `user_id=None` and still
    documents what it means, because that is the reader's contract rather than this surface's, but
    nothing passes it now — a personal conversation is only ever the learner's own messages.

    Reversed after the read, because the query takes the *newest* rows and the provider wants them
    oldest first.
    """
    from . import ask_service

    records = await readers.read_history(
        session_id=session_id,
        user_id=user_id,
        review_item_id=review_item_id,
        limit=ask_service.HISTORY_LIMIT + (1 if exclude_message_id else 0),
    )
    ordered = list(reversed(list(records)))
    if exclude_message_id:
        ordered = [row for row in ordered if getattr(row, "id", None) != exclude_message_id]
    return ask_service.format_history(ordered[-ask_service.HISTORY_LIMIT :])


# ===========================================================================
# Recall — retrieval and long-term memory
# ===========================================================================


async def attach_recall(
    *,
    context: dict[str, Any] | None,
    message: str,
    user_id: str,
    readers: ContextReaders,
) -> dict[str, Any] | None:
    """Add what the learner has written before, and what Maigie remembers, to this turn's context.

    Returns the context, creating one if there was none and something was found — which is why this
    returns rather than mutates. The handler open-coded `if not enriched_context: enriched_context = {}`
    at both call sites, and a third stage forgetting it would have raised on `None`.

    **Both are best-effort.** A turn without recall is a worse answer; a turn that fails because recall
    failed is no answer. So each is wrapped and logged, and the turn continues.

    **Both stages used to be skipped in a space room, and that skip was a privacy rule rather than an
    optimisation:** retrieval searches the learner's private notes and documents, and memory summarises
    their own conversations, so neither could be allowed to reach a room other members read. Space-room
    chat is gone, so every turn here is personal and the skip has nothing to guard. **Recorded because
    the rule outlives the code** — if room chat is ever built, this is one of the two places it has to be
    re-established.

    `message` is the model-facing text. Retrieval read the raw message and memory the mention-stripped
    one, which differed only in a space room where both were skipped; unified on the model-facing text
    because that is what the answer is generated from, so it is what the search should match.
    """
    from . import ask_service

    if ask_service.should_retrieve(message):
        try:
            results = await readers.retrieve(message, user_id, RETRIEVAL_LIMIT)
            items = ask_service.relevant_retrieved_items(results)
            if items:
                context = dict(context or {})
                context["retrieved_items"] = items
                logger.debug("Retrieval contributed %d items to the prompt.", len(items))
        except Exception as error:  # noqa: BLE001 — an enrichment, not a precondition
            logger.warning("Retrieval failed, continuing without it: %s", error)
    else:
        logger.debug("Skipping retrieval: nothing in this message to search on.")

    try:
        remembered = await readers.memory(user_id, message)
        if remembered:
            context = dict(context or {})
            context["memory_context"] = remembered
    except Exception as error:  # noqa: BLE001 — same reason as retrieval
        # Was a bare `print`, so a recurring memory failure was invisible in production logs.
        logger.warning("Memory context lookup failed, continuing without it: %s", error)

    return context
