"""The transcript buffer, and the note the learner asks for.

Two properties matter more than anything else here and both are about what does *not* happen:

- the buffer is memory only, bounded, and cleared, and
- `save_session_note` is never called on its own behalf — there is no timer, no turn counter, no end-of-session
  hook. The deleted implementation had all three, and wrote a learner's spoken mistakes into their own notes
  every six fragments without asking.

The rest of the assertions protect the parts that would otherwise silently produce a bad note: fragmented
transcription arriving mid-sentence, a model that ignores the output format, and a learner pressing save twice.
"""

from types import SimpleNamespace

import pytest

from src.domains.study_voice import notes
from src.domains.study_voice import transcript as transcript_module
from src.domains.study_voice.session_store import VoiceSession
from src.domains.study_voice.transcript import SessionTranscript
from src.shared.exceptions import NotFoundError, SubscriptionLimitError, ValidationError

# ---------------------------------------------------------------------------
# The buffer
# ---------------------------------------------------------------------------


def test_fragments_from_the_same_speaker_become_one_turn():
    """Transcription arrives in pieces. Counting each piece as a turn is what made the original's
    'every six turns' fire in the middle of a sentence."""
    t = SessionTranscript()
    t.add("user", "I think")
    t.add("user", "the answer is")
    t.add("user", "reciprocal")
    assert t.turn_count == 1
    assert t.render() == "USER: I think the answer is reciprocal"


def test_a_speaker_change_starts_a_new_turn():
    t = SessionTranscript()
    t.add("user", "Why does that hold?")
    t.add("assistant", "Because the base case is true.")
    t.add("user", "Got it.")
    assert t.turn_count == 3


def test_a_resent_transcription_tail_is_not_duplicated():
    t = SessionTranscript()
    t.add("assistant", "Because the base case is true.")
    t.add("assistant", "Because the base case is true.")
    assert t.render() == "ASSISTANT: Because the base case is true."


def test_blank_fragments_are_ignored():
    t = SessionTranscript()
    t.add("user", "   ")
    t.add("user", "")
    assert t.turn_count == 0


def test_the_buffer_is_bounded_by_turn_count():
    t = SessionTranscript()
    for i in range(transcript_module.MAX_TURNS + 50):
        t.add("user" if i % 2 == 0 else "assistant", f"line {i}")
    assert t.turn_count == transcript_module.MAX_TURNS
    # The oldest go first, so what survives is what a note would be written from anyway.
    assert "line 0" not in t.render()


def test_the_buffer_is_bounded_by_size():
    t = SessionTranscript()
    for i in range(400):
        t.add("user" if i % 2 == 0 else "assistant", "x" * 500)
    assert sum(len(turn.text) for turn in t.turns) <= transcript_module.MAX_CHARS


def test_render_keeps_the_most_recent_when_it_has_to_cut():
    t = SessionTranscript()
    t.add("user", "the oldest thing said")
    t.add("assistant", "the newest thing said")
    assert t.render(limit=25).endswith("the newest thing said")


def test_clearing_leaves_nothing_behind():
    t = SessionTranscript()
    t.add("user", "something private")
    t.clear()
    assert t.turn_count == 0
    assert t.render() == ""


def test_a_short_exchange_is_not_enough_for_a_note():
    t = SessionTranscript()
    t.add("user", "hello")
    t.add("assistant", "hello, shall we start?")
    assert t.has_enough_for_a_note() is False


def test_two_full_exchanges_are_enough_for_a_note():
    t = SessionTranscript()
    for i in range(transcript_module.MIN_TURNS_FOR_NOTE):
        t.add("user" if i % 2 == 0 else "assistant", f"turn {i}")
    assert t.has_enough_for_a_note() is True


# ---------------------------------------------------------------------------
# The note
# ---------------------------------------------------------------------------


@pytest.fixture
def conversation():
    t = SessionTranscript()
    t.add("user", "I still do not see why induction proves it for every n.")
    t.add("assistant", "Because the step carries the truth from one n to the next.")
    t.add("user", "So the base case is the anchor?")
    t.add("assistant", "Exactly — the anchor plus the step covers all of them.")
    return t


@pytest.fixture
def session():
    return VoiceSession(
        session_id="sess-1",
        user_id="user-1",
        system_instruction="brief",
        course_id="course-1",
        topic_id="topic-1",
    )


@pytest.fixture
def user():
    return SimpleNamespace(id="user-1", tier="PLUS")


