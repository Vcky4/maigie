"""The nightly ladder that acts on a goal falling behind (no DB required).

**This is the defect these tests exist for.** `is_at_risk`, `is_due_soon` and `is_overdue` were all
written, all pure, and all read only when a learner opened a page. So a goal drifted past its deadline and
the only thing that noticed was a label on a screen the learner had stopped visiting. Nothing ever looked.

What is pinned here is mostly what the ladder *refuses* to do, because that is where the harm lives. A pass
that escalates twice in a week is a notification loop. A pass that extends without a cap is a goal that
cannot fail. A pass that invents a deadline when it has no evidence is the system making up a commitment on
the learner's behalf. A pass that moves an exam date is lying about the world.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from src.domains.progress.services import goal_lifecycle_service as svc  # noqa: E402
from src.domains.progress.services import goal_metrics  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _goal(**overrides) -> SimpleNamespace:
    """A goal 30 days into a 40-day window, so it is well past half its time."""
    defaults = {
        "id": "goal-1",
        "user_id": "user-1",
        "title": "Finish linear algebra",
        "status": "ACTIVE",
        "metric_kind": "course_progress",
        "current_value": None,
        "target_value": 100.0,
        "course_id": "course-1",
        "topic_id": None,
        "prep_id": None,
        "created_at": NOW - timedelta(days=30),
        "target_date": NOW + timedelta(days=3),
        "progress": 0.0,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _history(extended=0, system_extended=0, original=None) -> goal_metrics.GoalScheduleHistory:
    return goal_metrics.GoalScheduleHistory(
        extended_count=extended,
        system_extended_count=system_extended,
        original_target_date=original,
    )


def _snapshots(*pairs) -> list[SimpleNamespace]:
    """`(days_ago, progress)` pairs, oldest first, as the snapshot rows the sizer reads."""
    return [
        SimpleNamespace(captured_on=(NOW - timedelta(days=ago)).date(), progress=progress)
        for ago, progress in pairs
    ]


class Recorder:
    """Captures every write the ladder makes, so a test can assert what it did and did not do."""

    def __init__(self, *, snapshots=None, suppress_notification=False, notify_raises=False):
        self.goal_updates: list[tuple[str, dict]] = []
        self.actions: list[dict] = []
        self.schedule_changes: list[dict] = []
        self.notifications: list[dict] = []
        self.created_goals: list[dict] = []
        self.snapshots = snapshots if snapshots is not None else _snapshots((14, 20.0), (0, 50.0))
        self.suppress_notification = suppress_notification
        self.notify_raises = notify_raises
        self.action_raises = False

    async def update_goal(self, goal_id, data):
        self.goal_updates.append((goal_id, data))
        return None

    async def create_lifecycle_action(self, data):
        if self.action_raises:
            raise RuntimeError("cooldown row failed")
        self.actions.append(data)
        return SimpleNamespace(id="a1")

    async def create_schedule_change(self, data):
        self.schedule_changes.append(data)
        return SimpleNamespace(id="c1")

    async def create_goal(self, data):
        self.created_goals.append(data)
        raise AssertionError("the ladder must never create a goal")

    async def list_history(self, *, user_id, goal_id, since, until):
        return list(self.snapshots)

    async def create_notification(self, **kwargs):
        if self.notify_raises:
            raise RuntimeError("notification exploded")
        self.notifications.append(kwargs)
        return None if self.suppress_notification else SimpleNamespace(id="n1")


@pytest.fixture
def rec(monkeypatch):
    def _wire(**kwargs):
        recorder = Recorder(**kwargs)
        monkeypatch.setattr(svc.progress_repo, "update_goal", recorder.update_goal)
        monkeypatch.setattr(
            svc.progress_repo, "create_lifecycle_action", recorder.create_lifecycle_action
        )
        monkeypatch.setattr(svc.progress_repo, "create_goal", recorder.create_goal)
        # The audit trail is written through `goal_schedule_log`, which holds its own repo reference.
        monkeypatch.setattr(
            "src.domains.progress.services.goal_schedule_log.progress_repo.create_schedule_change",
            recorder.create_schedule_change,
        )
        # Both are imported inside the function, so the module is patched rather than this module's
        # attribute — a local `from . import x` rebinds and would ignore the latter.
        monkeypatch.setattr(
            "src.domains.progress.services.goal_snapshot_service.list_history",
            recorder.list_history,
        )
        monkeypatch.setattr(
            "src.domains.personal_learning.services.notification_service.create_notification",
            recorder.create_notification,
        )
        return recorder

    return _wire


async def _act(goal, *, progress, history=None, now=NOW):
    return await svc._act_on(goal, progress=progress, history=history or _history(), now=now)


# ===========================================================================
# Which rung fires
# ===========================================================================


class TestTheLadder:
    @pytest.mark.asyncio
    async def test_a_goal_on_track_is_left_alone(self, rec):
        recorder = rec()
        # 80% with three days left on a 40-day window: ahead of its own pace.
        assert await _act(_goal(), progress=80.0) is None
        assert recorder.actions == []
        assert recorder.notifications == []

    @pytest.mark.asyncio
    async def test_a_finished_goal_is_left_alone(self, rec):
        """Progress at 100 with the deadline passed is not a goal in trouble, it is a goal nobody has
        marked complete. Chasing it would be telling the learner to do what they have already done."""
        recorder = rec()
        assert await _act(_goal(target_date=NOW - timedelta(days=2)), progress=100.0) is None
        assert recorder.actions == []

    @pytest.mark.asyncio
    async def test_behind_with_the_deadline_close_extends_the_learners_own_date(self, rec):
        recorder = rec()
        assert await _act(_goal(), progress=30.0) == "extended"
        assert recorder.actions[0]["trigger"] == "at_risk_due_soon"

    @pytest.mark.asyncio
    async def test_a_passed_deadline_extends_and_says_so(self, rec):
        recorder = rec()
        goal = _goal(target_date=NOW - timedelta(days=2))
        assert await _act(goal, progress=30.0) == "extended"
        assert recorder.actions[0]["trigger"] == "deadline_passed"

    @pytest.mark.asyncio
    async def test_an_exam_deadline_is_never_moved(self, rec):
        """The exam is on the 15th. All the ladder can do is say the real numbers."""
        recorder = rec()
        goal = _goal(prep_id="prep-1", metric_kind="prep_readiness")

        assert await _act(goal, progress=30.0) == "warned"

        assert recorder.goal_updates == []
        assert recorder.schedule_changes == []
        assert "30%" in recorder.notifications[0]["body"]
        assert "3 day" in recorder.notifications[0]["title"]

    @pytest.mark.asyncio
    async def test_a_passed_exam_is_left_to_the_post_exam_review(self, rec):
        """`mark_preparations_awaiting_review` has already asked how it went. Asking again here, in
        different words, from a different surface, about the same exam, would read as the system not
        knowing what it had already said."""
        recorder = rec()
        goal = _goal(
            prep_id="prep-1", metric_kind="prep_readiness", target_date=NOW - timedelta(days=2)
        )

        assert await _act(goal, progress=30.0) is None

        assert recorder.actions == []
        assert recorder.notifications == []

    @pytest.mark.asyncio
    async def test_behind_with_time_still_left_is_not_this_passs_problem(self, rec):
        """Answered by compressing the plan, which `redistribute_drifted_plans` owns and triggers from
        actual item drift. Acting here as well would bypass that sweep's cooldown and bring back the
        nightly churn it exists to prevent."""
        recorder = rec()
        far = _goal(target_date=NOW + timedelta(days=60), created_at=NOW - timedelta(days=60))

        assert await _act(far, progress=10.0) is None
        assert recorder.actions == []


# ===========================================================================
# The extension budget
# ===========================================================================


class TestTheBudget:
    @pytest.mark.asyncio
    async def test_a_spent_budget_asks_instead_of_extending(self, rec):
        """Without a cap this is a goal that cannot fail: every deadline it misses buys it a new one."""
        recorder = rec()
        history = _history(extended=3, system_extended=svc.MAX_SYSTEM_EXTENSIONS)

        assert await _act(_goal(), progress=30.0, history=history) == "asked_to_confirm"

        assert recorder.goal_updates == []
        assert "3 times" in recorder.notifications[0]["body"]

    @pytest.mark.asyncio
    async def test_the_learners_own_edits_do_not_spend_the_systems_budget(self, rec):
        """A learner moving their own deadline is stating a new intention. Charging it to the budget would
        mean refusing to help someone for having re-planned."""
        rec()
        history = _history(extended=9, system_extended=0)

        assert await _act(_goal(), progress=30.0, history=history) == "extended"

    @pytest.mark.asyncio
    async def test_one_under_the_cap_still_extends(self, rec):
        rec()
        history = _history(system_extended=svc.MAX_SYSTEM_EXTENSIONS - 1)
        assert await _act(_goal(), progress=30.0, history=history) == "extended"


# ===========================================================================
# Sizing the extension
# ===========================================================================


class TestSizingIsMeasured:
    @pytest.mark.asyncio
    async def test_the_new_date_comes_from_the_observed_rate(self, rec):
        """30 points over 15 days is 2 points a day; 70 points remain, so 35 days. Not a fixed guess."""
        recorder = rec(snapshots=_snapshots((15, 0.0), (0, 30.0)))
        goal = _goal(created_at=NOW - timedelta(days=400), target_date=NOW + timedelta(days=3))

        await _act(goal, progress=30.0)

        new_date = recorder.goal_updates[0][1]["targetDate"]
        assert (new_date - NOW).days == 35

    @pytest.mark.asyncio
    async def test_an_extension_may_at_most_double_the_original_window(self, rec):
        """A rate of a fraction of a point a day would otherwise produce a date years out."""
        recorder = rec(snapshots=_snapshots((14, 0.0), (0, 0.7)))
        goal = _goal(created_at=NOW - timedelta(days=30), target_date=NOW + timedelta(days=3))

        await _act(goal, progress=0.7)

        new_date = recorder.goal_updates[0][1]["targetDate"]
        # The original window is 33 days, so that is the ceiling — not the ~1900 days the rate implies.
        assert (new_date - NOW).days == 33

    @pytest.mark.asyncio
    async def test_the_cap_uses_the_original_window_so_extensions_cannot_compound(self, rec):
        """Read from the schedule history, not from `targetDate`, which is the already-extended column.
        Otherwise each extension doubles a window the last one had already doubled."""
        recorder = rec(snapshots=_snapshots((14, 0.0), (0, 0.5)))
        goal = _goal(
            created_at=NOW - timedelta(days=20),
            # Twice extended and still overdue, which is exactly when the third one is considered.
            target_date=NOW - timedelta(days=1),
        )
        history = _history(
            extended=2, system_extended=2, original=NOW - timedelta(days=10)
        )

        await _act(goal, progress=0.5, history=history)

        new_date = recorder.goal_updates[0][1]["targetDate"]
        # Original window was createdAt -> original target: 10 days. Not 220.
        assert (new_date - NOW).days == 10

    @pytest.mark.asyncio
    async def test_no_recorded_progress_means_asking_not_guessing(self, rec):
        """A goal at 0% for a fortnight has no rate, and 0 is not a number you can divide by to get a
        deadline."""
        recorder = rec(snapshots=_snapshots((14, 0.0), (0, 0.0)))

        assert await _act(_goal(), progress=0.0) == "asked_to_confirm"
        assert recorder.goal_updates == []

    @pytest.mark.asyncio
    async def test_going_backwards_means_asking(self, rec):
        rec(snapshots=_snapshots((14, 40.0), (0, 20.0)))
        assert await _act(_goal(), progress=20.0) == "asked_to_confirm"

    @pytest.mark.asyncio
    async def test_a_single_recorded_day_is_not_a_rate(self, rec):
        rec(snapshots=_snapshots((0, 30.0)))
        assert await _act(_goal(), progress=30.0) == "asked_to_confirm"

    @pytest.mark.asyncio
    async def test_no_history_at_all_means_asking(self, rec):
        rec(snapshots=[])
        assert await _act(_goal(), progress=30.0) == "asked_to_confirm"

    @pytest.mark.asyncio
    async def test_two_rows_on_the_same_day_are_not_a_span(self, rec):
        rec(snapshots=_snapshots((0, 10.0), (0, 30.0)))
        assert await _act(_goal(), progress=30.0) == "asked_to_confirm"


# ===========================================================================
# What gets written
# ===========================================================================


class TestRecording:
    @pytest.mark.asyncio
    async def test_an_extension_lands_in_both_logs(self, rec):
        """The audit trail is what `extendedCount` publishes, so a deadline the system moved must appear
        there or the field under-reports. The action log is the cooldown, so it must appear there or the
        goal is extended again tomorrow."""
        recorder = rec()

        await _act(_goal(), progress=30.0)

        assert recorder.schedule_changes[0]["reason"] == "system_extended"
        assert recorder.schedule_changes[0]["dateAuthority"] == "learner"
        assert recorder.actions[0]["action"] == "extended"
        assert recorder.actions[0]["goalId"] == "goal-1"
        assert recorder.actions[0]["userId"] == "user-1"

    @pytest.mark.asyncio
    async def test_the_audit_row_records_the_move_not_a_no_op(self, rec):
        recorder = rec()
        goal = _goal()

        await _act(goal, progress=30.0)

        change = recorder.schedule_changes[0]
        assert change["previousDate"] == goal.target_date
        assert change["newDate"] > goal.target_date

    @pytest.mark.asyncio
    async def test_a_suppressed_notification_still_leaves_the_action_recorded(self, rec):
        """`create_notification` returns `None` under quiet hours or the daily cap. Counting delivered
        messages would re-escalate the same goal every night — the trap the preparation ask and the weekly
        check-in each had to close."""
        recorder = rec(suppress_notification=True)

        assert await _act(_goal(), progress=30.0) == "extended"
        assert len(recorder.actions) == 1

    @pytest.mark.asyncio
    async def test_a_failing_notification_does_not_undo_the_action(self, rec):
        recorder = rec(notify_raises=True)

        assert await _act(_goal(), progress=30.0) == "extended"
        assert len(recorder.actions) == 1
        assert len(recorder.goal_updates) == 1

    @pytest.mark.asyncio
    async def test_a_failing_action_row_fails_the_whole_action(self, rec):
        """The opposite call from the audit log, which swallows its failures so a missing row cannot
        reject a learner's edit. Here the row *is* the cooldown, so an action taken without one repeats
        tomorrow and every night after. Better to lose one night's escalation than to start a loop."""
        recorder = rec()
        recorder.action_raises = True

        with pytest.raises(RuntimeError):
            await _act(_goal(), progress=30.0)

        assert recorder.notifications == []


