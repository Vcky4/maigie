"""Every row the chat pipeline reads by a client-supplied id must be read as its owner's.

`noteId`, `topicId`, `courseId` and `reviewItemId` arrive on the client's message context and are used
to enrich the prompt. Whatever enrichment fetches is written into the prompt and answered about, so an
unfiltered read here is another learner's material delivered in Maigie's reply.

**This file used to say "Topics and courses are catalogue rows and are shared by design". That was
wrong, and stating it here is what let the second hole survive the fix for the first.** `Course.user_id`
is a column, a course belongs to exactly one learner, and there is no share or visibility flag on it.
`Topic` and `Module` carry no `user_id` — they are owned *through* the course, which is why the knowledge
domain authorises a topic with `check_topic_ownership` walking topic → module → course, in about twelve
routes.

Three holes of the same shape, found in two passes:

- **`noteId`** — `select(Note).where(Note.id == note_id)`, no owner filter, injecting `noteTitle`,
  `noteSummary` and the full `noteContent`. Fixed 2026-08-27.
- **`topicId`** — `select(Topic).where(Topic.id == topic_id)`, no owner filter, walking up to module and
  course by hand and injecting `topicTitle`, the full `topicContent`, `moduleTitle`, `courseTitle` and
  `courseDescription`. Fixed 2026-08-28.
- **`courseId`** — `select(Course).where(Course.id == ...)`, no owner filter, injecting the course's
  title and description. Fixed 2026-08-28. The `noteId`-as-topic-id fallback had the `topicId` shape and
  was fixed with it.

The reads now go through the repositories' own owner-scoped methods —
`personal_learning_repo.find_note(id, user_id)`, `knowledge_repo.find_course(id, user_id)` and
`course_service.check_topic_ownership(id, user_id)` — resolved in
`intelligence/conversation/context_enrichment.py`, which is injectable and therefore testable.

Two kinds of test here, and both are needed:

- **Behavioural**, against `context_enrichment` with fake readers. These assert the rule.
- **A source scan** over the conversation package, because the enrichment *body* is still inline in
  `register_chat_websocket_routes` and cannot be driven without a live database, socket and model. The
  scan asserts that no hand-rolled `select` of an ownable model reappears there. It is the test that
  would have caught all three holes, and its value is that it fails on the *next* one.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domains.intelligence.conversation import context_enrichment

CONVERSATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "domains"
    / "intelligence"
    / "conversation"
)

#: Models whose rows belong to a learner, under the names they are imported as in this package.
#:
#: `Topic` and `Module` are here despite having no `user_id` of their own: they are owned through the
#: course, and a hand-rolled `select(Topic)` by a client-supplied id is exactly the read that cannot
#: prove it. `Note` is the one that was exploited.
OWNABLE_MODEL_NAMES = {"Note", "NoteModel", "Topic", "Course", "Module"}

#: What an owner-scoped read looks like. The repository helpers carry the filter themselves.
OWNERSHIP_MARKERS = ("user_id", "find_note", "find_course", "check_topic_ownership")


def _statements_selecting_ownable_models() -> list[tuple[str, int, str]]:
    """Return (file, line, source) for every statement containing a `select(<ownable model>)` call."""
    found: list[tuple[str, int, str]] = []

    for path in sorted(CONVERSATION.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue

            def selects_ownable(scope: ast.AST) -> bool:
                for inner in ast.walk(scope):
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    if not (isinstance(func, ast.Name) and func.id == "select"):
                        continue
                    if any(
                        isinstance(arg, ast.Name) and arg.id in OWNABLE_MODEL_NAMES
                        for arg in inner.args
                    ):
                        return True
                return False

            if not selects_ownable(node):
                continue

            # Only the innermost statement, so a `select` nested in a big `async with` is attributed to
            # its own assignment rather than to the whole block.
            if any(
                isinstance(child, ast.stmt)
                and child is not node
                and selects_ownable(child)
                for child in ast.walk(node)
            ):
                continue

            found.append(
                (path.name, node.lineno, ast.get_source_segment(source, node) or "")
            )

    return found


_OWNABLE_READS = _statements_selecting_ownable_models()


def test_the_scan_finds_the_reads_it_is_meant_to_check():
    """Guards against the scan silently matching nothing and passing vacuously — which would be
    indistinguishable from the code being safe."""
    assert _OWNABLE_READS, (
        "no `select(<ownable model>)` statements found in the conversation package. If the enrichment "
        "reads moved, point this scan at their new home rather than deleting it."
    )


@pytest.mark.parametrize(
    "filename,lineno,source",
    _OWNABLE_READS,
    ids=[f"{name}:{line}" for name, line, _ in _OWNABLE_READS],
)
def test_every_read_of_an_ownable_row_is_owner_scoped(
    filename: str, lineno: int, source: str
) -> None:
    assert any(marker in source for marker in OWNERSHIP_MARKERS), (
        f"{filename}:{lineno} reads an ownable row without an owner filter.\n\n"
        "Note, Topic, Course and Module rows all reach the prompt through context enrichment, and the "
        "ids come from the client's message context. An unfiltered read exposes another learner's "
        "material in Maigie's answer. Use the repository's owner-scoped read — `find_note(id, "
        "user_id)`, `find_course(id, user_id)` — or `check_topic_ownership(id, user_id)` for a topic, "
        "which authorises through the course.\n\n"
        f"{source}"
    )


def test_the_note_read_goes_through_the_repository() -> None:
    """Enrichment resolves a note through `find_note`, which carries the owner filter, rather than
    hand-rolling it. Two owner-filtered paths are two things to keep correct; the hole existed because
    one of them was written by hand and the filter was left off.

    Asserted against the readers bundle rather than a call count, because `enrich_context` now calls one
    injected `find_note` from two places and counting call sites would pass on the wrong thing.
    """
    from src.domains.personal_learning.repository import personal_learning_repo

    readers = context_enrichment.production_readers()
    # `==` rather than `is`: two bound methods off the same instance are equal but not identical.
    assert readers.find_note == personal_learning_repo.find_note
    assert readers.find_review.__name__ == "find_review"


def test_no_catalogue_read_is_hand_rolled_in_the_conversation_package() -> None:
    """Neither the handler nor `context_enrichment` may build its own topic → module → course walk.

    Two hand-walked chains is how one of them ends up missing the filter the other has — which is
    literally what happened. This asserts the walk has exactly one home, `resolve_topic_chain`, and that
    the chain is reached through the injected readers rather than through `select`.
    """
    offenders: list[str] = []
    for path in sorted(CONVERSATION.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "select"
                and any(
                    isinstance(arg, ast.Name)
                    and arg.id in {"Topic", "Course", "Module"}
                    for arg in node.args
                )
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], (
        f"a catalogue read is hand-rolled at {offenders}. Use "
        "`context_enrichment.resolve_owned_topic` for a client-supplied id, or `resolve_topic_chain` "
        "when ownership is already proven by the row the id hangs off."
    )


def test_every_reader_that_touches_an_owned_row_takes_an_owner() -> None:
    """The readers bundle is the contract. `find_topic` and `find_module` are the only two without an
    owner argument, and that is because their tables have no owner column — they are reachable only
    through `resolve_topic_chain`, whose caller has already proven ownership.

    This is the test that would have caught §5.5.14 as a design error rather than a code review note.
    """
    import inspect

    readers = context_enrichment.production_readers()
    exempt = {"find_topic", "find_module"}

    for field in (
        "find_note",
        "find_review",
        "find_course",
        "check_topic_ownership",
        "list_topic_notes",
        "latest_note_for_topic",
    ):
        reader = getattr(readers, field)
        params = list(inspect.signature(reader).parameters)
        assert any("user" in p for p in params), (
            f"reader `{field}` takes {params} and none of them is an owner. Every read of a "
            "learner-owned row must be scoped, or it is the §5.5.14 defect again."
        )

    for field in exempt:
        params = list(inspect.signature(getattr(readers, field)).parameters)
        assert not any("user" in p for p in params), (
            f"reader `{field}` now takes an owner. Good — but move it out of the exempt set in this "
            "test and out of `resolve_topic_chain`'s unauthorised walk."
        )


# ---------------------------------------------------------------------------
# Behavioural: the resolvers
# ---------------------------------------------------------------------------


def a_topic(
    topic_id="topic_1", module_id="module_1", title="Entropy", content="Disorder."
):
    return SimpleNamespace(
        id=topic_id, module_id=module_id, title=title, content=content
    )


def a_module(module_id="module_1", course_id="course_1", title="Thermodynamics"):
    return SimpleNamespace(id=module_id, course_id=course_id, title=title)


def a_course(course_id="course_1", title="Physics", description="A course"):
    return SimpleNamespace(id=course_id, title=title, description=description)


class ForbiddenError(Exception):
    """Stands in for the domain's own error. The resolver must not care which it was."""


