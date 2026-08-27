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
        """A notification that reaches nobody must not reopen the cooldown. Quiet hours hold a message until
        morning, the learner's daily allowance can defer it, and one held too long expires — and before
        phase 5 the allowance destroyed it outright, returning `None`, which is what this fake reproduces as
        the strictest case. Counting messages that landed would re-escalate the same goal every night."""
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

    def test_the_response_columns_arrived_with_something_that_writes_them(self):
        """They were withheld while nothing could answer — a column the schema offers that nothing can fill
        is the accept-and-ignore defect this codebase keeps closing. Now that the endpoint exists, this pins
        the rule rather than the old snapshot: the columns are present *and* reachable."""
        from src.domains.progress.db_models import GoalLifecycleAction
        from src.domains.progress.repository import progress_repo

        columns = {c.name for c in GoalLifecycleAction.__table__.columns}
        assert {"learnerResponse", "respondedAt"} <= columns
        assert "learnerResponse" in progress_repo._LIFECYCLE_ACTION_FIELD_MAP
        assert hasattr(progress_repo, "record_lifecycle_response")

    def test_the_response_tokens_match_the_database_constraint(self):
        from src.domains.progress.db_models import GoalLifecycleAction

        check = next(
            c
            for c in GoalLifecycleAction.__table__.constraints
            if getattr(c, "name", None) == "GoalLifecycleAction_learnerResponse_check"
        )
        sql = str(check.sqltext)
        for token in GoalLifecycleAction.RESPONSES:
            assert f"'{token}'" in sql, token
        assert sql.count("'") // 2 == len(GoalLifecycleAction.RESPONSES)

    def test_every_response_token_is_something_the_service_can_act_on(self):
        """A token the database accepts and the service does not understand would be a 422 on a valid
        answer, or worse, an answer stored and ignored."""
        from src.domains.progress.db_models import GoalLifecycleAction

        assert set(GoalLifecycleAction.RESPONSES) == set(svc._STATUS_FOR_RESPONSE)


# ===========================================================================
# The learner's answer
# ===========================================================================