# ===========================================================================
# The pass itself
# ===========================================================================


class TestThePass:
    async def _review(self, goals, recorder, *, history=None, progress=30.0):
        asked: dict = {}

        async def _list(**kwargs):
            asked.update(kwargs)
            return goals

        async def _measure(goal_list, *, now=None):
            return {
                g.id: goal_metrics.GoalMeasurement(current_value=progress, measured=True)
                for g in goal_list
            }

        async def _history(goal_ids):
            return history or {}

        with (
            patch.object(svc.progress_repo, "list_goals_for_lifecycle_review", _list),
            patch.object(goal_metrics, "derive_current_values", _measure),
            patch.object(goal_metrics, "derive_schedule_history", _history),
            patch.object(goal_metrics, "derived_progress", lambda g, m: progress),
        ):
            counts = await svc.review_goals(now=NOW)
        return counts, asked

    @pytest.mark.asyncio
    async def test_the_cooldown_and_horizon_are_what_the_database_is_asked_for(self, rec):
        rec()
        _, asked = await self._review([], Recorder())

        assert asked["not_acted_since"] == NOW - timedelta(days=svc.GOAL_ACTION_COOLDOWN_DAYS)
        assert asked["horizon"] == NOW + timedelta(days=goal_metrics.DUE_SOON_DAYS)

    @pytest.mark.asyncio
    async def test_each_goal_gets_at_most_one_action(self, rec):
        recorder = rec()
        goals = [_goal(id=f"g{n}") for n in range(3)]

        counts, _ = await self._review(goals, recorder)

        assert counts == {"extended": 3}
        assert len(recorder.actions) == 3

    @pytest.mark.asyncio
    async def test_one_bad_goal_does_not_end_the_run(self, rec):
        recorder = rec()
        goals = [_goal(id="g0"), _goal(id="g1", user_id=None), _goal(id="g2")]

        async def _explode(data):
            if data["goalId"] == "g1":
                raise RuntimeError("boom")
            recorder.actions.append(data)
            return SimpleNamespace(id="a")

        with patch.object(svc.progress_repo, "create_lifecycle_action", _explode):
            counts, _ = await self._review(goals, recorder)

        assert counts == {"extended": 2}

    @pytest.mark.asyncio
    async def test_it_never_creates_a_goal(self, rec):
        """`delete_goal` is a hard DELETE, so a sweep that created goals would resurrect one the learner
        deliberately threw away, every night, with no way to make it stop."""
        recorder = rec()

        await self._review([_goal()], recorder)

        assert recorder.created_goals == []

    @pytest.mark.asyncio
    async def test_nothing_to_review_asks_for_no_measurements(self, rec):
        rec()
        counts, _ = await self._review([], Recorder())
        assert counts == {}