class NotFoundError(Exception):
    pass


class TestResolveOwnedTopic:
    @pytest.mark.asyncio
    async def test_an_owned_topic_resolves_with_its_chain(self):
        async def check(topic_id, user_id):
            assert (topic_id, user_id) == ("topic_1", "user_1")
            return a_topic(), a_module(), a_course()

        chain = await context_enrichment.resolve_owned_topic(
            topic_id="topic_1", user_id="user_1", check_topic_ownership=check
        )
        assert chain is not None
        assert chain.topic.title == "Entropy"
        assert chain.module.title == "Thermodynamics"
        assert chain.course.title == "Physics"

    @pytest.mark.asyncio
    async def test_another_learners_topic_resolves_to_nothing(self):
        """The hole this closes. A refusal must contribute no prompt context at all — not a title, not
        a body, not the course description above it."""

        async def check(_topic_id, _user_id):
            raise ForbiddenError("You do not own this topic")

        assert (
            await context_enrichment.resolve_owned_topic(
                topic_id="someone_elses_topic",
                user_id="user_1",
                check_topic_ownership=check,
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_a_topic_that_does_not_exist_resolves_to_nothing(self):
        async def check(_topic_id, _user_id):
            raise NotFoundError("Topic")

        assert (
            await context_enrichment.resolve_owned_topic(
                topic_id="nope", user_id="user_1", check_topic_ownership=check
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_a_refusal_is_not_distinguishable_from_an_absence(self):
        """Deliberate: enrichment is best-effort, so both mean "omit it". A caller that could tell them
        apart would be a way to probe which ids exist."""

        async def forbidden(_topic_id, _user_id):
            raise ForbiddenError("nope")

        async def missing(_topic_id, _user_id):
            raise NotFoundError("nope")

        first = await context_enrichment.resolve_owned_topic(
            topic_id="t", user_id="u", check_topic_ownership=forbidden
        )
        second = await context_enrichment.resolve_owned_topic(
            topic_id="t", user_id="u", check_topic_ownership=missing
        )
        assert first == second is None

    @pytest.mark.asyncio
    async def test_no_topic_id_does_not_hit_the_reader(self):
        async def check(_topic_id, _user_id):
            raise AssertionError("must not be called without an id")

        assert (
            await context_enrichment.resolve_owned_topic(
                topic_id=None, user_id="user_1", check_topic_ownership=check
            )
            is None
        )


class TestResolveOwnedCourse:
    @pytest.mark.asyncio
    async def test_the_owner_id_reaches_the_read(self):
        """The whole fix is that this argument exists. The read was `select(Course).where(Course.id ==
        ...)` with nothing else in the `where`."""
        seen = {}

        async def find_course(course_id, user_id):
            seen["args"] = (course_id, user_id)
            return a_course()

        course = await context_enrichment.resolve_owned_course(
            course_id="course_1", user_id="user_1", find_course=find_course
        )
        assert seen["args"] == ("course_1", "user_1")
        assert course.title == "Physics"

    @pytest.mark.asyncio
    async def test_another_learners_course_resolves_to_nothing(self):
        async def find_course(_course_id, _user_id):
            return None

        assert (
            await context_enrichment.resolve_owned_course(
                course_id="theirs", user_id="user_1", find_course=find_course
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_no_course_id_does_not_hit_the_reader(self):
        async def find_course(_course_id, _user_id):
            raise AssertionError("must not be called without an id")

        assert (
            await context_enrichment.resolve_owned_course(
                course_id=None, user_id="user_1", find_course=find_course
            )
            is None
        )


class TestResolveTopicChain:
    """The unauthorised walk. Legitimate only where the id is a foreign key on a row the learner has
    already been proven to own — a `ReviewItem` read with `user_id`, or a `Note` from `find_note`.
    """

    @staticmethod
    def readers(topic=None, module=None, course=None):
        async def find_topic(_topic_id):
            return topic

        async def find_module(_module_id):
            return module

        async def find_course(_course_id, _user_id):
            return course

        return {
            "find_topic": find_topic,
            "find_module": find_module,
            "find_course": find_course,
        }

    @pytest.mark.asyncio
    async def test_it_walks_to_the_course(self):
        chain = await context_enrichment.resolve_topic_chain(
            topic_id="topic_1",
            user_id="user_1",
            **self.readers(topic=a_topic(), module=a_module(), course=a_course()),
        )
        assert (chain.topic.id, chain.module.id, chain.course.id) == (
            "topic_1",
            "module_1",
            "course_1",
        )

    @pytest.mark.asyncio
    async def test_an_orphaned_topic_is_not_an_error(self):
        """A topic with no module is a real state, and the caller renders its title and body without
        course fields. This is the one behaviour `resolve_owned_topic` deliberately does *not* have.
        """
        chain = await context_enrichment.resolve_topic_chain(
            topic_id="topic_1",
            user_id="user_1",
            **self.readers(topic=a_topic(module_id=None)),
        )
        assert chain.topic is not None
        assert chain.module is None
        assert chain.course is None

    @pytest.mark.asyncio
    async def test_the_course_half_is_still_owner_scoped(self):
        """The topic and module reads cannot be filtered — neither table has a `user_id` — but the
        course read can be, and is, so the walk cannot reach another learner's course description.
        """
        seen = {}

        async def find_topic(_topic_id):
            return a_topic()

        async def find_module(_module_id):
            return a_module()

        async def find_course(course_id, user_id):
            seen["args"] = (course_id, user_id)
            return None

        chain = await context_enrichment.resolve_topic_chain(
            topic_id="topic_1",
            user_id="user_1",
            find_topic=find_topic,
            find_module=find_module,
            find_course=find_course,
        )
        assert seen["args"] == ("course_1", "user_1")
        assert chain.course is None

    @pytest.mark.asyncio
    async def test_a_missing_topic_resolves_to_nothing(self):
        assert (
            await context_enrichment.resolve_topic_chain(
                topic_id="gone", user_id="user_1", **self.readers()
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_no_topic_id_resolves_to_nothing(self):
        assert (
            await context_enrichment.resolve_topic_chain(
                topic_id=None, user_id="user_1", **self.readers(topic=a_topic())
            )
            is None
        )


# ---------------------------------------------------------------------------
# Behavioural: enrich_context
# ---------------------------------------------------------------------------
#
# These are the first tests of the enrichment *orchestration* — the branch precedence, the cache
# semantics, the direct-content overlay. All of it was inline in a WebSocket receive loop and needed a
# live database, socket and model to reach, so none of it had ever been exercised. The branch order in
# particular is a contract nothing stated: which id wins decides what the model is told the learner is
# looking at.


def a_note(
    note_id="note_1", topic_id=None, course_id=None, title="My note", content="Body."
):
    return SimpleNamespace(
        id=note_id,
        topic_id=topic_id,
        course_id=course_id,
        title=title,
        content=content,
        summary="A summary",
    )


def a_review(review_id="review_1", topic_id="topic_1"):
    return SimpleNamespace(
        id=review_id, topic_id=topic_id, next_review_at="2026-09-01T00:00:00+00:00"
    )


def fake_readers(**overrides):
    """A full reader set that finds nothing, so a test only names what it wants found."""

    async def none1(_a):
        return None

    async def none2(_a, _b):
        return None

    async def empty(_a, _b):
        return []

    async def raises(_topic_id, _user_id):
        raise NotFoundError("Topic")

    async def attach(_user_id, _topic_id, _context):
        return None

    async def no_history(*, session_id, user_id, review_item_id, limit):
        return []

    async def no_hits(_query, _user_id, _limit):
        return []

    defaults = {
        "find_note": none2,
        "find_review": none2,
        "find_topic": none1,
        "find_module": none1,
        "find_course": none2,
        "check_topic_ownership": raises,
        "list_topic_notes": empty,
        "latest_note_for_topic": none2,
        "attach_topic_resources": attach,
        "read_history": no_history,
        "retrieve": no_hits,
        "memory": none2,
    }
    defaults.update(overrides)
    return context_enrichment.ContextReaders(**defaults)


def owns(topic=None, module=None, course=None):
    async def check(_topic_id, _user_id):
        if topic is None:
            raise NotFoundError("Topic")
        return topic, module, course

    return check


class TestEnrichContextBranchPrecedence:
    """Review beats note beats topic beats course. The ids nest — a review is about a topic which is in
    a course — so the most specific id present is the one that describes what the learner is looking at.
    Nothing stated this before; it was the order of an `if/elif` chain 900 lines into a function.
    """

    @pytest.mark.asyncio
    async def test_a_review_id_wins_over_every_other_id(self):
        async def find_review(_review_id, _user_id):
            return a_review()

        async def find_topic(_topic_id):
            return a_topic()

        async def unexpected(_a, _b):
            raise AssertionError(
                "the note branch must not run when a reviewItemId is present"
            )

        enriched = await context_enrichment.enrich_context(
            context={
                "reviewItemId": "review_1",
                "noteId": "note_1",
                "topicId": "topic_1",
                "courseId": "course_1",
            },
            user_id="user_1",
            readers=fake_readers(
                find_review=find_review, find_topic=find_topic, find_note=unexpected
            ),
        )
        assert enriched["reviewItemId"] == "review_1"
        assert enriched["topicTitle"] == "Entropy"

    @pytest.mark.asyncio
    async def test_a_note_id_wins_over_topic_and_course(self):
        async def find_note(_note_id, _user_id):
            return a_note()

        enriched = await context_enrichment.enrich_context(
            context={"noteId": "note_1", "topicId": "topic_1", "courseId": "course_1"},
            user_id="user_1",
            readers=fake_readers(
                find_note=find_note,
                check_topic_ownership=owns(a_topic(), a_module(), a_course()),
            ),
        )
        assert enriched["noteTitle"] == "My note"
        # The topic branch did not also run: it would have written the topic's own title.
        assert "topicTitle" not in enriched

    @pytest.mark.asyncio
    async def test_a_topic_id_wins_over_a_course_id(self):
        enriched = await context_enrichment.enrich_context(
            context={"topicId": "topic_1", "courseId": "course_1"},
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course())
            ),
        )
        assert enriched["topicTitle"] == "Entropy"

    @pytest.mark.asyncio
    async def test_a_course_id_alone_reaches_the_course_branch(self):
        async def find_course(_course_id, _user_id):
            return a_course()

        enriched = await context_enrichment.enrich_context(
            context={"courseId": "course_1"},
            user_id="user_1",
            readers=fake_readers(find_course=find_course),
        )
        assert enriched["courseTitle"] == "Physics"

    @pytest.mark.asyncio
    async def test_an_already_titled_course_is_not_refetched(self):
        """The guard that stops a note turn's course being overwritten by the raw `courseId` riding on
        the same context."""

        async def unexpected(_course_id, _user_id):
            raise AssertionError(
                "the course branch must not run when courseTitle is already set"
            )

        enriched = await context_enrichment.enrich_context(
            context={"courseId": "course_1", "courseTitle": "Already known"},
            user_id="user_1",
            readers=fake_readers(find_course=unexpected),
        )
        assert enriched["courseTitle"] == "Already known"


class TestEnrichContextOwnership:
    @pytest.mark.asyncio
    async def test_another_learners_topic_contributes_nothing(self):
        """§5.5.14, now assertable. A refused topic must add no title, no body and no course fields."""
        enriched = await context_enrichment.enrich_context(
            context={"topicId": "someone_elses"},
            user_id="user_1",
            readers=fake_readers(check_topic_ownership=owns(None)),
        )
        assert enriched == {"topicId": "someone_elses"}

    @pytest.mark.asyncio
    async def test_a_refused_topic_keeps_its_id_in_the_context(self):
        """Deliberate: the action service validates the id again before acting on it, so dropping it
        would turn a clean refusal into a tool call against a missing id."""
        enriched = await context_enrichment.enrich_context(
            context={"topicId": "someone_elses"},
            user_id="user_1",
            readers=fake_readers(check_topic_ownership=owns(None)),
        )
        assert enriched["topicId"] == "someone_elses"

    @pytest.mark.asyncio
    async def test_another_learners_course_contributes_nothing(self):
        enriched = await context_enrichment.enrich_context(
            context={"courseId": "theirs"}, user_id="user_1", readers=fake_readers()
        )
        assert "courseTitle" not in enriched

    @pytest.mark.asyncio
    async def test_the_owner_reaches_every_scoped_read(self):
        """A reader that silently ignored its `user_id` would pass every test above. This one checks the
        value actually arrives."""
        seen: list[str] = []

        async def find_note(_note_id, user_id):
            seen.append(user_id)
            return a_note()

        await context_enrichment.enrich_context(
            context={"noteId": "note_1"},
            user_id="user_42",
            readers=fake_readers(find_note=find_note),
        )
        assert seen == ["user_42"]


class TestEnrichContextNoteFallback:
    """A `noteId` that is really a topic id. A real path — clients have sent topic ids in `noteId`."""

    @pytest.mark.asyncio
    async def test_a_note_id_that_is_a_topic_id_adopts_the_latest_note_on_that_topic(
        self,
    ):
        async def find_note(note_id, _user_id):
            return (
                a_note(note_id="note_9", topic_id="topic_1")
                if note_id == "note_9"
                else None
            )

        async def latest(_topic_id, _user_id):
            return SimpleNamespace(id="note_9")

        enriched = await context_enrichment.enrich_context(
            context={"noteId": "topic_1"},
            user_id="user_1",
            readers=fake_readers(
                find_note=find_note,
                latest_note_for_topic=latest,
                check_topic_ownership=owns(a_topic(), a_module(), a_course()),
                find_topic=lambda _tid: _resolved(a_topic()),
                find_module=lambda _mid: _resolved(a_module()),
                find_course=lambda _cid, _uid: _resolved(a_course()),
            ),
        )
        assert (
            enriched["noteId"] == "note_9"
        ), "downstream must agree on which note this turn is about"
        assert enriched["noteTitle"] == "My note"
        assert enriched["topicTitle"] == "Entropy"

    @pytest.mark.asyncio
    async def test_a_topic_with_no_notes_still_contributes_its_own_context(self):
        enriched = await context_enrichment.enrich_context(
            context={"noteId": "topic_1"},
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course())
            ),
        )
        assert enriched["topicTitle"] == "Entropy"
        assert (
            enriched["noteId"] == "topic_1"
        ), "nothing was adopted, so the id is unchanged"

    @pytest.mark.asyncio
    async def test_an_id_that_is_neither_a_note_nor_a_topic_adds_nothing(self):
        enriched = await context_enrichment.enrich_context(
            context={"noteId": "rubbish"}, user_id="user_1", readers=fake_readers()
        )
        assert enriched == {"noteId": "rubbish"}


async def _resolved(value):
    return value


class TestEnrichContextCache:
    @staticmethod
    def cache(store: dict):
        async def get(key):
            return store.get(key)

        async def set_(key, value, ttl):
            store[key] = value
            store["__ttl__"] = ttl

        return context_enrichment.ContextCache(
            make_key=lambda parts: ":".join(parts), get=get, set=set_
        )

    @pytest.mark.asyncio
    async def test_a_hit_skips_the_reads_entirely(self):
        store = {"chat:context:user_1:-:topic_1:-:-": {"topicTitle": "From cache"}}

        async def unexpected(_topic_id, _user_id):
            raise AssertionError("a cache hit must not re-read")

        enriched = await context_enrichment.enrich_context(
            context={"topicId": "topic_1"},
            user_id="user_1",
            readers=fake_readers(check_topic_ownership=unexpected),
            cache=self.cache(store),
        )
        assert enriched["topicTitle"] == "From cache"

    @pytest.mark.asyncio
    async def test_a_miss_fetches_and_writes_the_ttl(self):
        store: dict = {}
        await context_enrichment.enrich_context(
            context={"topicId": "topic_1"},
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course())
            ),
            cache=self.cache(store),
        )
        assert store["__ttl__"] == context_enrichment.CONTEXT_CACHE_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_per_turn_values_are_not_written_to_the_cache(self):
        """`content` is pasted in with the message, so it is not a property of the ids the key is built
        from. Cached, it would be replayed into the next turn on the same topic."""
        store: dict = {}
        await context_enrichment.enrich_context(
            context={"topicId": "topic_1", "content": "pasted this turn"},
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course())
            ),
            cache=self.cache(store),
        )
        written = [v for k, v in store.items() if k != "__ttl__"][0]
        assert "content" not in written
        assert "pageContext" not in written

    @pytest.mark.asyncio
    async def test_a_context_with_no_ids_is_not_cached(self):
        """A key built from four dashes would be one shared entry for every context-free turn."""
        store: dict = {}
        await context_enrichment.enrich_context(
            context={"content": "just some text"},
            user_id="user_1",
            readers=fake_readers(),
            cache=self.cache(store),
        )
        assert store == {}


