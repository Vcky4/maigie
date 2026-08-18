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
        #: What `get_note` returns, so a revision can be given something to revise. `None` means the note is
        #: gone, which is the "learner deleted it between saves" path.
        existing_note=None,
        get_raises=None,
        #: Every prompt sent, in order. A revision run has two generations across two calls, and asserting
        #: on a single `prompt` attribute would only ever see the last one.
        prompts=[],
    )

    async def generate_content(prompt, **kwargs):
        world.prompt = prompt
        world.prompts.append(prompt)
        return world.response

    async def get_note(*, user_id, note_id):
        if world.get_raises:
            raise world.get_raises
        if world.existing_note is None:
            raise NotFoundError("Note", note_id)
        return world.existing_note

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

    async def remember(session_id, note_id, *, turns=0):
        world.remembered.append((session_id, note_id, turns))

    monkeypatch.setattr(notes, "generate_content", generate_content)
    monkeypatch.setattr(notes.note_service, "create_note", create_note)
    monkeypatch.setattr(notes.note_service, "get_note", get_note)
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
async def test_saving_twice_revises_the_same_note(note_world, user, session, conversation):
    """One sitting, one note. Two would be two accounts of the same conversation."""
    session.note_id = "note-1"
    note_world.existing_note = SimpleNamespace(
        id="note-1", title="Induction", content="- The base case anchors it."
    )
    result = await notes.save_session_note(user, session, conversation)

    assert result["created"] is False
    assert note_world.updated[0][0] == "note-1"
    assert note_world.created == []


@pytest.mark.asyncio
async def test_a_revision_is_given_the_existing_note_to_keep(
    note_world, user, session, conversation
):
    """The defect this fixes: the second write used to see only the transcript and overwrite the note.

    Two things were lost by that. The learner's own edits, and — the one that makes this necessary — the
    early part of a long session, which has aged out of the bounded transcript buffer and cannot be
    regenerated from it. So the note has to be an input, not just an output.
    """
    session.note_id = "note-1"
    note_world.existing_note = SimpleNamespace(
        id="note-1",
        title="Induction",
        content="- The base case anchors it.\n- A learner's own hand-written line.",
    )

    await notes.save_session_note(user, session, conversation)

    prompt = note_world.prompts[-1]
    assert "A learner's own hand-written line." in prompt
    assert "Induction" in prompt
    # And the instruction is to preserve rather than rewrite, since a free rewrite on every pass drifts.
    assert "Keep the existing note" in prompt
    # The conversation is still there too — a revision needs both halves.
    assert "induction proves it" in prompt


@pytest.mark.asyncio
async def test_a_note_already_up_to_date_is_not_regenerated(
    note_world, user, session, conversation
):
    """Nothing said since the last note means nothing to add, so no model call and no charge.

    This matters most on the teardown path, which fires whether or not the learner kept talking after
    writing a note by hand. Without the marker it would re-run over identical material and bill for the note
    already on screen.
    """
    session.note_id = "note-1"
    session.turns_at_last_note = conversation.turn_count
    note_world.existing_note = SimpleNamespace(id="note-1", title="Induction", content="- Anchored.")

    with pytest.raises(ValidationError):
        await notes.save_session_note(user, session, conversation)

    assert note_world.prompts == []
    assert note_world.charged == []
    assert note_world.updated == []


@pytest.mark.asyncio
async def test_a_deleted_note_is_rewritten_rather_than_reported_as_an_error(
    note_world, user, session, conversation
):
    """The learner tidied the note away between saves. Not an error, so write a fresh one.

    Detected on the read now rather than on the write, because a revision has to fetch what it is revising —
    which means the absence surfaces one step earlier than it used to.
    """
    session.note_id = "note-gone"
    note_world.existing_note = None

    result = await notes.save_session_note(user, session, conversation)
    assert result["created"] is True
    assert note_world.remembered == [("sess-1", "note-1", 4)]
    # A fresh note, so the first-write prompt rather than the revision one.
    assert "Keep the existing note" not in note_world.prompts[-1]


@pytest.mark.asyncio
async def test_the_note_marker_records_the_conversation_length(
    note_world, user, session, conversation
):
    """What the up-to-date check above reads. Recorded on creation and on revision alike."""
    await notes.save_session_note(user, session, conversation)
    assert note_world.remembered == [("sess-1", "note-1", conversation.turn_count)]


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


# ---------------------------------------------------------------------------
# The note written at the end of the session
# ---------------------------------------------------------------------------
#
# This is the normal path. The learner switches note-taking on, talks, and one note is written when the
# sitting ends — rather than a note every few minutes, which at 100 credits a pass would charge a
# forty-minute session eight times over for one note.
#
# It runs from the relay's teardown, on the hook credit settlement already uses, so what matters most about
# it is what happens when it goes wrong: an exception escaping here rides up through a `finally` whose other
# job is charging for the session.


