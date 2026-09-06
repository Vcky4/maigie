"""Notification creation and delivery: what gets held back, what gets released, what gets thrown away.

**Three defects these exist for**, all of them silent.

The daily allowance *destroyed* messages: over the cap, `create_notification` returned `None` and the
notification simply never existed. Indistinguishable, from the caller's side, from never having tried — which
is why `GoalLifecycleAction`, `PrepOutcome` and `StudyPlan.lastCheckInAt` all had to record attempts rather
than deliveries. It now defers.

Queued notifications were *never delivered at all*. `list_pending_for_delivery` selected `PENDING` only, so
every notification quiet hours had ever deferred was written, given a later `scheduledAt`, and read by
nothing. It still appeared in the in-app list, because that read filters on `READ`/`DISMISSED` rather than on
delivery, which is why it survived this long.

And a push, once wired, must not claim deliveries it did not make: nothing registers a `DeviceToken`, so
every send returns `no_tokens` today.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning.services import notification_service as svc  # noqa: E402
from src.shared.time import LearnerTimezone  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
)


def _profile(**overrides) -> SimpleNamespace:
    defaults = {
        "quiet_hours_start": None,
        "quiet_hours_end": None,
        "max_daily_notifications": 5,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _ago(**delta) -> datetime:
    """An instant relative to the **real** clock.

    Delivery rows have to be anchored to real time rather than to `NOW`, because `deliver_pending` takes
    no injectable clock — it reads `datetime.now(UTC)` and compares `scheduledAt` against it to decide
    whether a held-back message has gone stale. Frozen instants would drift out of `MAX_DEFERRAL_DAYS`
    and start expiring rows the test expected delivered, three days after the constant was written.

    `create_notification` is the opposite case and keeps `NOW`: it accepts `scheduled_at`, so its clock
    can be this file's.
    """
    return datetime.now(UTC) - timedelta(**delta)


def _row(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "n1",
        "user_id": "user-1",
        "type": "goal_at_risk",
        "title": "T",
        "body": "B",
        "action_data": {"goalId": "g1"},
        "scheduled_at": _ago(minutes=1),
        "status": "PENDING",
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self, *, profile=None, delivered_today=0):
        self.profile = profile if profile is not None else _profile()
        self.delivered_today = delivered_today
        self.created: list[dict] = []
        self.count_windows: list[tuple] = []

    async def get_profile_by_user(self, _user_id, **_kw):
        return self.profile

    async def count_delivered_between(self, _user_id, *, since, until, **_kw):
        self.count_windows.append((since, until))
        return self.delivered_today

    async def create_notification(self, data, **_kw):
        self.created.append(data)
        return SimpleNamespace(id="created", **{k: v for k, v in data.items()})


@pytest.fixture
def wire(monkeypatch):
    def _wire(repo, *, timezone_=LAGOS):
        monkeypatch.setattr(svc, "repo", repo)
        monkeypatch.setattr(
            "src.shared.time.learner_timezone.resolve_learner_timezone",
            _coro(timezone_),
        )
        monkeypatch.setattr(svc, "resolve_learner_timezone", _coro(timezone_))

    return _wire


def _coro(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


# ===========================================================================
# The allowance defers rather than destroying
# ===========================================================================


class TestTheDailyAllowance:
    @pytest.mark.asyncio
    async def test_a_notification_over_the_allowance_still_exists(self, wire):
        """**The original defect.** It used to return `None` and the message was gone — which a caller
        cannot tell apart from never having tried."""
        repo = FakeRepo(delivered_today=5)
        wire(repo)

        result = await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
        )

        assert result is not None
        assert len(repo.created) == 1

    @pytest.mark.asyncio
    async def test_it_is_held_until_the_learners_next_day(self, wire):
        repo = FakeRepo(delivered_today=5)
        wire(repo)

        # `scheduled_at` is passed so the service's clock is this test's, not the machine's. Without it
        # these assertions depended on the wall clock and **passed by coincidence**: they broke the moment
        # real time crossed 23:00 UTC, because that is midnight in Lagos and the learner's day rolled over.
        await svc.create_notification(
            user_id="user-1",
            type="suggestion",
            title="T",
            body="B",
            priority=4,
            scheduled_at=NOW,
        )

        written = repo.created[0]
        assert written["status"] == "QUEUED"
        # Lagos is an hour ahead, so their next midnight is 23:00 UTC.
        assert written["scheduledAt"] == datetime(2026, 8, 27, 23, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_under_the_allowance_it_goes_now(self, wire):
        repo = FakeRepo(delivered_today=2)
        wire(repo)

        await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
        )

        assert repo.created[0]["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_a_time_critical_message_outranks_the_allowance(self, wire):
        """The allowance protects attention. It should not silence a date — before this, the fifth
        recommendation of the day could bump the warning that an exam was in two days."""
        repo = FakeRepo(delivered_today=99)
        wire(repo)

        await svc.create_notification(
            user_id="user-1",
            type="goal_at_risk",
            title="T",
            body="B",
            priority=svc.PRIORITY_TIME_CRITICAL,
        )

        assert repo.created[0]["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_the_allowance_is_counted_over_the_learners_own_day(self, wire):
        """A UTC window refills the allowance at 01:00 in Lagos and 16:00 in Los Angeles, so the second
        learner could be messaged their whole quota twice inside a working day."""
        repo = FakeRepo(delivered_today=0)
        wire(repo)

        # Clock injected for the same reason as above: the window asserted below is the learner's own day
        # around a fixed instant, not around whenever the suite happens to run.
        await svc.create_notification(
            user_id="user-1",
            type="suggestion",
            title="T",
            body="B",
            priority=4,
            scheduled_at=NOW,
        )

        since, until = repo.count_windows[0]
        assert since == datetime(2026, 8, 26, 23, 0, tzinfo=UTC)
        assert until == datetime(2026, 8, 27, 23, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_a_learner_with_no_profile_gets_the_default_allowance(self, wire):
        repo = FakeRepo(profile=None, delivered_today=svc.DEFAULT_MAX_DAILY)
        wire(repo)

        await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
        )

        assert repo.created[0]["status"] == "QUEUED"


# ===========================================================================
# Quiet hours
# ===========================================================================


class TestQuietHours:
    @pytest.mark.asyncio
    async def test_a_message_in_the_learners_night_waits_for_morning(self, wire):
        repo = FakeRepo(profile=_profile(quiet_hours_start="22:00", quiet_hours_end="07:00"))
        wire(repo)

        # 23:00 UTC is midnight in Lagos.
        await svc.create_notification(
            user_id="user-1",
            type="suggestion",
            title="T",
            body="B",
            priority=4,
            scheduled_at=datetime(2026, 8, 27, 23, 0, tzinfo=UTC),
        )

        written = repo.created[0]
        assert written["status"] == "QUEUED"
        assert written["scheduledAt"] == datetime(2026, 8, 28, 6, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_quiet_hours_hold_even_a_time_critical_message(self, wire):
        """A deadline a few hours away does not justify waking someone, and nothing on this path is urgent
        on the scale that would."""
        repo = FakeRepo(profile=_profile(quiet_hours_start="22:00", quiet_hours_end="07:00"))
        wire(repo)

        await svc.create_notification(
            user_id="user-1",
            type="goal_at_risk",
            title="T",
            body="B",
            priority=svc.PRIORITY_TIME_CRITICAL,
            scheduled_at=datetime(2026, 8, 27, 23, 0, tzinfo=UTC),
        )

        assert repo.created[0]["status"] == "QUEUED"

    @pytest.mark.asyncio
    async def test_no_quiet_hours_configured_means_no_waiting(self, wire):
        repo = FakeRepo()
        wire(repo)

        await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
        )

        assert repo.created[0]["status"] == "PENDING"
