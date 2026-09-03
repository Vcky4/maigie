"""Background AI spends on learners who are still here, and at a cadence that follows the tier.

Decision M, and the cost it exists to stop: two Celery tasks called a model per learner per schedule
over **every profile that existed**, which is $0.64/month for someone who last opened the app in
March. §6.5 called that "the part the earlier revenue model missed entirely", and it is more than
three times the free-tier inference budget the previous model assumed for everything.

Two mechanisms, tested separately because they fail differently:

**The dormancy stop** is a query filter — it decides who is in the fan-out at all. Its failure mode is
generating for the long gone, which is invisible: nobody complains about a recommendation they never
saw, and the only symptom is the bill.

**The cadence gate** is per learner — it decides whether a learner in the fan-out is due. Its failure
mode is the opposite and very visible: get it wrong and a free learner's discovery feed stops. So it
fails open, and that is asserted.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning.services import discovery_service  # noqa: E402
from src.domains.personal_learning.tasks import recommendations, reflections  # noqa: E402


class TestTheDormancyCutoff:
    def test_it_is_seven_days_back(self):
        cutoff = recommendations._dormancy_cutoff()
        expected = datetime.now(UTC) - timedelta(
            days=recommendations.PROACTIVE_ACTIVITY_WINDOW_DAYS
        )

        assert abs((cutoff - expected).total_seconds()) < 5
        assert recommendations.PROACTIVE_ACTIVITY_WINDOW_DAYS == 7

    def test_both_proactive_tasks_use_the_same_window(self):
        """Two tasks, one rule. Divergent windows would mean a learner who is dormant for
        recommendations and active for reflections, which is not a state anyone could explain."""
        assert (
            reflections.PROACTIVE_ACTIVITY_WINDOW_DAYS
            == recommendations.PROACTIVE_ACTIVITY_WINDOW_DAYS
        )

    def test_a_weekend_learner_is_not_dormant_midweek(self):
        """Why the window is seven days rather than one. Someone who studies at weekends is not gone
        on a Wednesday, and a shorter window would stop generating for exactly the learners whose
        habit is real but not daily."""
        last_active = datetime.now(UTC) - timedelta(days=4)

        assert last_active >= recommendations._dormancy_cutoff()

    def test_a_learner_gone_a_month_is_dormant(self):
        last_active = datetime.now(UTC) - timedelta(days=30)

        assert last_active < recommendations._dormancy_cutoff()


class TestTheFanOutIsFiltered:
    """The task has to *pass* the cutoff, not merely compute it."""

    @pytest.fixture
    def spy_repo(self, monkeypatch):
        seen: dict = {}

        async def fake_list(*, skip, take, active_since=None):
            seen["active_since"] = active_since
            seen.setdefault("calls", 0)
            seen["calls"] += 1
            return []

        monkeypatch.setattr(
            "src.domains.personal_learning.repository.personal_learning_repo.list_active_profiles",
            fake_list,
        )
        return seen

    async def test_the_recommendations_task_asks_only_for_active_learners(
        self, spy_repo, monkeypatch
    ):
        async def noop():
            return None

        monkeypatch.setattr("src.shared.database.session.ensure_db", noop)

        await recommendations._generate_recommendations_async()

        assert (
            spy_repo["active_since"] is not None
        ), "the nightly fan-out must be filtered, or it generates for everyone who ever signed up"
        assert spy_repo["active_since"] < datetime.now(UTC)


class TestTheCadenceFollowsTheTier:
    @pytest.fixture
    def world(self, monkeypatch):
        state = SimpleNamespace(tier="free", last_at=None)

        async def resolve(_user_id):
            return SimpleNamespace(tier=state.tier)

        async def latest(_user_id):
            return state.last_at

        monkeypatch.setattr("src.domains.billing.services.entitlement_service.resolve", resolve)
        monkeypatch.setattr(
            "src.domains.personal_learning.repository.personal_learning_repo.latest_recommendation_at",
            latest,
        )
        return state

    async def test_plus_is_due_every_night(self, world):
        world.tier = "plus"
        world.last_at = datetime.now(UTC) - timedelta(hours=2)

        assert await discovery_service._cadence_allows("u1") is True

    async def test_free_is_not_due_the_next_night(self, world):
        world.tier = "free"
        world.last_at = datetime.now(UTC) - timedelta(days=1)

        assert await discovery_service._cadence_allows("u1") is False

    async def test_free_is_due_after_a_week(self, world):
        world.tier = "free"
        world.last_at = datetime.now(UTC) - timedelta(days=7, minutes=1)

        assert await discovery_service._cadence_allows("u1") is True

    async def test_a_learner_with_nothing_yet_is_always_due(self, world):
        """A first set is not a re-generation. Withholding it for six days would make discovery look
        broken to a learner who had just arrived."""
        world.tier = "free"
        world.last_at = None

        assert await discovery_service._cadence_allows("u1") is True

    async def test_a_naive_timestamp_does_not_crash_the_gate(self, world):
        """`created_at` has come back naive from this database before — the column is `timestamp
        without time zone` while the ORM declares `timezone=True`, and that mismatch answered `500`
        on `GET /progress/goals` once already. A cadence gate is not the place to rediscover it."""
        world.tier = "free"
        world.last_at = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)

        assert await discovery_service._cadence_allows("u1") is False

    async def test_it_fails_open(self, world, monkeypatch):
        """A gate that cannot read an entitlement must let the generation happen.

        The cost of one extra recommendation is ~150 units; the cost of the opposite mistake is a
        learner whose discovery feed silently stops. Same posture as the headroom gate, and the
        deliberate opposite of the model-quality gate, which fails to the *cheap* model because
        over-granting there is a margin question rather than a broken feature.
        """

        async def exploding(_user_id):
            raise RuntimeError("resolver unavailable")

        monkeypatch.setattr("src.domains.billing.services.entitlement_service.resolve", exploding)

        assert await discovery_service._cadence_allows("u1") is True


class TestTheGateRunsBeforeAnythingDestructive:
    async def test_a_learner_not_due_keeps_last_weeks_recommendations(self, monkeypatch):
        """`delete_old_recommendations` is destructive, so the order matters.

        Returning early *after* the delete would leave a free learner with nothing for six days
        instead of with last week's set — and that reads as the feature failing rather than as a
        cadence.
        """
        deleted: list[str] = []

        async def fake_delete(user_id):
            deleted.append(user_id)

        async def not_due(_user_id):
            return False

        monkeypatch.setattr(
            "src.domains.personal_learning.repository.personal_learning_repo.delete_old_recommendations",
            fake_delete,
        )
        monkeypatch.setattr(discovery_service, "_cadence_allows", not_due)

        created = await discovery_service.generate_recommendations(user_id="u1")

        assert created == 0
        assert not deleted, "nothing may be deleted for a learner who is not due a fresh set"