@pytest.fixture
def finalise_world(note_world, monkeypatch):
    """`note_world`, plus a fake session store so `finalise_session_note` can look a session up."""
    note_world.stored_session = None

    async def get(session_id):
        return note_world.stored_session

    monkeypatch.setattr(notes.session_store, "get", get)
    return note_world


@pytest.mark.asyncio
async def test_nothing_is_written_when_note_taking_was_never_switched_on(
    finalise_world, user, session, conversation
):
    """The consent gate, and the whole reason a toggle exists.

    With this off the transcript is still buffered — the session needs it to run — and nothing is ever
    written from it. This is the test that stops this feature from becoming the unasked-for automatic writer
    the module was built to remove.
    """
    session.note_taking = False
    finalise_world.stored_session = session

    assert await notes.finalise_session_note(user, session, conversation) is None
    assert finalise_world.prompts == []
    assert finalise_world.created == []
    assert finalise_world.charged == []


@pytest.mark.asyncio
async def test_a_note_is_written_at_teardown_when_note_taking_is_on(
    finalise_world, user, session, conversation
):
    session.note_taking = True
    finalise_world.stored_session = session

    saved = await notes.finalise_session_note(user, session, conversation)

    assert saved is not None
    assert saved["note_id"] == "note-1"
    # Tied to the lesson, which is the point of writing it at all — it has to be findable from the topic.
    assert finalise_world.created[0]["topicId"] == "topic-1"
    assert finalise_world.created[0]["courseId"] == "course-1"


@pytest.mark.asyncio
async def test_the_note_is_written_even_though_the_record_is_already_deleted(
    finalise_world, user, session, conversation
):
    """The reported bug, and the reason this function no longer looks the session up.

    Two sessions were reported where note-taking was switched on and no note appeared. The cause was an
    ordering race the client creates on every exit: `stopConversation` closes the socket and *immediately*
    fires `POST /conversation/{id}/stop`, which **deletes the session record**. Teardown then read the record
    by id, got `None`, and returned silently — no note, no error, nothing in the log a learner could see.

    `stored_session = None` here is that state: the record is gone. The note must still be written, because
    the socket held its own copy and passed it in.
    """
    session.note_taking = True
    finalise_world.stored_session = None

    saved = await notes.finalise_session_note(user, session, conversation)

    assert saved is not None
    assert finalise_world.created[0]["topicId"] == "topic-1"


@pytest.mark.asyncio
async def test_the_session_is_never_looked_up_by_id(
    finalise_world, user, session, conversation, monkeypatch
):
    """Guards the fix at the mechanism rather than the symptom.

    A later refactor that reintroduced a lookup would pass the test above whenever the record happened to
    still exist — which is most of the time in a test and rarely at the moment a real session ends.
    """
    session.note_taking = True
    looked_up: list[str] = []

    async def get(session_id):
        looked_up.append(session_id)
        return finalise_world.stored_session

    monkeypatch.setattr(notes.session_store, "get", get)

    await notes.finalise_session_note(user, session, conversation)

    assert looked_up == [], "teardown must not depend on the stored record"


@pytest.mark.asyncio
async def test_another_learners_session_is_refused(finalise_world, user, conversation):
    """Cannot happen through the socket, which checks ownership before starting.

    Guarded anyway, because this writes into somebody's note library and the check costs one comparison.
    """
    theirs = VoiceSession(
        session_id="sess-1",
        user_id="someone-else",
        system_instruction="brief",
        topic_id="topic-1",
        note_taking=True,
    )
    assert await notes.finalise_session_note(user, theirs, conversation) is None
    assert finalise_world.created == []


@pytest.mark.asyncio
async def test_a_conversation_too_short_to_write_about_ends_quietly(
    finalise_world, user, session
):
    """An ordinary ending, not a fault: the learner switched note-taking on and then said very little."""
    session.note_taking = True
    finalise_world.stored_session = session

    thin = SessionTranscript()
    thin.add("user", "hello")

    assert await notes.finalise_session_note(user, session, thin) is None


@pytest.mark.asyncio
async def test_teardown_never_raises_whatever_happens(
    finalise_world, user, session, conversation, monkeypatch
):
    """The one property this function must have.

    It runs beside the credit settlement in the same teardown. An exception escaping would ride up through a
    `finally` whose other job is charging for the session, so a model outage could turn into a lost
    settlement — or, put the other way, crashing would become the cheapest way to study.
    """
    session.note_taking = True
    finalise_world.stored_session = session

    async def explode(*args, **kwargs):
        raise RuntimeError("the model is on fire")

    monkeypatch.setattr(notes, "generate_content", explode)

    assert await notes.finalise_session_note(user, session, conversation) is None


@pytest.mark.asyncio
async def test_running_out_of_credits_at_teardown_is_not_an_error(
    finalise_world, user, session, conversation
):
    """The learner spent their allowance during the session. Logged, not raised — see above."""
    session.note_taking = True
    finalise_world.stored_session = session
    finalise_world.available = (False, "Out of credits")

    assert await notes.finalise_session_note(user, session, conversation) is None
