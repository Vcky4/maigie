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


def _row(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "n1",
        "user_id": "user-1",
        "type": "goal_at_risk",
        "title": "T",
        "body": "B",
        "action_data": {"goalId": "g1"},
        "scheduled_at": NOW - timedelta(minutes=1),
        "status": "PENDING",
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self, *, profile=None, delivered_today=0, due=()):
        self.profile = profile if profile is not None else _profile()
        self.delivered_today = delivered_today
        self.due = list(due)
        self.created: list[dict] = []
        self.status_updates: list[dict] = []
        self.count_windows: list[tuple] = []
        self.list_limit: int | None = None
        self.fail_status_for: set[str] = set()

    async def get_profile_by_user(self, _user_id, **_kw):
        return self.profile

    async def count_delivered_between(self, _user_id, *, since, until, **_kw):
        self.count_windows.append((since, until))
        return self.delivered_today

    async def create_notification(self, data, **_kw):
        self.created.append(data)
        return SimpleNamespace(id="created", **{k: v for k, v in data.items()})

    async def list_due_for_delivery(self, *, limit=500, **_kw):
        self.list_limit = limit
        return list(self.due)

    async def update_status(self, notification_id, status, delivered_at=None, pushed_at=None, **_kw):
        if notification_id in self.fail_status_for:
            raise RuntimeError("write failed")
        self.status_updates.append(
            {
                "id": notification_id,
                "status": status,
                "delivered_at": delivered_at,
                "pushed_at": pushed_at,
            }
        )


@pytest.fixture
def wire(monkeypatch):
    def _wire(repo, *, timezone_=LAGOS, push_result=None, push_allowed=True):
        monkeypatch.setattr(svc, "repo", repo)
        monkeypatch.setattr(
            "src.shared.time.learner_timezone.resolve_learner_timezone",
            _coro(timezone_),
        )
        monkeypatch.setattr(svc, "resolve_learner_timezone", _coro(timezone_))
        monkeypatch.setattr(svc, "resolve_many", _coro_many(timezone_))
        monkeypatch.setattr(svc, "_push_allowed", _coro(push_allowed))
        sends: list[dict] = []

        async def _send(**kwargs):
            sends.append(kwargs)
            return push_result if push_result is not None else {"sent": 0, "no_tokens": True}

        monkeypatch.setattr(
            "src.shared.infrastructure.push_notifications.send_push_notification", _send
        )
        return sends

    return _wire