@pytest.fixture
def note_world(monkeypatch):
    """Fakes the model, the note store and billing, and records what each was asked to do."""
    world = SimpleNamespace(
        response="TITLE: Induction, anchored\nCONTENT: - The base case anchors it.\n- The step carries it.",
        created=[],
        updated=[],
        charged=[],
        remembered=[],
        available=(True, None),
        update_raises=None,
        note_id_counter=0,
    )

    async def generate_content(prompt, **kwargs):
        world.prompt = prompt
        return world.response

    async def create_note(*, user_id, data):
        world.note_id_counter += 1
        note = SimpleNamespace(
            id=f"note-{world.note_id_counter}", title=data["title"], content=data["content"]
        )
        world.created.append(data)
        return note

    async def update_note(*, user_id, note_id, data):
        if world.update_raises:
            raise world.update_raises
        world.updated.append((note_id, data))
        return SimpleNamespace(id=note_id, title=data["title"], content=data["content"])

    async def check(user_, cost, **kwargs):
        return world.available

    async def consume(user_, cost, operation="unknown", **kwargs):
        world.charged.append((cost, operation))
        return None

    async def remember(session_id, note_id):
        world.remembered.append((session_id, note_id))

    monkeypatch.setattr(notes, "generate_content", generate_content)
    monkeypatch.setattr(notes.note_service, "create_note", create_note)
    monkeypatch.setattr(notes.note_service, "update_note", update_note)
    monkeypatch.setattr(notes, "check_credit_availability", check)
    monkeypatch.setattr(notes, "consume_credits", consume)
    monkeypatch.setattr(notes.session_store, "remember_note", remember)
    return world


@pytest.mark.asyncio
async def test_a_note_is_written_from_the_conversation(note_world, user, session, conversation):
    result = await notes.save_session_note(user, session, conversation)

    assert result["title"] == "Induction, anchored"
    assert "base case anchors it" in str(result["content"])
    assert result["created"] is True
    assert note_world.created[0]["topicId"] == "topic-1"
    assert note_world.created[0]["courseId"] == "course-1"


@pytest.mark.asyncio
async def test_the_transcript_reaches_the_model_and_nothing_else_does(
    note_world, user, session, conversation
):
    await notes.save_session_note(user, session, conversation)
    assert "I still do not see why induction" in note_world.prompt


@pytest.mark.asyncio
async def test_a_thin_conversation_is_refused_with_a_reason(note_world, user, session):
    t = SessionTranscript()
    t.add("user", "hi")
    with pytest.raises(ValidationError):
        await notes.save_session_note(user, session, t)
    assert note_world.created == []


@pytest.mark.asyncio
async def test_a_session_with_no_topic_cannot_produce_a_note(note_world, user, conversation):
    homeless = VoiceSession(session_id="s", user_id="user-1", system_instruction="b", topic_id=None)
    with pytest.raises(ValidationError):
        await notes.save_session_note(user, homeless, conversation)


@pytest.mark.asyncio
async def test_nothing_is_generated_when_the_learner_cannot_pay(
    note_world, user, session, conversation
):
    note_world.available = (False, "Out of credits.")
    with pytest.raises(SubscriptionLimitError):
        await notes.save_session_note(user, session, conversation)
    assert note_world.created == []
    assert not hasattr(note_world, "prompt")


@pytest.mark.asyncio
async def test_the_charge_happens_after_the_note_exists(note_world, user, session, conversation):
    """A generation that failed is not something to bill for."""
    await notes.save_session_note(user, session, conversation)
    assert note_world.charged == [(100, "voice_session_note")]


@pytest.mark.asyncio
async def test_saving_twice_rewrites_the_same_note(note_world, user, session, conversation):
    """One sitting, one note. Two would be two accounts of the same conversation."""
    session.note_id = "note-1"
    result = await notes.save_session_note(user, session, conversation)

    assert result["created"] is False
    assert note_world.updated[0][0] == "note-1"
    assert note_world.created == []


@pytest.mark.asyncio
async def test_a_deleted_note_is_rewritten_rather_than_reported_as_an_error(
    note_world, user, session, conversation
):
    session.note_id = "note-gone"
    note_world.update_raises = NotFoundError("Note", "note-gone")

    result = await notes.save_session_note(user, session, conversation)
    assert result["created"] is True
    assert note_world.remembered == [("sess-1", "note-1")]


@pytest.mark.asyncio
async def test_a_model_that_ignores_the_format_still_produces_the_learners_note(
    note_world, user, session, conversation
):
    note_world.response = "Induction works because the step carries the base case forward."
    result = await notes.save_session_note(user, session, conversation)
    assert result["title"] == "Voice study session"
    assert "step carries the base case" in str(result["content"])


@pytest.mark.asyncio
async def test_an_empty_model_reply_is_reported_not_saved(note_world, user, session, conversation):
    note_world.response = "   "
    with pytest.raises(ValidationError):
        await notes.save_session_note(user, session, conversation)
    assert note_world.created == []


@pytest.mark.asyncio
async def test_a_multiline_body_survives_parsing(note_world, user, session, conversation):
    note_world.response = "TITLE: Induction\nCONTENT:\n- first point\n- second point\n\n- third"
    result = await notes.save_session_note(user, session, conversation)
    assert str(result["content"]) == "- first point\n- second point\n\n- third"


@pytest.mark.asyncio
async def test_a_written_note_is_handed_over_even_if_the_charge_fails(
    note_world, user, session, conversation, monkeypatch
):
    """Refusing to show the learner a note they can already be charged for would be worse than absorbing
    the cost of one generation."""

    async def consume(*args, **kwargs):
        raise SubscriptionLimitError()

    monkeypatch.setattr(notes, "consume_credits", consume)
    result = await notes.save_session_note(user, session, conversation)
    assert result["note_id"] == "note-1"