class TestEnrichContextDirectContent:
    @pytest.mark.asyncio
    async def test_pasted_content_survives_a_cache_hit(self):
        """It is sent with the message, so it must be applied whether or not the fetching ran."""
        store = {"chat:context:user_1:-:topic_1:-:-": {"topicTitle": "From cache"}}
        enriched = await context_enrichment.enrich_context(
            context={"topicId": "topic_1", "content": "pasted"},
            user_id="user_1",
            readers=fake_readers(),
            cache=TestEnrichContextCache.cache(store),
        )
        assert enriched["content"] == "pasted"

    @pytest.mark.asyncio
    async def test_a_fetched_note_body_is_not_overwritten_by_a_supplied_one(self):
        """The note that was actually read wins. Otherwise a client could replace the body of a real
        note with arbitrary text and have the model answer about that instead."""

        async def find_note(_note_id, _user_id):
            return a_note(content="The real body.")

        enriched = await context_enrichment.enrich_context(
            context={"noteId": "note_1", "noteContent": "something else"},
            user_id="user_1",
            readers=fake_readers(find_note=find_note),
        )
        assert enriched["noteContent"] == "The real body."

    @pytest.mark.asyncio
    async def test_a_supplied_note_body_is_used_when_nothing_was_fetched(self):
        enriched = await context_enrichment.enrich_context(
            context={"noteContent": "typed directly"},
            user_id="user_1",
            readers=fake_readers(),
        )
        assert enriched["noteContent"] == "typed directly"


