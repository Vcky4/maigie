"""The tutor brief.

The brief is the only thing the model is told, and it cannot be replaced once the provider socket is open, so
everything worth knowing has to be in it and nothing dangerous can be. Two assertions here are the reason the
file exists:

- the knowledge check's **question** reaches the tutor and its **answer** never does, and
- a learner who got that check wrong is flagged, because the attempt recording built for the lesson reader was
  built for exactly this.

Everything is faked at the repository boundary. These are assertions about what goes into a prompt, and a
database would only make them slower.
"""

from types import SimpleNamespace

import pytest

from src.domains.study_voice import context

CHECK = {
    "question": "What does an engagement cue signal?",
    "explanation": "Engagement cues signal readiness to interact.",
    "choices": [
        {"id": "a", "label": "Readiness to interact", "correct": True},
        {"id": "b", "label": "Hunger", "correct": False},
    ],
}


def _topic(**overrides):
    base = {
        "id": "topic-1",
        "title": "Understanding newborn behaviour",
        "summary": "How newborns signal what they need.",
        "objectives": ["Read an engagement cue", "Tell hunger from overstimulation"],
        "knowledge_check": CHECK,
        "module_id": "module-1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _course(**overrides):
    base = {
        "id": "course-1",
        "title": "Newborn care",
        "teaching_style": "Visual",
        "user_id": "user-1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _section(title, *, completed=False, summary=None, key_idea=None):
    return SimpleNamespace(title=title, completed=completed, summary=summary, key_idea=key_idea)


def _attempt(correct: bool):
    return SimpleNamespace(correct=correct, choice_id="b", created_at=None)


def _resource(title, *, source=None, meta=None, rtype="ARTICLE", url="", description=""):
    return SimpleNamespace(
        title=title,
        type=rtype,
        url=url,
        description=description,
        recommendation_source=source,
        metadata_json=meta,
    )


@pytest.fixture
def brief_world(monkeypatch):
    """A topic that exists, with nothing attached to it. Individual tests add what they need."""
    world = SimpleNamespace(
        topic=_topic(),
        course=_course(),
        sections=[],
        attempts=[],
        notes=[],
        resources=[],
    )

    async def ownership(topic_id, user_id):
        return world.topic, SimpleNamespace(id="module-1", course_id="course-1"), world.course

    async def sections(topic_id):
        return world.sections

    async def attempts(topic_id, user_id):
        return world.attempts

    async def notes(user_id, *, where, take=20, session=None):
        return world.notes, len(world.notes)

    async def resources(*, where, skip=0, take=20, order=None):
        return world.resources, len(world.resources)

    monkeypatch.setattr(context, "check_topic_ownership", ownership)
    monkeypatch.setattr(context.knowledge_repo, "list_topic_sections", sections)
    monkeypatch.setattr(context.knowledge_repo, "list_topic_check_attempts", attempts)
    monkeypatch.setattr(context.knowledge_repo, "list_resources", resources)
    monkeypatch.setattr(context.personal_learning_repo, "list_notes", notes)
    return world


# ---------------------------------------------------------------------------
# The answer key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_check_question_reaches_the_tutor(brief_world):
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "What does an engagement cue signal?" in brief


@pytest.mark.asyncio
async def test_the_correct_answer_never_reaches_the_tutor(brief_world):
    """A tutor holding the key gives it away, and the check is the one place understanding is measured."""
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "Readiness to interact" not in brief
    assert "Hunger" not in brief
    assert CHECK["explanation"] not in brief


# ---------------------------------------------------------------------------
# What the learner has already struggled with
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_learner_who_got_the_check_wrong_is_flagged(brief_world):
    brief_world.attempts = [_attempt(False), _attempt(True)]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "got it wrong" in brief
    assert "This is the part to spend the session on" in brief


@pytest.mark.asyncio
async def test_passing_later_does_not_erase_having_failed(brief_world):
    """The whole point of keeping every attempt: a corrected answer is not the same as having known it."""
    brief_world.attempts = [_attempt(False), _attempt(True)]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "reach the right answer eventually" in brief


@pytest.mark.asyncio
async def test_a_first_time_pass_is_reported_as_one(brief_world):
    brief_world.attempts = [_attempt(True)]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "correctly first time" in brief


@pytest.mark.asyncio
async def test_an_unattempted_check_says_so(brief_world):
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "have not attempted it yet" in brief


# ---------------------------------------------------------------------------
# Lesson material
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_lesson_outline_marks_what_has_already_been_read(brief_world):
    brief_world.sections = [
        _section("Why newborns cry", completed=True, summary="The four reasons"),
        _section("Reading cues", completed=False),
    ]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "[read] Why newborns cry — The four reasons" in brief
    assert "[not yet read] Reading cues" in brief


@pytest.mark.asyncio
async def test_objectives_and_teaching_style_are_included(brief_world):
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "- Read an engagement cue" in brief
    assert "Visual" in brief
    assert "Lead with concrete imagery" in brief


@pytest.mark.asyncio
async def test_a_course_with_no_teaching_style_gets_no_style_instruction(brief_world):
    brief_world.course = _course(teaching_style=None)
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "asked to be taught" not in brief


@pytest.mark.asyncio
async def test_notes_are_read_in_the_order_they_were_written(brief_world):
    """`list_notes` returns newest first; the tutor should see the learner's thinking develop forwards."""
    brief_world.notes = [
        SimpleNamespace(content="Second thought"),
        SimpleNamespace(content="First thought"),
    ]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert brief.index("First thought") < brief.index("Second thought")


@pytest.mark.asyncio
async def test_learner_supplied_resources_are_separated_from_ai_suggestions(brief_world):
    brief_world.resources = [
        _resource("Uploaded handout"),
        _resource("Suggested video", source="ai"),
        _resource("Studio pick", meta={"studioAiRecommendation": True}),
    ]
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "prioritise them for grounding" in brief
    assert brief.index("Uploaded handout") < brief.index("Suggested video")
    assert "suggestions rather than source material" in brief
    assert "Studio pick" in brief


# ---------------------------------------------------------------------------
# Degenerate cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_with_no_topic_still_gets_a_usable_instruction(brief_world):
    brief = await context.build_brief("user-1", course_id=None, topic_id=None)
    assert brief.startswith(context.BASE_INSTRUCTION)
    assert "ask what they want to work on" in brief


@pytest.mark.asyncio
async def test_a_topic_with_no_check_produces_no_check_section(brief_world):
    brief_world.topic = _topic(knowledge_check=None)
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "on their own" not in brief


@pytest.mark.asyncio
async def test_the_brief_survives_a_repository_failure(brief_world, monkeypatch):
    """A lesson with unreadable sections should still get a tutor, not a 500 on pressing Study."""

    async def boom(topic_id):
        raise RuntimeError("database is having a moment")

    monkeypatch.setattr(context.knowledge_repo, "list_topic_sections", boom)
    brief = await context.build_brief("user-1", course_id="course-1", topic_id="topic-1")
    assert "Understanding newborn behaviour" in brief
