"""A goal's deadline moving: who was allowed to move it, and whether anyone can tell that it did.

Two defects these pin, both of which are invisible by construction.

**A goal that extends its own deadline marks itself healthy.** `elapsed_percent` measures the window as
`createdAt → targetDate`, so pushing the deadline out enlarges the denominator, shrinks elapsed percent,
shrinks the lag `is_at_risk` tests, and the goal reports itself on track for having been given more time.
Nothing in the row distinguishes a goal that was always due in December from one that was due in August
and has been rewritten twice. `GoalScheduleChange` is that distinction, and `extendedCount` is the number
that makes it visible — so the tests here are mostly about it counting the *right* things, because a count
that includes ordinary saves is a warning light that is always on.

**Date authority decides what falling behind can be answered with.** An exam is on the 15th; a course
deadline was always an intention. Derived from the link rather than stored, so the tests pin the
derivation against the trap of reading `metricKind` instead — a `prep_readiness` goal with no `prepId` has
no exam date to be external to.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from src.domains.progress.services import goal_metrics, goal_schedule_log  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _goal(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "goal-1",
        "user_id": "user-1",
        "metric_kind": "manual",
        "course_id": None,
        "topic_id": None,
        "prep_id": None,
        "created_at": NOW - timedelta(days=30),
        "target_date": NOW + timedelta(days=10),
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _rows_session(rows: list):
    """An async-context session whose one query returns `rows` from `.all()`."""

    class _Result:
        def all(self):
            return rows

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    return _Session()


# ===========================================================================
# Date authority
# ===========================================================================


class TestDateAuthority:
    def test_a_goal_attached_to_a_preparation_is_external(self):
        """The exam is on the 15th. Nothing the learner does moves it."""
        assert goal_metrics.date_authority(_goal(prep_id="prep-1")) == "external"

    def test_a_course_goal_is_the_learners_own_date(self):
        assert goal_metrics.date_authority(_goal(course_id="course-1")) == "learner"

    def test_a_goal_with_no_link_is_the_learners_own_date(self):
        assert goal_metrics.date_authority(_goal()) == "learner"

    def test_a_goal_with_no_deadline_at_all_is_still_the_learners(self):
        """Not a third token. An absent deadline behaves exactly like the learner's own: freely settable."""
        assert goal_metrics.date_authority(_goal(target_date=None)) == "learner"

    def test_the_metric_kind_is_not_what_decides_it(self):
        """The trap. The four links are independent nullable columns and nothing enforces that a
        `prep_readiness` goal carries a `prepId` — so reading the kind would call this goal external on
        the strength of a label while the row holds no exam date to be external to."""
        goal = _goal(metric_kind="prep_readiness", prep_id=None)
        assert goal_metrics.date_authority(goal) == "learner"

    def test_a_prep_link_wins_over_a_course_link(self):
        """Nothing enforces mutual exclusivity between the links. If a row ever carries both, the exam
        date is the one that cannot move, so it is the one that decides."""
        goal = _goal(prep_id="prep-1", course_id="course-1")
        assert goal_metrics.date_authority(goal) == "external"

    def test_it_is_never_stored(self):
        """Derived, so there is no column to disagree with the link. A field map that accepted it would
        be the beginning of `Goal.progress` all over again — a stored copy of a derived figure."""
        from src.domains.progress.repository import progress_repo

        assert "dateAuthority" not in progress_repo._GOAL_FIELD_MAP
        from src.domains.progress.db_models import Goal

        assert not hasattr(Goal, "date_authority")


# ===========================================================================
# The extension count
# ===========================================================================


def _change(previous, new, reason="learner_edited") -> tuple:
    return ("goal-1", previous, new, reason)