class TestTheQueryItself:
    """The filters that bound the pass live in SQL, so they are asserted against the statement."""

    @pytest.mark.asyncio
    async def test_the_filters_are_in_the_statement(self):
        from src.domains.progress.repository import progress_repo

        captured: list = []

        class _Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        class _Session:
            async def execute(self, stmt):
                captured.append(stmt)
                return _Result()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        async def _session():
            return _Session()

        with patch.object(progress_repo, "_session", _session):
            await progress_repo.list_goals_for_lifecycle_review(
                now=NOW,
                horizon=NOW + timedelta(days=7),
                not_acted_since=NOW - timedelta(days=7),
            )

        sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert "'ACTIVE'" in sql
        assert '"targetDate" IS NOT NULL' in sql
        assert '"targetDate" <' in sql
        # The cooldown, as a NOT EXISTS against the action log rather than a stamp on the goal.
        assert "NOT (EXISTS" in sql
        assert '"GoalLifecycleAction"' in sql


class TestTheActionTokens:
    def test_they_match_the_database_constraint(self):
        from src.domains.progress.db_models import GoalLifecycleAction

        for name, tokens in (
            ("GoalLifecycleAction_action_check", GoalLifecycleAction.ACTIONS),
            ("GoalLifecycleAction_trigger_check", GoalLifecycleAction.TRIGGERS),
        ):
            check = next(
                c
                for c in GoalLifecycleAction.__table__.constraints
                if getattr(c, "name", None) == name
            )
            sql = str(check.sqltext)
            for token in tokens:
                assert f"'{token}'" in sql, (name, token)
            assert sql.count("'") // 2 == len(tokens), name

    def test_there_are_no_response_columns_without_a_question(self):
        """Whether the learner wants to deprioritise, keep going, or says it is already done is the most
        valuable thing this table could hold, and nothing asks for it yet. The columns arrive with the
        question."""
        from src.domains.progress.db_models import GoalLifecycleAction

        columns = {c.name for c in GoalLifecycleAction.__table__.columns}
        assert not columns & {"learnerResponse", "respondedAt", "outcome", "outcomeAt"}
