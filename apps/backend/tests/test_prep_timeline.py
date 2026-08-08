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
from src.shared.exceptions import NotFoundError

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

    def add_plan(self, prep_id: str, user_id: str, plan_id: str, items: list[SimpleNamespace]):
        self.plans.setdefault((prep_id, user_id), []).append(
            SimpleNamespace(id=plan_id, items=items)
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

    async def test_items_from_multiple_plans_are_merged(self, repo):
        """Regenerating a plan should not hide the earlier one's items."""
        repo.add_prep("prep-1", OWNER)
        repo.add_plan("prep-1", OWNER, "plan-1", [_item("old", days=1)])
        repo.add_plan("prep-1", OWNER, "plan-2", [_item("new", days=2)])

        timeline = await exam_prep_service.get_timeline(user_id=OWNER, prep_id="prep-1")

        ids = [m["id"] for m in timeline["milestones"] if m["kind"] == "STUDY"]
        assert ids == ["old", "new"]

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