class TestDeriveScheduleHistory:
    @pytest.mark.asyncio
    async def test_no_goals_asks_the_database_nothing(self):
        with patch.object(goal_metrics, "get_session_factory") as factory:
            assert await goal_metrics.derive_schedule_history([]) == {}
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_goal_with_no_recorded_change_is_absent(self):
        """Absent rather than present with zeros, matching `count_achieved_milestones`. The caller
        defaults it, so "never moved" and "moved zero times" do not need to be different things."""
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session([])):
            assert await goal_metrics.derive_schedule_history(["goal-1"]) == {}

    @pytest.mark.asyncio
    async def test_a_deadline_pushed_later_is_an_extension(self):
        rows = [_change(NOW, NOW + timedelta(days=14))]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 1
        assert history["goal-1"].original_target_date == NOW

    @pytest.mark.asyncio
    async def test_a_deadline_pulled_earlier_is_not_an_extension(self):
        """A learner bringing a deadline forward has not bought themselves room, and counting it would
        make the one number that means "this goal has been given more time" mean "this goal was edited".
        """
        rows = [_change(NOW + timedelta(days=14), NOW)]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 0

    @pytest.mark.asyncio
    async def test_setting_a_first_deadline_is_not_an_extension(self):
        """A goal that had no deadline was not late. Treating the absence as an infinitely early date
        would make every goal that ever gained a deadline read as extended."""
        rows = [_change(None, NOW + timedelta(days=14))]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 0
        assert history["goal-1"].original_target_date is None

    @pytest.mark.asyncio
    async def test_clearing_a_deadline_is_not_an_extension(self):
        rows = [_change(NOW, None)]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 0

    @pytest.mark.asyncio
    async def test_every_push_counts_not_just_the_last(self):
        rows = [
            _change(NOW, NOW + timedelta(days=7)),
            _change(NOW + timedelta(days=7), NOW + timedelta(days=21)),
            _change(NOW + timedelta(days=21), NOW + timedelta(days=60)),
        ]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 3

    @pytest.mark.asyncio
    async def test_the_original_deadline_is_the_earliest_rows_not_the_minimum(self):
        """The reason this is aggregated in memory rather than as `min(previousDate)` in SQL.

        This goal was pulled forward and then pushed out twice. The minimum previous date is the
        mid-August one, which was never the window the goal started with — it started in October.
        """
        october = datetime(2026, 10, 1, tzinfo=UTC)
        august = datetime(2026, 8, 15, tzinfo=UTC)
        rows = [
            _change(october, august),
            _change(august, datetime(2026, 11, 1, tzinfo=UTC)),
            _change(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
        ]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].original_target_date == october
        assert history["goal-1"].extended_count == 2

    @pytest.mark.asyncio
    async def test_naive_stored_instants_do_not_raise(self):
        """`GoalScheduleChange`'s columns come back naive like every other stored instant in this
        database, and comparing one to an aware datetime raises `TypeError`. This is the trap that made
        `GET /progress/goals` a 500 for any goal with a deadline."""
        rows = [_change(datetime(2026, 8, 27, 12, 0), datetime(2026, 9, 27, 12, 0))]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])
        assert history["goal-1"].extended_count == 1

    @pytest.mark.asyncio
    async def test_the_systems_own_extensions_are_counted_separately(self):
        """The distinction the ladder's extension budget rests on. Conflating the two would either spend
        a learner's budget on their own re-planning, or let the system extend indefinitely."""
        rows = [
            _change(NOW, NOW + timedelta(days=7), "learner_edited"),
            _change(NOW + timedelta(days=7), NOW + timedelta(days=21), "system_extended"),
            _change(NOW + timedelta(days=21), NOW + timedelta(days=30), "system_extended"),
            _change(NOW + timedelta(days=30), NOW + timedelta(days=40), "plan_regenerated"),
        ]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])

        assert history["goal-1"].extended_count == 4
        assert history["goal-1"].system_extended_count == 2

    @pytest.mark.asyncio
    async def test_a_learners_own_edit_never_counts_against_the_system(self):
        rows = [_change(NOW, NOW + timedelta(days=30), "learner_edited")]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])

        assert history["goal-1"].extended_count == 1
        assert history["goal-1"].system_extended_count == 0

    @pytest.mark.asyncio
    async def test_a_system_extension_pulled_earlier_is_not_counted(self):
        """The `system_extended` label is not enough on its own — the count is of deadlines that moved
        *later*, whoever moved them."""
        rows = [_change(NOW + timedelta(days=30), NOW, "system_extended")]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1"])

        assert history["goal-1"].system_extended_count == 0

    @pytest.mark.asyncio
    async def test_several_goals_are_attributed_separately(self):
        rows = [
            ("goal-1", NOW, NOW + timedelta(days=7), "learner_edited"),
            ("goal-2", NOW, NOW - timedelta(days=7), "learner_edited"),
            ("goal-1", NOW + timedelta(days=7), NOW + timedelta(days=30), "learner_edited"),
        ]
        with patch.object(goal_metrics, "get_session_factory", lambda: lambda: _rows_session(rows)):
            history = await goal_metrics.derive_schedule_history(["goal-1", "goal-2"])
        assert history["goal-1"].extended_count == 2
        assert history["goal-2"].extended_count == 0