class TestEnrichContextEdges:
    @pytest.mark.asyncio
    async def test_no_context_is_none_not_an_empty_dict(self):
        """The caller distinguishes them: an absent context means the turn carries no page scope, and
        the space-room and reply blocks downstream create a dict only when they have something to add.
        """
        assert (
            await context_enrichment.enrich_context(
                context=None, user_id="user_1", readers=fake_readers()
            )
            is None
        )
        assert (
            await context_enrichment.enrich_context(
                context={}, user_id="user_1", readers=fake_readers()
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_the_callers_context_is_not_mutated(self):
        original = {"topicId": "topic_1"}
        await context_enrichment.enrich_context(
            context=original,
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course())
            ),
        )
        assert original == {"topicId": "topic_1"}

    @pytest.mark.asyncio
    async def test_topic_resources_are_attached_whenever_a_topic_is_in_scope(self):
        seen: list[str] = []

        async def attach(_user_id, topic_id, _context):
            seen.append(topic_id)

        await context_enrichment.enrich_context(
            context={"topicId": "topic_1"},
            user_id="user_1",
            readers=fake_readers(
                check_topic_ownership=owns(a_topic(), a_module(), a_course()),
                attach_topic_resources=attach,
            ),
        )
        assert seen == ["topic_1"]

    @pytest.mark.asyncio
    async def test_a_review_whose_topic_is_gone_contributes_nothing(self):
        """Preserved from the handler and deliberate: `reviewItemId` and `nextReviewAt` without a subject
        would put the model into the spaced-repetition protocol with nothing to ask about.
        """

        async def find_review(_review_id, _user_id):
            return a_review()

        enriched = await context_enrichment.enrich_context(
            context={"reviewItemId": "review_1"},
            user_id="user_1",
            readers=fake_readers(find_review=find_review),
        )
        assert enriched == {"reviewItemId": "review_1"}
        assert "nextReviewAt" not in enriched


