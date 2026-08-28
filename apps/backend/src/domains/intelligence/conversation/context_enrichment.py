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


@cache
def production_readers() -> ContextReaders:
    """The real readers. Imported lazily because these cross domains and this module is imported early.

    Memoized because the bundle is stateless and the caller is a per-message loop — the imports are
    cached by Python anyway, but rebuilding the closures on every turn is pointless work in the path a
    learner is waiting on.
    """
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
        topic_id=topic_id, user_id=user_id, check_topic_ownership=readers.check_topic_ownership
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

    if not context:
        return None

    enriched = context.copy()

    cache_key = None
    if cache is not None:
        # Which ids identify a cached enrichment is `ask_service.context_cache_key_parts`. It is a named
        # function because an id that changes what enrichment fetches but is missing from the key serves
        # one learner's topic as another's for the TTL. `None` means there is no id to look up.
        key_parts = ask_service.context_cache_key_parts(user_id=user_id, context=context)
        cache_key = cache.make_key(key_parts) if key_parts else None

    cached = await cache.get(cache_key) if (cache is not None and cache_key) else None

    if cached:
        enriched = ask_service.merge_cached_context(context, cached)
    else:
        if context.get("reviewItemId"):
            await _apply_review_context(
                enriched, review_id=context["reviewItemId"], user_id=user_id, readers=readers
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
                enriched, course_id=context["courseId"], user_id=user_id, readers=readers
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

    return enriched