# ===========================================================================
# Writing the log
# ===========================================================================


class TestRecordDateChange:
    @pytest.mark.asyncio
    async def test_a_move_is_recorded_with_both_dates_and_the_reason(self):
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=NOW),
                new_date=NOW + timedelta(days=30),
                reason="learner_edited",
            )

        assert len(recorded) == 1
        assert recorded[0]["previousDate"] == NOW
        assert recorded[0]["newDate"] == NOW + timedelta(days=30)
        assert recorded[0]["reason"] == "learner_edited"
        assert recorded[0]["goalId"] == "goal-1"
        assert recorded[0]["userId"] == "user-1"

    @pytest.mark.asyncio
    async def test_the_authority_is_snapshotted_not_left_to_be_derived_later(self):
        """`Goal.prepId` is `ON DELETE SET NULL`, so deleting the preparation would retroactively
        reclassify this change as the learner's own. What the entry records is what was true when the
        date moved."""
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(prep_id="prep-1", target_date=NOW),
                new_date=NOW + timedelta(days=3),
                reason="plan_regenerated",
            )

        assert recorded[0]["dateAuthority"] == "external"

    @pytest.mark.asyncio
    async def test_saving_a_goal_without_touching_the_deadline_records_nothing(self):
        """Every field update goes through the same write path, so logging unconditionally would turn
        "this deadline moved three times" into "this goal was saved three times"."""
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=NOW), new_date=NOW, reason="learner_edited"
            )

        assert recorded == []

    @pytest.mark.asyncio
    async def test_the_same_instant_written_naive_is_still_a_no_op(self):
        """The stored column is naive and the incoming value is aware. Compared directly these are never
        equal, so every save would record a move."""
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=datetime(2026, 8, 27, 12, 0)),
                new_date=NOW,
                reason="learner_edited",
            )

        assert recorded == []

    @pytest.mark.asyncio
    async def test_clearing_a_deadline_is_recorded(self):
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=NOW), new_date=None, reason="learner_edited"
            )

        assert len(recorded) == 1
        assert recorded[0]["newDate"] is None

    @pytest.mark.asyncio
    async def test_a_goal_that_never_had_a_deadline_gaining_one_is_recorded(self):
        recorded: list[dict] = []

        async def _create(data):
            recorded.append(data)

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _create):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=None), new_date=NOW, reason="learner_edited"
            )

        assert recorded[0]["previousDate"] is None

    @pytest.mark.asyncio
    async def test_a_failure_to_log_does_not_fail_the_edit(self):
        """The edit is what the learner asked for; the log is bookkeeping about it. Raising here would
        mean a full disk rejecting a learner's deadline change."""

        async def _boom(_data):
            raise RuntimeError("no")

        with patch.object(goal_schedule_log.progress_repo, "create_schedule_change", _boom):
            await goal_schedule_log.record_date_change(
                goal=_goal(target_date=NOW),
                new_date=NOW + timedelta(days=1),
                reason="learner_edited",
            )