# ---------------------------------------------------------------------------
# History: the two isolation rules
# ---------------------------------------------------------------------------
#
# Both rules are about a thread not inheriting messages that were never part of it, and both were
# `conditions.append(...)` lines on an inline query that nothing could reach without a live socket and
# database. What they decide is what the model is told the conversation was.


def history_reader(records=(), calls=None):
    async def read(*, session_id, user_id, review_item_id, limit):
        if calls is not None:
            calls.append(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "review_item_id": review_item_id,
                    "limit": limit,
                }
            )
        return list(records)

    return read


def a_message(content="hello", role="USER"):
    return SimpleNamespace(content=content, role=role, image_urls=[], image_url=None)


class TestHistoryIsolation:
    """The review-thread rule. A second rule — a space room's history is the whole room — was removed
    with space-room chat."""

    @pytest.mark.asyncio
    async def test_a_review_thread_asks_only_for_its_own_review(self):
        """Otherwise a spaced-repetition review answers against the learner's unrelated questions."""
        calls: list[dict] = []
        await context_enrichment.build_history(
            session_id="sess_1",
            user_id="user_1",
            review_item_id="review_7",
            readers=fake_readers(read_history=history_reader(calls=calls)),
        )
        assert calls[0]["review_item_id"] == "review_7"

    @pytest.mark.asyncio
    async def test_general_chat_asks_for_rows_with_no_review(self):
        """`review_item_id=None` means "no review", not "any" — the reader turns it into `IS NULL`. So
        the learner's next general question does not inherit the review they just did.
        """
        calls: list[dict] = []
        await context_enrichment.build_history(
            session_id="sess_1",
            user_id="user_1",
            review_item_id=None,
            readers=fake_readers(read_history=history_reader(calls=calls)),
        )
        assert calls[0]["review_item_id"] is None

    @pytest.mark.asyncio
    async def test_it_asks_only_for_the_learners_own_messages(self):
        """Every conversation on this surface is personal. A space room's history was the whole room,
        passed as `user_id=None`; that went with space-room chat."""
        calls: list[dict] = []
        await context_enrichment.build_history(
            session_id="sess_1",
            user_id="user_1",
            review_item_id=None,
            readers=fake_readers(read_history=history_reader(calls=calls)),
        )
        assert calls[0]["user_id"] == "user_1"

    @pytest.mark.asyncio
    async def test_the_window_is_the_named_limit(self):
        calls: list[dict] = []
        await context_enrichment.build_history(
            session_id="sess_1",
            user_id="user_1",
            review_item_id=None,
            readers=fake_readers(read_history=history_reader(calls=calls)),
        )
        from src.domains.intelligence.conversation import ask_service

        assert calls[0]["limit"] == ask_service.HISTORY_LIMIT

    @pytest.mark.asyncio
    async def test_the_newest_rows_are_reversed_into_oldest_first(self):
        """The query takes the newest rows so a long thread sends the model the *recent* conversation
        rather than its beginning; the provider wants them oldest first. Getting this backwards would
        answer every follow-up against the wrong end of the thread."""
        newest_first = [a_message("third"), a_message("second"), a_message("first")]
        history = await context_enrichment.build_history(
            session_id="sess_1",
            user_id="user_1",
            review_item_id=None,
            readers=fake_readers(read_history=history_reader(records=newest_first)),
        )
        assert [entry["parts"][0] for entry in history] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Recall: retrieval and long-term memory