def _coro(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def _coro_many(timezone_):
    async def _inner(user_ids, *_a, **_k):
        return {uid: timezone_ for uid in user_ids}

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

        await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
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

        await svc.create_notification(
            user_id="user-1", type="suggestion", title="T", body="B", priority=4
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


# ===========================================================================
# Delivery
# ===========================================================================


class TestDelivery:
    @pytest.mark.asyncio
    async def test_a_queued_notification_is_finally_delivered(self, wire):
        """**The black hole.** Only `PENDING` was ever selected, so everything quiet hours deferred was
        written and then read by nothing."""
        repo = FakeRepo(due=[_row(status="QUEUED")])
        wire(repo)

        assert await svc.deliver_pending() == 1
        assert repo.status_updates[0]["status"] == "DELIVERED"

    @pytest.mark.asyncio
    async def test_nothing_due_touches_nothing(self, wire):
        repo = FakeRepo(due=[])
        wire(repo)

        assert await svc.deliver_pending() == 0
        assert repo.status_updates == []

    @pytest.mark.asyncio
    async def test_a_learner_still_in_their_night_is_left_alone(self, wire):
        """Re-checked at delivery rather than trusted from creation, because a learner can change their
        quiet hours after a notification was scheduled."""
        repo = FakeRepo(
            profile=_profile(quiet_hours_start="00:00", quiet_hours_end="23:59"),
            due=[_row(status="QUEUED")],
        )
        wire(repo)

        assert await svc.deliver_pending() == 0
        assert repo.status_updates == []

    @pytest.mark.asyncio
    async def test_a_message_too_stale_to_help_is_expired_not_delivered(self, wire):
        """"Your exam is in two days" arriving after the exam is worse than silence, because the learner
        acts on it."""
        stale = _row(scheduled_at=NOW - timedelta(days=svc.MAX_DEFERRAL_DAYS + 1))
        repo = FakeRepo(due=[stale])
        wire(repo)

        assert await svc.deliver_pending() == 0
        assert repo.status_updates[0]["status"] == "EXPIRED"
        assert repo.status_updates[0]["delivered_at"] is None

    @pytest.mark.asyncio
    async def test_a_recent_deferral_is_still_worth_delivering(self, wire):
        repo = FakeRepo(due=[_row(scheduled_at=NOW - timedelta(hours=8))])
        wire(repo)

        assert await svc.deliver_pending() == 1

    @pytest.mark.asyncio
    async def test_the_batch_is_bounded(self, wire):
        """The sweep runs every five minutes under a 45 second soft limit and now does network I/O per row,
        so an unbounded backlog would time out and make no progress at all."""
        repo = FakeRepo(due=[])
        wire(repo)

        await svc.deliver_pending()

        assert repo.list_limit == svc.DELIVERY_BATCH

    @pytest.mark.asyncio
    async def test_one_bad_row_does_not_strand_everyone_elses(self, wire):
        repo = FakeRepo(due=[_row(id="bad"), _row(id="good", user_id="user-2")])
        repo.fail_status_for = {"bad"}
        wire(repo)

        assert await svc.deliver_pending() == 1
        assert [u["id"] for u in repo.status_updates] == ["good"]


# ===========================================================================
# Push
# ===========================================================================


class TestPush:
    @pytest.mark.asyncio
    async def test_delivery_is_recorded_before_the_push_is_attempted(self, wire):
        """Deliberate ordering. The status write is what stops the row being selected again, so a crash
        between the two loses a push rather than repeating one — the right way round for something that
        buzzes a phone in a pocket."""
        repo = FakeRepo(due=[_row()])
        wire(repo, push_result={"sent": 1, "failed": 0})

        await svc.deliver_pending()

        assert repo.status_updates[0]["status"] == "DELIVERED"
        assert repo.status_updates[0]["delivered_at"] is not None
        assert repo.status_updates[0]["pushed_at"] is None
        # The push is recorded afterwards, as a second write.
        assert repo.status_updates[1]["pushed_at"] is not None

    @pytest.mark.asyncio
    async def test_no_device_tokens_is_not_recorded_as_a_push(self, wire):
        """True of every learner today: nothing registers a `DeviceToken`. Recording it as a push would put
        a claim in the database that nothing sent anything to."""
        repo = FakeRepo(due=[_row()])
        wire(repo, push_result={"sent": 0, "failed": 0, "no_tokens": True})

        assert await svc.deliver_pending() == 1
        assert all(u["pushed_at"] is None for u in repo.status_updates)

    @pytest.mark.asyncio
    async def test_an_unconfigured_firebase_is_not_recorded_as_a_push(self, wire):
        repo = FakeRepo(due=[_row()])
        wire(repo, push_result={"sent": 0, "failed": 0, "skipped": True})

        assert await svc.deliver_pending() == 1
        assert all(u["pushed_at"] is None for u in repo.status_updates)

    @pytest.mark.asyncio
    async def test_a_learner_who_opted_out_is_not_pushed(self, wire):
        repo = FakeRepo(due=[_row()])
        sends = wire(repo, push_allowed=False, push_result={"sent": 1})

        assert await svc.deliver_pending() == 1
        assert sends == []
        # The in-app notification still stands. Push is an extra channel, not the delivery.
        assert repo.status_updates[0]["status"] == "DELIVERED"

    @pytest.mark.asyncio
    async def test_a_failing_push_does_not_undo_the_delivery(self, wire, monkeypatch):
        repo = FakeRepo(due=[_row()])
        wire(repo)

        async def _boom(**_kw):
            raise RuntimeError("fcm down")

        monkeypatch.setattr(
            "src.shared.infrastructure.push_notifications.send_push_notification", _boom
        )

        assert await svc.deliver_pending() == 1
        assert repo.status_updates[0]["status"] == "DELIVERED"


class TestTheDeliveryQuery:
    """The filter that decides what counts as due lives in SQL, so it is asserted against the statement.

    A fake repository can only prove the service handles the rows it is given. It cannot catch `QUEUED`
    being dropped from the query, which is the exact regression that made every quiet-hours notification
    disappear in the first place.
    """

    @pytest.mark.asyncio
    async def test_queued_and_pending_are_both_due(self):
        from src.domains.personal_learning.repository import personal_learning_repo

        captured: list = []

        class _Session:
            async def execute(self, stmt):
                captured.append(stmt)
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        await personal_learning_repo.list_due_for_delivery(limit=7, session=_Session())

        sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert "'PENDING'" in sql
        assert "'QUEUED'" in sql
        assert '"scheduledAt" <=' in sql
        assert "LIMIT 7" in sql


class TestPushConsent:
    """`UserPreferences.notifications`, `pushScheduleReminder` and `pushStudyTips` have existed unread for
    the whole life of the schema. Starting to send push without consulting them would turn a dormant column
    into a broken promise."""

    async def _allowed(self, monkeypatch, prefs, notification_type):
        class _Session:
            async def execute(self, _stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: prefs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        monkeypatch.setattr(
            "src.shared.database.get_session_factory", lambda: lambda: _Session()
        )
        return await svc._push_allowed("user-1", notification_type)

    @pytest.mark.asyncio
    async def test_the_master_switch_governs_everything(self, monkeypatch):
        prefs = SimpleNamespace(notifications=False, push_schedule_reminder=True, push_study_tips=True)
        assert await self._allowed(monkeypatch, prefs, "goal_at_risk") is False

    @pytest.mark.asyncio
    async def test_a_type_the_learner_muted_is_not_pushed(self, monkeypatch):
        prefs = SimpleNamespace(notifications=True, push_schedule_reminder=False, push_study_tips=True)
        assert await self._allowed(monkeypatch, prefs, "study_plan_check_in") is False

    @pytest.mark.asyncio
    async def test_a_type_that_toggle_allows_is_pushed(self, monkeypatch):
        prefs = SimpleNamespace(notifications=True, push_schedule_reminder=True, push_study_tips=False)
        assert await self._allowed(monkeypatch, prefs, "study_plan_check_in") is True

    @pytest.mark.asyncio
    async def test_a_type_no_toggle_describes_is_allowed(self, monkeypatch):
        """Mapping `goal_at_risk` onto "study tips" would be reading consent into an answer the learner
        never gave about it."""
        prefs = SimpleNamespace(notifications=True, push_schedule_reminder=False, push_study_tips=False)
        assert await self._allowed(monkeypatch, prefs, "goal_at_risk") is True

    @pytest.mark.asyncio
    async def test_no_preferences_row_is_not_consent(self, monkeypatch):
        assert await self._allowed(monkeypatch, None, "goal_at_risk") is False

    @pytest.mark.asyncio
    async def test_it_fails_closed_when_preferences_cannot_be_read(self, monkeypatch):
        """The opposite of `parse_hhmm`, and for the opposite reason: an unsent push costs the learner
        nothing, because the notification is already in their list."""

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr("src.shared.database.get_session_factory", _boom)
        assert await svc._push_allowed("user-1", "goal_at_risk") is False
