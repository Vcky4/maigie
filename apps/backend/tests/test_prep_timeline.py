"""Tests for the derived preparation timeline (no DB required).

The timeline has no table. It is built from the linked study plan's items plus the
preparation's target date, so that "what should I do by when" has exactly one
source of truth rather than a milestone entity that drifts from the plan.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import exam_prep_service
from src.shared.exceptions import MaigieError, NotFoundError

OWNER = "user-owner"
INTRUDER = "user-intruder"
NOW = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
EXAM_DATE = NOW + timedelta(days=30)


class FakeRepo:
    def __init__(self):
        self.preps: dict[tuple[str, str], SimpleNamespace] = {}
        self.plans: dict[tuple[str, str], list[SimpleNamespace]] = {}

    def add_prep(self, prep_id: str, user_id: str, *, subject="Statistics", exam_date=EXAM_DATE):
        self.preps[(prep_id, user_id)] = SimpleNamespace(
            id=prep_id,
            user_id=user_id,
            subject=subject,
            exam_date=exam_date,
            status="IN_PROGRESS",
        )

    def add_plan(
        self,
        prep_id: str,
        user_id: str,
        plan_id: str,
        items: list[SimpleNamespace],
        *,
        status: str = "ACTIVE",
    ):
        self.plans.setdefault((prep_id, user_id), []).append(
            SimpleNamespace(id=plan_id, items=items, status=status)
        )

    async def find_exam_prep(self, prep_id: str, user_id: str):
        return self.preps.get((prep_id, user_id))

    async def list_prep_study_plans(self, prep_id: str, user_id: str):
        return self.plans.get((prep_id, user_id), [])


def _item(item_id: str, *, days: int, title="Review topic", status="PENDING", topic_id="topic-1"):
    return SimpleNamespace(
        id=item_id,
        title=title,
        description="Some detail",
        scheduled_date=NOW + timedelta(days=days),
        estimated_minutes=30,
        item_type="STUDY",
        status=status,
        prep_topic_id=topic_id,
        completed_at=None,
    )


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(exam_prep_service, "repo", fake)
    return fake


class TestDerivedTimeline:
    async def test_plan_items_become_milestones(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("i1", days=1), _item("i2", days=2)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        study = [m for m in timeline["milestones"] if m["kind"] == "STUDY"]
        assert [m["id"] for m in study] == ["i1", "i2"]
        assert study[0]["studyPlanId"] == "plan-1"
        assert study[0]["estimatedMinutes"] == 30
        assert study[0]["prepTopicId"] == "topic-1"

    async def test_milestones_are_ordered_by_date(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan(
            "prep-1",
            OWNER,
            "plan-1",
            [_item("late", days=10), _item("early", days=1), _item("mid", days=5)],
        )

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        ids = [m["id"] for m in timeline["milestones"] if m["kind"] == "STUDY"]
        assert ids == ["early", "mid", "late"]

    async def test_exam_is_the_final_milestone(self, repo):
        """The one date the learner is actually working towards."""
        repo.add_prep("prep-1", OWNER, subject="Statistics final")
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("i1", days=1)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        last = timeline["milestones"][-1]
        assert last["kind"] == "EXAM"
        assert last["title"] == "Statistics final"
        assert last["scheduledFor"] == EXAM_DATE

    async def test_exam_stays_last_even_when_items_are_scheduled_after_it(self, repo):
        """A plan can overrun its own deadline; the exam is still the end."""
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("overrun", days=90)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["milestones"][-1]["kind"] == "EXAM"

    async def test_no_plan_yields_only_the_exam_and_says_so(self, repo):
        """`hasStudyPlan` lets the client offer generation instead of rendering a
        planned-and-empty timeline."""
        repo.add_prep("prep-1", OWNER)

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is False
        assert len(timeline["milestones"]) == 1
        assert timeline["milestones"][0]["kind"] == "EXAM"

    async def test_has_study_plan_is_true_once_one_exists(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("i1", days=1)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is True

    async def test_an_empty_plan_still_counts_as_having_one(self, repo):
        """A generated plan with no items is a plan, not an absence of one."""
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is True
        assert len(timeline["milestones"]) == 1

    async def test_items_from_multiple_live_plans_are_merged(self, repo):
        """Two plans that are both current both contribute."""
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("first", days=1)])
        repo.add_plan("prep-1", OWNER, "plan-2", [_item("second", days=2)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        ids = [m["id"] for m in timeline["milestones"] if m["kind"] == "STUDY"]
        assert ids == ["first", "second"]

    async def test_a_superseded_plans_pending_items_are_dropped(self, repo):
        """Regenerating replaces the schedule rather than doubling it.

        This was the original behaviour and it was wrong: every plan contributed its
        items regardless of status, so a second generation listed each topic twice on
        overlapping days. Described as "not hiding history", it was in fact two
        competing answers to what to do tomorrow.
        """
        repo.add_prep("prep-1", OWNER)
        repo.add_plan(
            "prep-1",
            OWNER,
            "plan-old",
            [_item("stale", days=1), _item("already-done", days=2, status="COMPLETED")],
            status="SUPERSEDED",
        )
        repo.add_plan("prep-1", OWNER, "plan-new", [_item("current", days=3)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        ids = [m["id"] for m in timeline["milestones"] if m["kind"] == "STUDY"]
        # The completed item stays: it is work the learner actually did on that date.
        # The pending one goes: the new plan has replaced it.
        assert ids == ["already-done", "current"]

    async def test_a_superseded_plan_with_nothing_completed_leaves_no_trace(self, repo):
        """And then `hasStudyPlan` must be false again.

        Otherwise the client suppresses its generate offer and shows a timeline
        holding only the exam — the planned-and-empty misread the flag exists for.
        """
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-old", [_item("stale", days=1)], status="SUPERSEDED")

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is False
        assert [m["kind"] for m in timeline["milestones"]] == ["EXAM"]

    async def test_a_superseded_plan_with_completed_work_still_counts(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan(
            "prep-1",
            OWNER,
            "plan-old",
            [_item("done", days=1, status="COMPLETED")],
            status="SUPERSEDED",
        )

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is True
        assert [m["id"] for m in timeline["milestones"] if m["kind"] == "STUDY"] == ["done"]

    async def test_item_status_is_carried_through(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("done", days=1, status="COMPLETED")])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert timeline["milestones"][0]["status"] == "COMPLETED"

    async def test_plan_items_none_is_tolerated(self, repo):
        """A plan loaded without items must not raise."""
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", None)

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        assert len(timeline["milestones"]) == 1


class TestPlanGenerationPreconditions:
    """The timeline is empty until a plan exists, so generation is the real feature.

    `scripts/check_prep_timeline.py` measured both refusals against live data: of 23
    preparations, 12 have no topics and 12 have a target date in the past. Neither
    was previously refused — the first invented a schedule from the title, the second
    scheduled everything today.
    """

    @pytest.fixture
    def plan_repo(self, repo, monkeypatch):
        repo.topics: dict[str, list[SimpleNamespace]] = {}
        repo.status_writes: list[tuple[str, str]] = []
        generated: list[dict] = []

        async def list_prep_topics(prep_id: str):
            return repo.topics.get(prep_id, [])

        async def update_plan_status(plan_id: str, status: str):
            repo.status_writes.append((plan_id, status))

        async def generate_plan(*, user_id: str, data: dict):
            generated.append(data)
            plan = SimpleNamespace(id=f"plan-{len(generated)}", items=[], status="ACTIVE")
            repo.plans.setdefault((data["prepId"], user_id), []).append(plan)
            return plan

        repo.list_prep_topics = list_prep_topics
        repo.update_plan_status = update_plan_status
        monkeypatch.setattr(
            "src.domains.personal_learning.services.study_plan_service.generate_plan",
            generate_plan,
        )
        repo.generated = generated
        return repo

    async def test_a_preparation_with_topics_generates(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]

        plan = await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert plan.id == "plan-1"
        # The deadline is the preparation's own target date, not a 30-day default.
        assert plan_repo.generated[-1]["deadline"] == EXAM_DATE
        assert plan_repo.generated[-1]["prepId"] == "prep-1"

    async def test_no_topics_is_refused_rather_than_invented(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER)

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert excinfo.value.code == "PREP_TOPICS_REQUIRED"
        assert excinfo.value.status_code == 409
        assert plan_repo.generated == []

    async def test_a_past_target_date_is_refused(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER, exam_date=NOW - timedelta(days=3))
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert excinfo.value.code == "PREP_TARGET_DATE_PASSED"
        assert plan_repo.generated == []

    async def test_a_naive_target_date_is_still_compared(self, plan_repo):
        """The column is timezone-aware in the model and naive in some rows.

        Comparing a naive datetime to an aware one raises `TypeError`, which would be
        a 500 on the generate button rather than the refusal it should be.
        """
        plan_repo.add_prep("prep-1", OWNER, exam_date=datetime(2020, 1, 1))
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert excinfo.value.code == "PREP_TARGET_DATE_PASSED"

    async def test_everything_mastered_is_its_own_message(self, plan_repo):
        """Not a gap to fill. There is nothing left to schedule."""
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [
            SimpleNamespace(id="t-1", status="MASTERED"),
            SimpleNamespace(id="t-2", status="MASTERED"),
        ]

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert excinfo.value.code == "PREP_ALL_TOPICS_MASTERED"

    async def test_one_unmastered_topic_is_enough(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [
            SimpleNamespace(id="t-1", status="MASTERED"),
            SimpleNamespace(id="t-2", status="IN_PROGRESS"),
        ]

        await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert len(plan_repo.generated) == 1

    async def test_regenerating_supersedes_the_previous_plan(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]

        await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")
        await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        # Only the first, and never the one just created.
        assert plan_repo.status_writes == [("plan-1", "SUPERSEDED")]

    async def test_a_completed_plan_is_not_superseded(self, plan_repo):
        """Completing a plan is an outcome. Superseding it would rewrite that."""
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]
        plan_repo.add_plan("prep-1", OWNER, "plan-finished", [], status="COMPLETED")

        await exam_prep_service.generate_preparation_plan(user_id=OWNER, prep_id="prep-1")

        assert plan_repo.status_writes == []

    async def test_another_learners_preparation_cannot_be_planned(self, plan_repo):
        plan_repo.add_prep("prep-1", OWNER)
        plan_repo.topics["prep-1"] = [SimpleNamespace(id="t-1", status="IN_PROGRESS")]

        with pytest.raises(NotFoundError):
            await exam_prep_service.generate_preparation_plan(user_id=INTRUDER, prep_id="prep-1")
        assert plan_repo.generated == []


class TestTimelineOwnership:
    async def test_unknown_preparation_is_not_found(self, repo):
        with pytest.raises(NotFoundError):
            await exam_prep_service.get_timeline(user_id=OWNER, prep_id="nope")

    async def test_another_users_preparation_is_not_found(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("i1", days=1)])

        with pytest.raises(NotFoundError):
            await exam_prep_service.get_timeline(user_id=INTRUDER, prep_id="prep-1")

    async def test_plans_are_scoped_to_the_learner(self, repo):
        """The plan query is scoped by user as well as by preparation."""
        repo.add_prep("prep-1", OWNER)
        repo.add_prep("prep-1", INTRUDER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("owners", days=1)])

        timeline = await exam_prep_service.get_timeline(user_id=INTRUDER, prep_id="prep-1")

        assert timeline["hasStudyPlan"] is False
        assert [m["kind"] for m in timeline["milestones"]] == ["EXAM"]
