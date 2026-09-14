"""The Learn path builds a course, and the document a learner uploads shapes it.

Three defects sat behind this, all in the flow a new learner is most likely to pick:

1. **"Learn" produced a preparation, not a course.** `skill_building` mapped to an `ExamPrep`
   of type `PROJECT` with a target date thirty days out that nobody had asked for, and the
   learner was dropped on `/prepare`. No branch of onboarding called `create_course`, so the
   one option on the picker naming a surface delivered a different one.

2. **A learner who named a skill and no subjects got nothing at all.** The mobile details
   screen requires a skill name and treats subjects as optional; `auto_setup_for_learner`
   required `subjects` and returned `skipped` without it. Nothing was created,
   `onboardingState` never reached `content_ready`, and the progress screen polled a status
   that could not change. A hang, not a cosmetic gap.

3. **An uploaded syllabus could not influence anything.** Course material was stored as a URL
   with no text extracted, and the outline prompt had no input for it, so the document that
   authoritatively describes a course was the one input ignored.

These tests exercise the branch, the fallback, the grounding and the retry guard.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace

import pytest

from src.domains.knowledge.services import course_material_context, lesson_service
from src.domains.personal_learning.services import auto_setup_service, flashcard_service


def profile(**overrides):
    base = {
        "user_id": "user-1",
        "purpose": "skill_building",
        "subjects": ["Python"],
        "goals_text": "",
        "skill_name": None,
        "exam_name": None,
        "current_level": None,
        "onboarding_course_id": None,
        "onboarding_state": "details_set",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestWhichArtefactGetsBuilt:
    def test_learn_purposes_build_a_course(self):
        """`course_completion` is included because a learner completing a course needs one."""
        assert "skill_building" in auto_setup_service.COURSE_FIRST_PURPOSES
        assert "general_learning" in auto_setup_service.COURSE_FIRST_PURPOSES
        assert "course_completion" in auto_setup_service.COURSE_FIRST_PURPOSES

    def test_dated_purposes_still_build_a_preparation(self):
        """Everything the prep surface does is built around a date. An exam has one; "learn
        Python" does not, which is why it was the wrong container for it."""
        assert "exam_prep" not in auto_setup_service.COURSE_FIRST_PURPOSES
        assert "professional_certification" not in auto_setup_service.COURSE_FIRST_PURPOSES

    @pytest.mark.asyncio
    async def test_a_learn_learner_is_routed_to_the_course_path(self, monkeypatch):
        calls: list[str] = []

        async def fake_course_path(**kwargs):
            calls.append("course")
            return {"status": "completed", "created": {"courses": ["course-1"]}}

        async def fake_get_profile(user_id):
            return profile()

        monkeypatch.setattr(auto_setup_service, "_setup_course_first", fake_course_path)
        monkeypatch.setattr(
            auto_setup_service.repo, "get_profile_by_user", fake_get_profile, raising=False
        )

        result = await auto_setup_service.auto_setup_for_learner(user_id="user-1")

        assert calls == ["course"]
        assert result["created"]["courses"] == ["course-1"]


class TestWhatCountsAsSomethingToLearn:
    def test_subjects_are_used_when_given(self):
        assert (
            auto_setup_service._primary_subject(profile(subjects=["Optics", "Waves"])) == "Optics"
        )

    def test_a_skill_name_stands_in_for_a_missing_subject(self):
        """The hang. Mobile lets a learner submit a skill name with no subjects, and that used
        to read as an incomplete profile — so onboarding created nothing and never finished."""
        assert (
            auto_setup_service._primary_subject(profile(subjects=[], skill_name="  Rust  "))
            == "Rust"
        )

    def test_an_exam_name_stands_in_too(self):
        assert (
            auto_setup_service._primary_subject(
                profile(subjects=None, skill_name=None, exam_name="WAEC Chemistry")
            )
            == "WAEC Chemistry"
        )

    def test_nothing_recorded_is_still_nothing(self):
        assert auto_setup_service._primary_subject(profile(subjects=[], skill_name="")) is None

    @pytest.mark.asyncio
    async def test_setup_is_skipped_when_there_is_nothing_to_learn(self, monkeypatch):
        async def fake_get_profile(user_id):
            return profile(subjects=[], skill_name=None)

        monkeypatch.setattr(
            auto_setup_service.repo, "get_profile_by_user", fake_get_profile, raising=False
        )

        result = await auto_setup_service.auto_setup_for_learner(user_id="user-1")

        assert result == {"status": "skipped", "reason": "incomplete_profile"}


