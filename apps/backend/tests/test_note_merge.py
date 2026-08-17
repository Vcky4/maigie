"""Combining several notes on one lesson into one.

A voice session writes one note per sitting, and `Note.topicId` is a plain foreign key, so five short
sittings on a lesson leave five thin notes on it. This is the learner consolidating them.

The tests worth having here are not the happy path. They are the three ways this could quietly harm somebody:
merging notes that are not theirs, losing a note that was named, and destroying the originals.

Fakes rather than a database, following `test_study_voice_notes.py`. What matters is the service's decisions —
which ids it resolves, what it archives, when it charges — and none of that is exercised any better by real
SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import note_merge_service
from src.shared.exceptions import NotFoundError, SubscriptionLimitError, ValidationError


def _note(note_id: str, *, title: str, content: str, minutes_old: int = 0, topic="topic-1", course="course-1", tags=()):
    return SimpleNamespace(
        id=note_id,
        title=title,
        content=content,
        topic_id=topic,
        course_id=course,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_old),
        tags=[SimpleNamespace(tag=t) for t in tags],
    )


@pytest.fixture
def world(monkeypatch):
    world = SimpleNamespace(
        notes={
            "n1": _note("n1", title="First sitting", content="- Anchored by the base case.", minutes_old=30),
            "n2": _note("n2", title="Second sitting", content="- The step carries it.", minutes_old=20),
            "n3": _note("n3", title="Third sitting", content="- My own hand-written line.", minutes_old=10),
        },
        response="TITLE: Induction, whole\nCONTENT: - Anchored by the base case.\n- The step carries it.",
        created=[],
        archived=[],
        charged=[],
        available=(True, None),
        prompt="",
        archive_raises=None,
    )

    async def get_note(*, user_id, note_id):
        note = world.notes.get(note_id)
        if note is None:
            raise NotFoundError("Note", note_id)
        return note

    async def create_note(*, user_id, data):
        world.created.append(data)
        return SimpleNamespace(id="merged-1", **{k: v for k, v in data.items() if k != "tags"})

    async def update_note(note_id, data):
        if world.archive_raises:
            raise world.archive_raises
        world.archived.append((note_id, data))

    async def generate_content(prompt, **kwargs):
        world.prompt = prompt
        return world.response

    async def check(user_, cost, **kwargs):
        return world.available

    async def consume(user_, cost, operation="unknown", **kwargs):
        world.charged.append((cost, operation))

    monkeypatch.setattr(note_merge_service.note_service, "get_note", get_note)
    monkeypatch.setattr(note_merge_service.note_service, "create_note", create_note)
    monkeypatch.setattr(note_merge_service.repo, "update_note", update_note)
    monkeypatch.setattr(note_merge_service, "generate_content", generate_content)
    monkeypatch.setattr(note_merge_service, "check_credit_availability", check)
    monkeypatch.setattr(note_merge_service, "consume_credits", consume)
    return world


@pytest.fixture
def user():
    return SimpleNamespace(id="user-1", tier="PLUS")


# ---------------------------------------------------------------------------
# What it produces
# ---------------------------------------------------------------------------


async def test_notes_are_combined_into_one(world, user):
    merged = await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert merged.id == "merged-1"
    assert world.created[0]["title"] == "Induction, whole"
    # The lesson survives, which is the point: the merged note has to be findable from the topic its parts
    # were about.
    assert world.created[0]["topicId"] == "topic-1"
    assert world.created[0]["courseId"] == "course-1"


async def test_every_note_reaches_the_prompt(world, user):
    """The failure this guards is silent loss: a note named and then not read.

    Some of these are the learner's own writing, and the merged note replaces them — so anything missing from
    the prompt is gone from their library.
    """
    await note_merge_service.merge_notes(user, note_ids=["n1", "n2", "n3"])

    assert "Anchored by the base case." in world.prompt
    assert "The step carries it." in world.prompt
    assert "My own hand-written line." in world.prompt
    # And the instruction is to keep, not to summarise — a merge that tidies is the same loss as an overwrite.
    assert "Keep every distinct point" in world.prompt


async def test_notes_are_ordered_oldest_first(world, user):
    """So the combined note reads as understanding developing forwards.

    The client lists notes newest first, which is right for choosing them and wrong for reading them: the
    prompt asks the model to say which of two conflicting points is later, and that only means something if
    the order is known.
    """
    await note_merge_service.merge_notes(user, note_ids=["n3", "n1", "n2"])

    first = world.prompt.index("First sitting")
    second = world.prompt.index("Second sitting")
    third = world.prompt.index("Third sitting")
    assert first < second < third


async def test_tags_from_every_note_are_carried(world, user):
    world.notes["n1"] = _note("n1", title="A", content="a", minutes_old=2, tags=("induction", "proofs"))
    world.notes["n2"] = _note("n2", title="B", content="b", minutes_old=1, tags=("proofs", "revision"))

    await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    # Deduplicated and sorted, so the merged note is findable by anything its parts were.
    assert world.created[0]["tags"] == ["induction", "proofs", "revision"]


async def test_notes_from_two_topics_belong_to_neither(world, user):
    """Allowed, but not filed under whichever happened to be first.

    Picking one would put a note about two lessons on one of them, which is a quiet lie about what it covers.
    """
    world.notes["n2"] = _note("n2", title="Elsewhere", content="b", minutes_old=1, topic="topic-2", course="course-2")

    await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.created[0]["topicId"] is None
    assert world.created[0]["courseId"] is None


# ---------------------------------------------------------------------------
# The originals
# ---------------------------------------------------------------------------


async def test_the_originals_are_archived_not_deleted(world, user):
    """A merge is one model call reading several inputs, so it can drop something.

    "Your five notes are now one and the five are gone" is not a claim worth making without a way back.
    Deleting would also destroy their attachments and version history, which the merged note cannot carry.
    """
    await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.archived == [("n1", {"archived": True}), ("n2", {"archived": True})]


async def test_a_note_that_will_not_archive_does_not_fail_the_merge(world, user):
    """The merged note exists and is what the learner asked for.

    A note that failed to archive is visible clutter, not lost work, so it is logged rather than raised.
    """
    world.archive_raises = RuntimeError("database hiccup")

    merged = await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])
    assert merged.id == "merged-1"


async def test_nothing_is_archived_before_the_merged_note_exists(world, user, monkeypatch):
    """Ordering. The other way round risks hidden notes and nothing to show for them."""

    async def explode(prompt, **kwargs):
        raise RuntimeError("the model is on fire")

    monkeypatch.setattr(note_merge_service, "generate_content", explode)

    with pytest.raises(RuntimeError):
        await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.archived == []
    assert world.created == []


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


async def test_another_learners_note_fails_the_whole_request(world, user):
    """Not skipped. Combining two notes when three were named and reporting success is accept-and-discard.

    `get_note` is scoped to the owner, so an id belonging to somebody else raises — and that raise is allowed
    to propagate rather than being caught and the id dropped.
    """
    with pytest.raises(NotFoundError):
        await note_merge_service.merge_notes(user, note_ids=["n1", "n2", "not-mine"])

    assert world.created == []
    assert world.archived == []
    assert world.charged == []


async def test_fewer_than_two_notes_is_refused(world, user):
    with pytest.raises(ValidationError):
        await note_merge_service.merge_notes(user, note_ids=["n1"])


async def test_a_repeated_id_is_refused_rather_than_deduplicated(world, user):
    """A repeated id means the caller built its list wrongly.

    Quietly merging two notes when three were named is the same silent-success this codebase guards against
    elsewhere, so it is refused with something the client can act on.
    """
    with pytest.raises(ValidationError):
        await note_merge_service.merge_notes(user, note_ids=["n1", "n2", "n1"])

    assert world.created == []


async def test_more_than_the_maximum_is_refused_rather_than_truncated(world, user):
    """Truncating would drop notes the learner named. The message says to batch instead."""
    many = [f"id-{i}" for i in range(note_merge_service.MAX_NOTES + 1)]
    with pytest.raises(ValidationError):
        await note_merge_service.merge_notes(user, note_ids=many)

    assert world.created == []


async def test_notes_with_no_content_are_refused(world, user):
    world.notes["n1"] = _note("n1", title="Empty", content="", minutes_old=2)
    world.notes["n2"] = _note("n2", title="Also empty", content="   ", minutes_old=1)

    with pytest.raises(ValidationError):
        await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.charged == []


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


async def test_it_is_charged_once_regardless_of_how_many_notes(world, user):
    """Pricing per input would make consolidating a messy topic more expensive the messier it got.

    The inputs were already paid for when they were written.
    """
    await note_merge_service.merge_notes(user, note_ids=["n1", "n2", "n3"])
    assert world.charged == [(100, "note_merge")]


async def test_being_unable_to_pay_refuses_before_the_model_is_called(world, user):
    world.available = (False, "Out of credits")

    with pytest.raises(SubscriptionLimitError):
        await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.prompt == ""
    assert world.created == []


async def test_a_completed_merge_is_handed_over_even_if_billing_fails(world, user, monkeypatch):
    """The work is done and the learner can see it. Same decision as the session note."""

    async def consume(*args, **kwargs):
        raise SubscriptionLimitError()

    monkeypatch.setattr(note_merge_service, "consume_credits", consume)

    merged = await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])
    assert merged.id == "merged-1"


async def test_a_model_that_ignores_the_format_still_produces_a_note(world, user):
    """Its answer is still about these notes, and this replaces them.

    Discarding the one thing that read all of them would be worse than a badly titled note.
    """
    world.response = "- base case\n- step"

    await note_merge_service.merge_notes(user, note_ids=["n1", "n2"])

    assert world.created[0]["content"] == "- base case\n- step"
    # Falls back to the oldest note's title rather than inventing one.
    assert world.created[0]["title"] == "First sitting"