class TestTheAnswer:
    """**The write that makes the ladder capable of improving.** Everything else records what the system
    decided; this records whether the decision was any good. Without it every future version of this
    escalation guesses at the same rate as the first — which is the state
    `retention_service.record_intervention_outcome` has been in since it was written, with zero callers.
    """

    def _wire(self, monkeypatch, *, goal, action):
        recorded: list[dict] = []
        updates: list[tuple[str, dict]] = []
        state = {"goal": goal}

        async def _find_goal(goal_id, user_id):
            g = state["goal"]
            if g is None or g.id != goal_id or g.user_id != user_id:
                return None
            return g

        async def _find_action(_goal_id):
            return action

        async def _record(action_id, *, response, responded_at):
            recorded.append(
                {"action_id": action_id, "response": response, "responded_at": responded_at}
            )

        async def _update(goal_id, data):
            updates.append((goal_id, data))
            if "status" in data:
                state["goal"] = SimpleNamespace(
                    **{**vars(state["goal"]), "status": data["status"]}
                )
            return state["goal"]

        monkeypatch.setattr(svc.progress_repo, "find_goal", _find_goal)
        monkeypatch.setattr(svc.progress_repo, "find_latest_lifecycle_action", _find_action)
        monkeypatch.setattr(svc.progress_repo, "record_lifecycle_response", _record)
        monkeypatch.setattr(svc.progress_repo, "update_goal", _update)
        return recorded, updates

    @pytest.mark.asyncio
    async def test_keep_going_stores_the_answer_and_changes_nothing_else(self, monkeypatch):
        """The learner asked for the goal exactly as it is. Touching its status would be the system doing
        something in response to being told to do nothing."""
        goal = _goal(status="ACTIVE")
        action = SimpleNamespace(id="a1", action="asked_to_confirm")
        recorded, updates = self._wire(monkeypatch, goal=goal, action=action)

        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="keep_going")

        assert recorded[0]["response"] == "keep_going"
        assert recorded[0]["action_id"] == "a1"
        assert recorded[0]["responded_at"] is not None
        assert updates == []

    @pytest.mark.asyncio
    async def test_setting_it_aside_archives_the_goal(self, monkeypatch):
        """`ARCHIVED` rather than a new paused state: it is the only existing value meaning "concluded
        without being achieved", it is what `prep_outcome_service` already does for an unmet preparation
        goal, and it takes the goal out of the at-risk counts — which is what "stop chasing me" means."""
        goal = _goal(status="ACTIVE")
        action = SimpleNamespace(id="a1", action="asked_to_confirm")
        recorded, updates = self._wire(monkeypatch, goal=goal, action=action)

        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="set_aside")

        assert recorded[0]["response"] == "set_aside"
        assert updates == [("goal-1", {"status": "ARCHIVED"})]

    @pytest.mark.asyncio
    async def test_already_done_completes_it(self, monkeypatch):
        """The answer worth the most: it says the measurement is wrong rather than the learner."""
        goal = _goal(status="ACTIVE")
        action = SimpleNamespace(id="a1", action="warned")
        _, updates = self._wire(monkeypatch, goal=goal, action=action)

        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="already_done")

        assert updates == [("goal-1", {"status": "COMPLETED"})]

    @pytest.mark.asyncio
    async def test_an_answer_that_changes_nothing_writes_no_status(self, monkeypatch):
        """Already archived, and they say set it aside again. The answer is still recorded — it is data about
        the ask — but the goal is not rewritten to the value it already holds."""
        goal = _goal(status="ARCHIVED")
        action = SimpleNamespace(id="a1", action="asked_to_confirm")
        recorded, updates = self._wire(monkeypatch, goal=goal, action=action)

        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="set_aside")

        assert len(recorded) == 1
        assert updates == []

    @pytest.mark.asyncio
    async def test_changing_their_mind_replaces_the_answer(self, monkeypatch):
        """A learner who says "keep going" on Monday and "set it aside" on Thursday has changed their mind,
        and the last word is the one that counts."""
        goal = _goal(status="ACTIVE")
        action = SimpleNamespace(id="a1", action="asked_to_confirm")
        recorded, updates = self._wire(monkeypatch, goal=goal, action=action)

        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="keep_going")
        await svc.record_answer(user_id="user-1", goal_id="goal-1", response="set_aside")

        assert [r["response"] for r in recorded] == ["keep_going", "set_aside"]
        assert updates == [("goal-1", {"status": "ARCHIVED"})]

    @pytest.mark.asyncio
    async def test_answering_a_question_nobody_asked_is_a_404(self, monkeypatch):
        """This route answers a nudge. Changing a goal nobody asked about is what `PATCH` is for, and
        accepting it here would let a client record a reply to a nudge that never happened."""
        from src.shared.exceptions import NotFoundError

        goal = _goal()
        self._wire(monkeypatch, goal=goal, action=None)

        with pytest.raises(NotFoundError):
            await svc.record_answer(user_id="user-1", goal_id="goal-1", response="keep_going")

    @pytest.mark.asyncio
    async def test_another_learners_goal_is_not_found(self, monkeypatch):
        from src.shared.exceptions import NotFoundError

        goal = _goal(user_id="someone-else")
        self._wire(monkeypatch, goal=goal, action=SimpleNamespace(id="a1", action="warned"))

        with pytest.raises(NotFoundError):
            await svc.record_answer(user_id="user-1", goal_id="goal-1", response="keep_going")

    @pytest.mark.asyncio
    async def test_an_unknown_answer_is_refused(self, monkeypatch):
        from src.shared.exceptions import ValidationError

        goal = _goal()
        recorded, _ = self._wire(
            monkeypatch, goal=goal, action=SimpleNamespace(id="a1", action="warned")
        )

        with pytest.raises(ValidationError):
            await svc.record_answer(user_id="user-1", goal_id="goal-1", response="maybe")

        assert recorded == []

    @pytest.mark.asyncio
    async def test_the_answer_is_stored_before_the_goal_is_touched(self, monkeypatch):
        """The answer is the thing worth keeping. If archiving fails, the reply must still be on record —
        losing it means losing the only evidence about whether the ask worked."""
        goal = _goal(status="ACTIVE")
        action = SimpleNamespace(id="a1", action="asked_to_confirm")
        recorded, _ = self._wire(monkeypatch, goal=goal, action=action)

        async def _boom(*_a, **_k):
            raise RuntimeError("status write failed")

        monkeypatch.setattr(svc.progress_repo, "update_goal", _boom)

        with pytest.raises(RuntimeError):
            await svc.record_answer(user_id="user-1", goal_id="goal-1", response="set_aside")

        assert recorded[0]["response"] == "set_aside"


class TestTheQuestionIsReachable:
    """A notification can be held until morning, deferred to the next day by the learner's daily allowance,
    or expire before it is read. So a goal waiting on an answer has to say so somewhere the learner can find
    on their own — the same argument that put `AWAITING_REVIEW` on the prepare dashboard.
    """

    @pytest.mark.asyncio
    async def test_only_the_most_recent_action_counts_and_only_while_unanswered(self):
        from src.domains.progress.repository import progress_repo

        captured: list = []

        class _Session:
            async def execute(self, stmt):
                captured.append(stmt)
                return SimpleNamespace(all=lambda: [])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        async def _session():
            return _Session()

        with patch.object(progress_repo, "_session", _session):
            await progress_repo.latest_unanswered_actions(["goal-1"])

        sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert '"learnerResponse" IS NULL' in sql
        # The newest row per goal, so a superseded nudge is not presented as a live question.
        assert "max(" in sql

    @pytest.mark.asyncio
    async def test_no_goals_asks_the_database_nothing(self):
        from src.domains.progress.repository import progress_repo

        called = {"n": 0}

        async def _session():
            called["n"] += 1
            raise AssertionError("no query should be issued for an empty goal list")

        with patch.object(progress_repo, "_session", _session):
            assert await progress_repo.latest_unanswered_actions([]) == {}
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_the_answer_attaches_to_the_newest_action_not_the_oldest(self):
        """A learner with three nudges on one goal is answering the last one. Reading the oldest would file
        their reply against a question the system had already moved past — and then the *current* nudge would
        still read as unanswered, so they would be asked again."""
        from src.domains.progress.repository import progress_repo

        captured: list = []

        class _Session:
            async def execute(self, stmt):
                captured.append(stmt)
                return SimpleNamespace(scalar_one_or_none=lambda: None)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        async def _session():
            return _Session()

        with patch.object(progress_repo, "_session", _session):
            await progress_repo.find_latest_lifecycle_action("goal-1")

        sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert '"createdAt" DESC' in sql
        assert "LIMIT 1" in sql