class TestTheOutlineReadsWhatWasUploaded:
    def test_material_is_placed_in_the_prompt(self):
        prompt = lesson_service.build_outline_prompt(
            title="Optics",
            brief="I want to understand lenses properly",
            source_material="[SYLLABUS · phy101.pdf]\nWeek 1: refraction and Snell's law",
        )

        assert "phy101.pdf" in prompt
        assert "Snell's law" in prompt
        # The rule that decides what happens when the two disagree.
        assert "material wins" in prompt.replace("\n  ", " ")

    def test_an_ungrounded_prompt_is_unchanged(self):
        """Most courses have no uploads. That path must not acquire instructions about
        material the learner never provided."""
        prompt = lesson_service.build_outline_prompt(title="Optics", brief="lenses, properly")

        assert "uploaded" not in prompt.lower()
        assert "material wins" not in prompt.replace("\n  ", " ")

    def test_a_course_with_no_readable_material_grounds_nothing(self):
        empty = course_material_context.prep_material_context.select([], budget=1000)

        assert course_material_context.as_prompt_material(empty) is None

    def test_material_is_labelled_by_filename_so_the_model_knows_what_it_reads(self):
        view = course_material_context._view(
            SimpleNamespace(extracted_text="Module 1: derivatives", title="MTH101 outline.pdf")
        )
        context = course_material_context.prep_material_context.select([view], budget=5_000)

        block = course_material_context.as_prompt_material(context)

        assert block is not None
        assert "MTH101 outline.pdf" in block
        assert "derivatives" in block

    def test_a_resource_with_no_text_is_not_offered_as_grounding(self):
        """A scanned PDF or an image is stored and downloadable, and contributes nothing."""
        view = course_material_context._view(
            SimpleNamespace(extracted_text=None, title="whiteboard.png")
        )
        context = course_material_context.prep_material_context.select([view], budget=5_000)

        assert context.has_text is False


class TestStarterCardsAreFiledAgainstWhateverWasBuilt:
    @pytest.mark.asyncio
    async def test_cards_land_in_a_course_deck_on_the_learn_path(self, monkeypatch):
        """Cards with a null `deckId` are invisible: the flashcards dashboard joins from
        `FlashcardDeck`, so unfiled cards match no row."""
        recorded: dict = {}

        async def fake_generate_content(prompt, **kwargs):
            return '[{"front": "What is a list?", "back": "An ordered, mutable sequence."}]'

        async def fake_ensure_deck(**kwargs):
            recorded.update(kwargs)
            return "deck-1"

        async def fake_create_flashcard(*, user_id, data):
            recorded.setdefault("cards", []).append(data)
            return SimpleNamespace(id="card-1")

        monkeypatch.setattr(
            "src.domains.personal_learning.services.llm_resilient.generate_content",
            fake_generate_content,
        )
        monkeypatch.setattr(flashcard_service, "ensure_deck_for_origin", fake_ensure_deck)
        monkeypatch.setattr(flashcard_service, "create_flashcard", fake_create_flashcard)

        cards = await auto_setup_service._generate_initial_flashcards(
            "user-1",
            "Python",
            origin_type=flashcard_service.DECK_ORIGIN_COURSE,
            origin_id="course-1",
            deck_title="Python — starter cards",
            deck_subject="Python",
        )

        assert len(cards) == 1
        assert recorded["origin_type"] == flashcard_service.DECK_ORIGIN_COURSE
        assert recorded["origin_id"] == "course-1"
        assert recorded["cards"][0]["deckId"] == "deck-1"
        # Points at the course, not at a subject string nothing can resolve.
        assert recorded["cards"][0]["sourceId"] == "course-1"

    @pytest.mark.asyncio
    async def test_no_subject_means_no_model_call(self, monkeypatch):
        async def explode(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("generation attempted with nothing to generate about")

        monkeypatch.setattr(
            "src.domains.personal_learning.services.llm_resilient.generate_content", explode
        )

        assert await auto_setup_service._generate_initial_flashcards("user-1", "") == []