# ---------------------------------------------------------------------------


def recall_readers(*, retrieve=None, memory=None, calls=None):
    async def default_retrieve(query, user_id, limit):
        if calls is not None:
            calls.append(("retrieve", query, user_id, limit))
        return []

    async def default_memory(user_id, query):
        if calls is not None:
            calls.append(("memory", query, user_id, None))
        return None

    return fake_readers(
        retrieve=retrieve or default_retrieve, memory=memory or default_memory
    )

    @pytest.mark.asyncio
    async def test_retrieved_items_reach_the_context(self):
        async def retrieve(_query, _user_id, _limit):
            return [
                {
                    "similarity": 0.9,
                    "objectType": "note",
                    "objectId": "n1",
                    "data": {"title": "E"},
                }
            ]

        context = await context_enrichment.attach_recall(
            context={},
            message="What did I write about entropy in my notes?",
            user_id="user_1",
            readers=recall_readers(retrieve=retrieve),
        )
        assert any("NOTE" in line for line in context["retrieved_items"])

    @pytest.mark.asyncio
    async def test_a_trivial_message_is_not_searched(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="hi",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        assert not [c for c in calls if c[0] == "retrieve"]

    @pytest.mark.asyncio
    async def test_memory_runs_even_for_a_trivial_message(self):
        """Retrieval is a search that needs something to search on; memory is what Maigie already knows
        about this learner, and "hi" is exactly the turn where knowing them matters."""
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="hi",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        assert [c for c in calls if c[0] == "memory"]

    @pytest.mark.asyncio
    async def test_both_stages_see_the_same_model_facing_text(self):
        """Retrieval used the raw text and memory the mention-stripped text before this — no reachable
        difference, since they differ only in a space room where both are skipped, but it would have
        become one the day space rooms worked."""
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes please",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        queries = {c[1] for c in calls}
        assert queries == {"Explain entropy from my notes please"}

    @pytest.mark.asyncio
    async def test_a_failing_retrieval_does_not_lose_the_turn(self):
        """An enrichment, not a precondition. A turn without recall is a worse answer; a turn that fails
        because recall failed is no answer."""

        async def broken(_query, _user_id, _limit):
            raise RuntimeError("vector store is down")

        async def memory(_user_id, _query):
            return "They are revising thermodynamics."

        context = await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(retrieve=broken, memory=memory),
        )
        assert "retrieved_items" not in context
        assert context["memory_context"] == "They are revising thermodynamics."

    @pytest.mark.asyncio
    async def test_a_failing_memory_lookup_does_not_lose_the_turn(self):
        async def broken(_user_id, _query):
            raise RuntimeError("memory store is down")

        context = await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(memory=broken),
        )
        assert context == {}

    @pytest.mark.asyncio
    async def test_a_context_is_created_when_there_was_none(self):
        """Returned rather than mutated. The handler open-coded `if not context: context = {}` at both
        call sites, and a third stage forgetting it would have raised on `None`."""

        async def memory(_user_id, _query):
            return "Remembered."

        context = await context_enrichment.attach_recall(
            context=None,
            message="hi",
            user_id="user_1",
            readers=recall_readers(memory=memory),
        )
        assert context == {"memory_context": "Remembered."}

    @pytest.mark.asyncio
    async def test_nothing_found_leaves_an_absent_context_absent(self):
        """An empty dict and `None` mean different things downstream, so finding nothing must not
        manufacture a context."""
        assert (
            await context_enrichment.attach_recall(
                context=None,
                message="hi",
                user_id="user_1",
                readers=recall_readers(),
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_the_callers_context_is_not_mutated(self):
        async def memory(_user_id, _query):
            return "Remembered."

        original = {"topicId": "topic_1"}
        await context_enrichment.attach_recall(
            context=original,
            message="hi",
            user_id="user_1",
            readers=recall_readers(memory=memory),
        )
        assert original == {"topicId": "topic_1"}

    @pytest.mark.asyncio
    async def test_the_owner_reaches_both_stages(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_42",
            readers=recall_readers(calls=calls),
        )
        assert {c[2] for c in calls} == {"user_42"}

    @pytest.mark.asyncio
    async def test_the_retrieval_limit_is_the_named_constant(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        retrieve = [c for c in calls if c[0] == "retrieve"][0]
        assert retrieve[3] == context_enrichment.RETRIEVAL_LIMIT


class TestAttachRecall:
    @pytest.mark.asyncio
    async def test_retrieved_items_reach_the_context(self):
        async def retrieve(_query, _user_id, _limit):
            return [
                {
                    "similarity": 0.9,
                    "objectType": "note",
                    "objectId": "n1",
                    "data": {"title": "E"},
                }
            ]

        context = await context_enrichment.attach_recall(
            context={},
            message="What did I write about entropy in my notes?",
            user_id="user_1",
            readers=recall_readers(retrieve=retrieve),
        )
        assert any("NOTE" in line for line in context["retrieved_items"])

    @pytest.mark.asyncio
    async def test_a_trivial_message_is_not_searched(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="hi",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        assert not [c for c in calls if c[0] == "retrieve"]

    @pytest.mark.asyncio
    async def test_memory_runs_even_for_a_trivial_message(self):
        """Retrieval is a search that needs something to search on; memory is what Maigie already knows
        about this learner, and "hi" is exactly the turn where knowing them matters."""
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="hi",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        assert [c for c in calls if c[0] == "memory"]

    @pytest.mark.asyncio
    async def test_both_stages_see_the_same_model_facing_text(self):
        """Retrieval used the raw text and memory the mention-stripped text before this — no reachable
        difference, since they differ only in a space room where both are skipped, but it would have
        become one the day space rooms worked."""
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes please",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        queries = {c[1] for c in calls}
        assert queries == {"Explain entropy from my notes please"}

    @pytest.mark.asyncio
    async def test_a_failing_retrieval_does_not_lose_the_turn(self):
        """An enrichment, not a precondition. A turn without recall is a worse answer; a turn that fails
        because recall failed is no answer."""

        async def broken(_query, _user_id, _limit):
            raise RuntimeError("vector store is down")

        async def memory(_user_id, _query):
            return "They are revising thermodynamics."

        context = await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(retrieve=broken, memory=memory),
        )
        assert "retrieved_items" not in context
        assert context["memory_context"] == "They are revising thermodynamics."

    @pytest.mark.asyncio
    async def test_a_failing_memory_lookup_does_not_lose_the_turn(self):
        async def broken(_user_id, _query):
            raise RuntimeError("memory store is down")

        context = await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(memory=broken),
        )
        assert context == {}

    @pytest.mark.asyncio
    async def test_a_context_is_created_when_there_was_none(self):
        """Returned rather than mutated. The handler open-coded `if not context: context = {}` at both
        call sites, and a third stage forgetting it would have raised on `None`."""

        async def memory(_user_id, _query):
            return "Remembered."

        context = await context_enrichment.attach_recall(
            context=None,
            message="hi",
            user_id="user_1",
            readers=recall_readers(memory=memory),
        )
        assert context == {"memory_context": "Remembered."}

    @pytest.mark.asyncio
    async def test_nothing_found_leaves_an_absent_context_absent(self):
        """An empty dict and `None` mean different things downstream, so finding nothing must not
        manufacture a context."""
        assert (
            await context_enrichment.attach_recall(
                context=None,
                message="hi",
                user_id="user_1",
                readers=recall_readers(),
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_the_callers_context_is_not_mutated(self):
        async def memory(_user_id, _query):
            return "Remembered."

        original = {"topicId": "topic_1"}
        await context_enrichment.attach_recall(
            context=original,
            message="hi",
            user_id="user_1",
            readers=recall_readers(memory=memory),
        )
        assert original == {"topicId": "topic_1"}

    @pytest.mark.asyncio
    async def test_the_owner_reaches_both_stages(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_42",
            readers=recall_readers(calls=calls),
        )
        assert {c[2] for c in calls} == {"user_42"}

    @pytest.mark.asyncio
    async def test_the_retrieval_limit_is_the_named_constant(self):
        calls: list = []
        await context_enrichment.attach_recall(
            context={},
            message="Explain entropy from my notes",
            user_id="user_1",
            readers=recall_readers(calls=calls),
        )
        retrieve = [c for c in calls if c[0] == "retrieve"][0]
        assert retrieve[3] == context_enrichment.RETRIEVAL_LIMIT


class TestExtendedContextAuthorization:
    @pytest.mark.asyncio
    async def test_refused_owned_ids_are_removed_and_never_claim_grounding(self):
        async def learner_context(user_id, raw):
            assert user_id == "user_1"
            assert raw["goalId"] == "foreign_goal"
            return {"rejectedContextIds": ["goalId", "spaceId"]}

        enriched = await context_enrichment.enrich_context(
            context={"goalId": "foreign_goal", "spaceId": "foreign_space"},
            user_id="user_1",
            readers=fake_readers(learner_context=learner_context),
        )
        assert enriched is None

    @pytest.mark.asyncio
    async def test_authorized_extended_context_is_added_as_bounded_structured_data(
        self,
    ):
        async def learner_context(_user_id, _raw):
            return {
                "goal": {"id": "goal_1", "title": "Thermodynamics"},
                "spaceMembershipVerified": True,
                "rejectedContextIds": [],
            }

        enriched = await context_enrichment.enrich_context(
            context={"goalId": "goal_1", "spaceId": "space_1"},
            user_id="user_1",
            readers=fake_readers(learner_context=learner_context),
        )
        assert enriched["goal"]["id"] == "goal_1"
        assert enriched["spaceMembershipVerified"] is True


@pytest.mark.asyncio
async def test_client_cannot_spoof_server_derived_owner_scoped_context():
    async def reject_all(_user_id, _raw):
        return {"rejectedContextIds": ["examPrepId", "spaceId"]}

    enriched = await context_enrichment.enrich_context(
        context={
            "examPrepId": "foreign",
            "examPrep": {"id": "foreign", "subject": "spoofed"},
            "learnerProfile": {"purpose": "spoofed"},
            "spaceId": "foreign",
            "spaceMembershipVerified": True,
            "unsupportedFutureId": "preserved",
        },
        user_id="user_1",
        readers=fake_readers(learner_context=reject_all),
    )
    assert enriched == {"unsupportedFutureId": "preserved"}
