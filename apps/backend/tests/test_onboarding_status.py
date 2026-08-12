"""Tests for the onboarding status the setup screen polls.

This endpoint is the only thing standing between a new learner and their workspace,
and it had no tests. Two defects, both observed live on a real profile:

1. **`progress.topics`, `flashcards` and `studyPlan` were hardcoded `False`** with
   `TODO` comments, so the four-step setup screen could never advance past step one.
   A learner watched "Extracting key topics" spin indefinitely while their topics sat
   in the database.

2. **The exit condition trusted a flag rather than the content.** `content_ready` is
   written by the last line of a background task, so anything that stops that task —
   a crash, a deploy, a dropped `asyncio` task — leaves the flag behind while the
   content exists. The observed profile read `not_started` with **3 preparations and
   17 topics**, and the screen's only way forward was that flag.

Readiness is now derived from what exists. These tests are the four states that are
genuinely different, plus the two that must not regress.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import onboarding_service


class FakeRepo:
    def __init__(self):
        self.profile: SimpleNamespace | None = None
        self.preps: list[SimpleNamespace] = []
        self.topic_counts: dict[str, int] = {}
        self.flashcards = 0
        self.plans = 0

    def set_profile(self, state: str | None):
        self.profile = SimpleNamespace(
            user_id="user-1", onboarding_state=state, purpose="exam_prep"
        )

    def add_prep(self, prep_id: str, *, subject: str, topics: int = 0):
        self.preps.append(SimpleNamespace(id=prep_id, subject=subject))
        self.topic_counts[prep_id] = topics

    async def get_profile_by_user(self, user_id: str):
        return self.profile

    async def list_exam_preps(self, user_id: str, **kwargs):
        return self.preps

    async def count_prep_topics(self, prep_id: str):
        return self.topic_counts.get(prep_id, 0)

    async def count_flashcards(self, user_id: str):
        return self.flashcards

    async def count_study_plans(self, user_id: str):
        return self.plans


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(onboarding_service, "repo", fake)
    return fake


async def _status():
    return await onboarding_service.get_onboarding_status(user_id="user-1")


class TestProgressReflectsRealContent:
    @pytest.mark.asyncio
    async def test_every_step_is_read_from_the_database(self, repo):
        """All four were `False` literals. The screen ticks these, so a literal meant
        a step that could never complete."""
        repo.set_profile("details_set")
        repo.add_prep("prep-1", subject="Statistics", topics=10)
        repo.flashcards = 12
        repo.plans = 1

        status = await _status()

        assert status["progress"] == {
            "preparation": True,
            "topics": True,
            "flashcards": True,
            "studyPlan": True,
        }

    @pytest.mark.asyncio
    async def test_a_step_with_nothing_behind_it_is_false(self, repo):
        repo.set_profile("details_set")
        repo.add_prep("prep-1", subject="Statistics", topics=0)

        status = await _status()

        assert status["progress"]["preparation"] is True
        assert status["progress"]["topics"] is False
        assert status["progress"]["flashcards"] is False
        assert status["progress"]["studyPlan"] is False

    @pytest.mark.asyncio
    async def test_topics_are_counted_on_the_preparation_being_opened(self, repo):
        """The screen navigates into the *first* preparation, so that is the one whose
        readiness decides whether it can open."""
        repo.set_profile("details_set")
        repo.add_prep("prep-first", subject="Opens this one", topics=0)
        repo.add_prep("prep-other", subject="Not this one", topics=25)

        status = await _status()

        assert status["progress"]["topics"] is False
        assert status["firstPreparation"]["id"] == "prep-first"

    @pytest.mark.asyncio
    async def test_no_profile_is_not_started_rather_than_an_error(self, repo):
        status = await _status()

        assert status["state"] == "not_started"
        assert status["firstPreparation"] is None


class TestReadinessIsDerivedNotTrusted:
    @pytest.mark.asyncio
    async def test_content_that_exists_reports_ready_whatever_the_flag_says(self, repo):
        """The exact live case: `not_started` with preparations and topics already
        created, and a screen whose only exit was the flag."""
        repo.set_profile("not_started")
        repo.add_prep("prep-1", subject="English", topics=17)

        status = await _status()

        assert status["state"] == "content_ready"
        assert status["estimatedSecondsRemaining"] == 0

    @pytest.mark.asyncio
    async def test_a_preparation_with_topics_is_enough(self, repo):
        """Flashcards and the study plan are best-effort in `auto_setup_for_learner` —
        each is wrapped in its own `try` and returns empty on failure — so requiring
        them would strand a learner whose content is perfectly usable."""
        repo.set_profile("details_set")
        repo.add_prep("prep-1", subject="Statistics", topics=8)
        repo.flashcards = 0
        repo.plans = 0

        status = await _status()

        assert status["state"] == "content_ready"

    @pytest.mark.asyncio
    async def test_a_preparation_without_topics_is_not_ready(self, repo):
        """Opening a preparation with no topics gives a workspace that cannot
        practise, which is worse than waiting."""
        repo.set_profile("details_set")
        repo.add_prep("prep-1", subject="Statistics", topics=0)

        status = await _status()

        assert status["state"] == "details_set"
        assert status["estimatedSecondsRemaining"] == 30

    @pytest.mark.asyncio
    async def test_completed_is_never_walked_backwards(self, repo):
        """A learner who has finished onboarding must not be sent through it again
        because their content was later deleted."""
        repo.set_profile("completed")

        status = await _status()

        assert status["state"] == "completed"

    @pytest.mark.asyncio
    async def test_completed_survives_content_being_present(self, repo):
        repo.set_profile("completed")
        repo.add_prep("prep-1", subject="Statistics", topics=10)

        assert (await _status())["state"] == "completed"

    @pytest.mark.asyncio
    async def test_an_estimate_is_only_offered_while_something_is_pending(self, repo):
        """`None` means "no estimate", and the screen hides the line. Offering "about
        30 seconds" to a learner nothing is happening for is the claim that stalled
        this screen in the first place."""
        repo.set_profile("not_started")

        status = await _status()

        assert status["state"] == "not_started"
        assert status["estimatedSecondsRemaining"] is None


class TestBackgroundTaskIsHeld:
    def test_a_reference_is_kept_to_the_spawned_task(self):
        """`asyncio.create_task` is only weakly referenced by the loop, so a discarded
        handle can be garbage-collected part-way through. Both call sites discarded it,
        which is one way a profile ends up reading `not_started` while its content
        exists — the work happened and the line that advanced the state never ran.
        """
        assert isinstance(onboarding_service._IN_FLIGHT, set)

    @pytest.mark.asyncio
    async def test_the_task_is_registered_then_discarded_on_completion(self, monkeypatch):
        completed = []

        async def fake_generate(**kwargs):
            completed.append(kwargs)

        monkeypatch.setattr(onboarding_service, "_generate_onboarding_content", fake_generate)

        onboarding_service._spawn_content_generation(user_id="user-1")
        # Registered synchronously, before the coroutine has had a chance to run.
        assert len(onboarding_service._IN_FLIGHT) == 1

        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert completed == [{"user_id": "user-1"}]
        # And released, so the set cannot grow without bound.
        assert onboarding_service._IN_FLIGHT == set()