class TestTheReasonTokens:
    def test_they_match_the_database_constraint(self):
        """A token Python writes and Postgres refuses is a 500 on a path whose whole job is to be
        unobtrusive. Same pin as `Goal_metricKind_check`."""
        from src.domains.progress.db_models import GoalScheduleChange

        check = next(
            constraint
            for constraint in GoalScheduleChange.__table__.constraints
            if getattr(constraint, "name", None) == "GoalScheduleChange_reason_check"
        )
        sql = str(check.sqltext)
        for reason in GoalScheduleChange.REASONS:
            assert f"'{reason}'" in sql, reason
        assert sql.count("'") // 2 == len(GoalScheduleChange.REASONS)

    def test_the_authority_constraint_matches_the_derived_type(self):
        from typing import get_args

        from src.domains.progress.db_models import GoalScheduleChange

        check = next(
            constraint
            for constraint in GoalScheduleChange.__table__.constraints
            if getattr(constraint, "name", None) == "GoalScheduleChange_dateAuthority_check"
        )
        sql = str(check.sqltext)
        for authority in get_args(goal_metrics.DateAuthority):
            assert f"'{authority}'" in sql, authority
        assert sql.count("'") // 2 == len(get_args(goal_metrics.DateAuthority))

    def test_every_token_has_a_writer(self):
        """`system_extended` was withheld until the nightly ladder existed, because a value the schema
        offers and nothing can produce is the accept-and-ignore defect this codebase keeps closing. It
        arrived with `goal_lifecycle_service`. This pins the rule rather than the snapshot: each token
        must be written by something in `src`."""
        import pathlib
        import re

        from src.domains.progress.db_models import GoalScheduleChange

        src = pathlib.Path(__file__).resolve().parents[1] / "src"
        written = set()
        for path in src.rglob("*.py"):
            for match in re.finditer(r'reason="([a-z_]+)"', path.read_text()):
                written.add(match.group(1))

        assert (
            set(GoalScheduleChange.REASONS) <= written
        ), f"tokens with no writer: {sorted(set(GoalScheduleChange.REASONS) - written)}"


# ===========================================================================
# The learner's own edit
# ===========================================================================


class TestUpdateGoalLogsTheDeadline:
    @pytest.mark.asyncio
    async def test_sending_a_new_deadline_records_a_change(self):
        from src.domains.progress.services import goal_service

        existing = _goal(target_date=NOW)
        calls: list[dict] = []

        async def _record(*, goal, new_date, reason):
            calls.append({"goal": goal, "new_date": new_date, "reason": reason})

        with (
            patch.object(goal_service.progress_repo, "find_goal", return_value=existing),
            patch.object(goal_service.progress_repo, "update_goal", return_value=existing),
            patch.object(goal_service.goal_schedule_log, "record_date_change", _record),
        ):
            await goal_service.update_goal(
                goal_id="goal-1",
                user_id="user-1",
                data={"targetDate": NOW + timedelta(days=30)},
            )

        assert len(calls) == 1
        assert calls[0]["reason"] == "learner_edited"
        # The row **before** the update, which is the only thing that still knows the old deadline.
        assert calls[0]["goal"] is existing

    @pytest.mark.asyncio
    async def test_editing_something_else_records_nothing(self):
        """`exclude_unset=True` on the route means the key is present only when the learner sent it, so
        renaming a goal must not look like moving its deadline."""
        from src.domains.progress.services import goal_service

        existing = _goal(target_date=NOW)
        calls: list = []

        async def _record(**kwargs):
            calls.append(kwargs)

        with (
            patch.object(goal_service.progress_repo, "find_goal", return_value=existing),
            patch.object(goal_service.progress_repo, "update_goal", return_value=existing),
            patch.object(goal_service.goal_schedule_log, "record_date_change", _record),
        ):
            await goal_service.update_goal(
                goal_id="goal-1", user_id="user-1", data={"title": "Renamed"}
            )

        assert calls == []

    @pytest.mark.asyncio
    async def test_an_empty_update_writes_nothing_at_all(self):
        from src.domains.progress.services import goal_service

        existing = _goal(target_date=NOW)
        calls: list = []
        updates: list = []

        async def _record(**kwargs):
            calls.append(kwargs)

        async def _update(*a, **k):
            updates.append(a)
            return existing

        with (
            patch.object(goal_service.progress_repo, "find_goal", return_value=existing),
            patch.object(goal_service.progress_repo, "update_goal", _update),
            patch.object(goal_service.goal_schedule_log, "record_date_change", _record),
        ):
            await goal_service.update_goal(goal_id="goal-1", user_id="user-1", data={})

        assert calls == []
        assert updates == []


# ===========================================================================
# The plan regenerator — the rewriter that was already there
# ===========================================================================


class TestRegeneratePlanRecordsTheMove:
    """`regenerate_goal_plan` recomputes `targetDate` from a requested duration in weeks and writes it.

    It does not read date authority, so it will move an exam-derived deadline; it had no record of doing
    so, which is what made "this goal's window was always this long" indistinguishable from "this goal
    was quietly given six more weeks". Behaviour is unchanged here — the rewrite still happens. These pin
    that it is now *visible*.
    """

    async def _regenerate(self, goal, *, recorded: list):
        from src.domains.intelligence.planning import planning_impl
        from src.domains.progress.services import goal_schedule_log as log_module

        async def _record(*, goal, new_date, reason):
            recorded.append({"goal": goal, "new_date": new_date, "reason": reason})

        async def _plan(_prompt):
            return {"goal": {"description": "d"}, "schedule": {}, "study_tips": []}

        with (
            patch.object(planning_impl.progress_repo, "find_goal", return_value=goal),
            patch.object(planning_impl.progress_repo, "delete_blocks_for_goal", return_value=0),
            patch.object(planning_impl.progress_repo, "update_goal", return_value=goal),
            patch.object(planning_impl, "_call_gemini_for_plan", _plan),
            patch.object(planning_impl, "IdentityRepository") as identity,
            # `create=True` because **`action_service` is a stub that has no `create_schedule`**
            # (`intelligence/action/action_service.py` holds only `execute`, returning `None`). So this
            # route raises `AttributeError` the moment it reaches its block-creation loop, today, for
            # reasons that have nothing to do with this change. Patched in rather than worked around so
            # these tests exercise the deadline write, which happens before that loop and is therefore
            # reached in production too — the date moves, then the request 500s.
            patch.object(
                planning_impl.action_service, "create_schedule", create=True, new=_empty_result
            ),
            patch.object(log_module, "record_date_change", _record),
        ):
            identity.return_value.find_by_id = _none_coroutine
            return await planning_impl.regenerate_goal_plan(
                user_id="user-1", goal_id="goal-1", duration_weeks=6
            )

    @pytest.mark.asyncio
    async def test_regenerating_a_plan_records_the_deadline_it_moved(self):
        recorded: list[dict] = []
        goal = _goal(title="T", description=None, target_date=NOW)

        result = await self._regenerate(goal, recorded=recorded)

        assert result["status"] == "success"
        assert len(recorded) == 1
        assert recorded[0]["reason"] == "plan_regenerated"
        # Six weeks out, which is not the deadline the learner set.
        assert recorded[0]["new_date"] > NOW

    @pytest.mark.asyncio
    async def test_it_records_the_row_as_it_was_before_the_write(self):
        """The pre-update row is the only thing that still holds the old deadline. Reading the goal back
        after the update would record a change from the new date to itself, which the no-op rule then
        discards — a log that is always empty and never wrong."""
        recorded: list[dict] = []
        goal = _goal(title="T", description=None, target_date=NOW, prep_id="prep-1")

        await self._regenerate(goal, recorded=recorded)

        assert recorded[0]["goal"].target_date == NOW
        # And an exam-derived deadline being rewritten is exactly the case worth being able to find.
        assert goal_metrics.date_authority(recorded[0]["goal"]) == "external"


async def _none_coroutine(*_a, **_k):
    return None


async def _empty_result(*_a, **_k):
    return {}
