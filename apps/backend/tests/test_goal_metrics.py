"""What a goal measures, and the labels derived from it.

Three things are worth pinning here beyond the arithmetic.

**One at-risk threshold.** Decision N asks for a single rule shared by `/progress/goals` and
`/reflect/goals`. It was defined in `personal_learning` while `Goal` belongs to `progress`, so it
moved rather than being copied — and a test asserts the two modules resolve to the same object,
because a duplicate would show the same learner two different labels for the same goal.

**`currentValue` is refused, not dropped, on a measured goal.** Accepting a figure and silently
overwriting it on the next read is the accept-and-discard pattern this backend forbids everywhere
else.

**The Pydantic enum and the database CHECK must agree.** A value one accepts and the other refuses
is a 500 where the learner should have got a 422.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.progress.services import goal_metrics
from src.shared.exceptions import ValidationError

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _goal(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "goal-1",
        "user_id": "user-1",
        "metric_kind": "manual",
        "current_value": None,
        "target_value": None,
        "course_id": None,
        "topic_id": None,
        "prep_id": None,
        "created_at": NOW - timedelta(days=10),
        "target_date": NOW + timedelta(days=10),
        "progress": 0.0,
    }
    return SimpleNamespace(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# TestOneDefinition
# ---------------------------------------------------------------------------


class TestOneDefinition:
    def test_reflect_reads_the_same_at_risk_rule_as_progress(self):
        """Not a copy — the same function object, so the two cannot drift."""
        from src.domains.personal_learning.services import reflect_aggregates

        assert reflect_aggregates.is_at_risk is goal_metrics.is_at_risk
        assert reflect_aggregates.AT_RISK_LAG_POINTS is goal_metrics.AT_RISK_LAG_POINTS

    def test_the_metric_kinds_match_the_database_constraint(self):
        """A value Pydantic accepts and Postgres refuses is a 500, not a 422."""
        from src.domains.progress.db_models import Goal

        check = next(
            constraint
            for constraint in Goal.__table__.constraints
            if getattr(constraint, "name", None) == "Goal_metricKind_check"
        )
        sql = str(check.sqltext)

        for kind in goal_metrics.METRIC_KINDS:
            assert f"'{kind}'" in sql, kind
        # And nothing in the constraint that the code does not know about.
        assert sql.count("'") // 2 == len(goal_metrics.METRIC_KINDS)

    def test_the_pydantic_enum_matches_too(self):
        from typing import get_args

        from src.domains.progress.models import GoalMetricKind

        assert set(get_args(GoalMetricKind)) == set(goal_metrics.METRIC_KINDS)


# ---------------------------------------------------------------------------
# TestPaceAndStatus
# ---------------------------------------------------------------------------


class TestPaceAndStatus:
    def test_an_open_ended_goal_is_never_at_risk(self):
        """No deadline means no pace to fall behind.

        Labelling it "needs attention" for slow progress would invent a commitment the learner
        never made.
        """
        assert (
            goal_metrics.is_at_risk(
                progress=1.0, created_at=NOW - timedelta(days=200), target_date=None, now=NOW
            )
            is False
        )
        assert (
            goal_metrics.elapsed_percent(
                created_at=NOW - timedelta(days=200), target_date=None, now=NOW
            )
            is None
        )

    def test_an_overdue_unfinished_goal_is_at_risk_whatever_its_progress(self):
        assert goal_metrics.is_at_risk(
            progress=99.0,
            created_at=NOW - timedelta(days=30),
            target_date=NOW - timedelta(days=1),
            now=NOW,
        )

    def test_a_finished_goal_is_never_at_risk(self):
        assert (
            goal_metrics.is_at_risk(
                progress=100.0,
                created_at=NOW - timedelta(days=30),
                target_date=NOW - timedelta(days=1),
                now=NOW,
            )
            is False
        )

    def test_lagging_by_more_than_the_threshold_needs_attention(self):
        """Halfway through the window: 50% elapsed against 30% done is a 20-point lag."""
        created, target = NOW - timedelta(days=10), NOW + timedelta(days=10)

        assert (
            goal_metrics.status_label(
                progress=30.0, status="ACTIVE", created_at=created, target_date=target, now=NOW
            )
            == "NEEDS_ATTENTION"
        )
        # 40% done is a 10-point lag, inside the threshold.
        assert (
            goal_metrics.status_label(
                progress=40.0, status="ACTIVE", created_at=created, target_date=target, now=NOW
            )
            == "ON_TRACK"
        )

    def test_the_threshold_is_exclusive_at_exactly_fifteen_points(self):
        """Pinned because `>` against `>=` moves the label for a whole band of goals."""
        created, target = NOW - timedelta(days=10), NOW + timedelta(days=10)
        exactly = 50.0 - goal_metrics.AT_RISK_LAG_POINTS

        assert (
            goal_metrics.is_at_risk(
                progress=exactly, created_at=created, target_date=target, now=NOW
            )
            is False
        )

    def test_completion_wins_over_the_lifecycle_status(self):
        assert (
            goal_metrics.status_label(
                progress=100.0, status="ACTIVE", created_at=None, target_date=None, now=NOW
            )
            == "COMPLETED"
        )
        assert (
            goal_metrics.status_label(
                progress=12.0, status="COMPLETED", created_at=None, target_date=None, now=NOW
            )
            == "COMPLETED"
        )

    def test_pace_is_one_hundred_when_exactly_on_schedule(self):
        created, target = NOW - timedelta(days=10), NOW + timedelta(days=10)

        assert goal_metrics.pace_percent(
            progress=50.0, created_at=created, target_date=target, now=NOW
        ) == pytest.approx(100.0)

    def test_pace_and_projection_are_null_without_a_deadline(self):
        for value in (
            goal_metrics.pace_percent(
                progress=50.0, created_at=NOW - timedelta(days=1), target_date=None, now=NOW
            ),
            goal_metrics.projected_outcome(
                progress=50.0, created_at=NOW - timedelta(days=1), target_date=None, now=NOW
            ),
        ):
            assert value is None

    def test_pace_is_withheld_at_the_very_start_of_a_window(self):
        """A ratio against a near-zero elapsed fraction swings wildly and means nothing."""
        created = NOW - timedelta(minutes=1)
        target = NOW + timedelta(days=100)

        assert (
            goal_metrics.pace_percent(progress=1.0, created_at=created, target_date=target, now=NOW)
            is None
        )

    def test_projection_is_capped_at_one_hundred(self):
        """A goal cannot be more than finished."""
        created, target = NOW - timedelta(days=1), NOW + timedelta(days=99)

        assert (
            goal_metrics.projected_outcome(
                progress=90.0, created_at=created, target_date=target, now=NOW
            )
            == 100.0
        )


# ---------------------------------------------------------------------------
# TestAssertedCurrentValue
# ---------------------------------------------------------------------------


class TestAssertedCurrentValue:
    """`currentValue` is the learner's only on a `manual` goal."""

    async def test_a_manual_goal_may_carry_its_own_figure(self):
        from src.domains.progress.services import goal_service

        goal_service._reject_asserted_current_value({"currentValue": 42.0}, metric_kind="manual")

    async def test_a_measured_goal_refuses_an_asserted_figure(self):
        """Refused rather than dropped: a learner overruled without explanation is worse."""
        from src.domains.progress.services import goal_service

        with pytest.raises(ValidationError) as caught:
            goal_service._reject_asserted_current_value(
                {"currentValue": 42.0}, metric_kind="focused_minutes"
            )

        message = str(caught.value)
        assert "currentValue" in message
        assert "focused_minutes" in message

    async def test_omitting_it_is_always_fine(self):
        from src.domains.progress.services import goal_service

        for kind in goal_metrics.METRIC_KINDS:
            goal_service._reject_asserted_current_value({}, metric_kind=kind)
            goal_service._reject_asserted_current_value({"currentValue": None}, metric_kind=kind)


# ---------------------------------------------------------------------------
# TestDerivedCurrentValue
# ---------------------------------------------------------------------------


class TestDerivedCurrentValue:
    async def test_a_manual_goal_returns_its_stored_value_unmeasured(self):
        results = await goal_metrics.derive_current_values(
            [_goal(metric_kind="manual", current_value=17.0)], now=NOW
        )

        assert results["goal-1"].current_value == 17.0
        assert results["goal-1"].measured is False

    async def test_no_goals_needs_no_queries(self):
        assert await goal_metrics.derive_current_values([], now=NOW) == {}

    async def test_a_kind_without_its_link_is_unmeasured_not_zero(self, monkeypatch):
        """A `course_progress` goal with no `courseId` has nothing to measure.

        `0` would report the learner as having made no progress, which is a different and false
        claim.
        """

        class _Result:
            def all(self):
                return []

        class _Session:
            async def execute(self, *_a, **_k):
                return _Result()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_e):
                return False

        monkeypatch.setattr(goal_metrics, "get_session_factory", lambda: _Session)

        results = await goal_metrics.derive_current_values(
            [_goal(metric_kind="course_progress", course_id=None)], now=NOW
        )

        assert results["goal-1"].current_value is None
        assert results["goal-1"].measured is False


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _portfolio_goal(status, progress, created_at, target_date, **overrides):
    """A goal row as the portfolio now reads it: the whole row, not four columns.

    `manual` by default, so `derived_progress` returns the stored figure and the counting rules these
    tests are about are unaffected by the measurement layer.
    """
    from types import SimpleNamespace

    fields = {
        "id": overrides.pop("id", f"goal-{progress}-{status}"),
        "user_id": "u",
        "status": status,
        "progress": progress,
        "created_at": created_at,
        "target_date": target_date,
        "metric_kind": "manual",
        "target_value": None,
        "current_value": None,
        "course_id": None,
        "topic_id": None,
        "prep_id": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def _portfolio_from_rows(rows, *, measurements=None) -> goal_metrics.GoalPortfolio:
    """Run `get_goal_portfolio`'s folding over given `(status, progress, createdAt, targetDate)` rows.

    The query needs Postgres; the counting rules are what these tests are about, so the rows are
    supplied and only the arithmetic runs. Same device as `test_daily_snapshots._mastery_from_rows`.

    Tuples are accepted for the rules that predate measured progress; pass `_portfolio_goal(...)`
    objects to exercise a goal whose progress is measured rather than stored.
    """
    from unittest.mock import patch

    goals = [row if not isinstance(row, tuple) else _portfolio_goal(*row) for row in rows]

    class _Scalars:
        def all(self):
            return goals

    class _Result:
        def all(self):
            return goals

        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    async def _measure(_goals, *, now=None):
        return measurements or {}

    with patch.object(goal_metrics, "get_session_factory", lambda: _Session):
        if measurements is not None:
            with patch.object(goal_metrics, "derive_current_values", _measure):
                return await goal_metrics.get_goal_portfolio(user_id="u", now=NOW)
        return await goal_metrics.get_goal_portfolio(user_id="u", now=NOW)


class TestDueSoonAndOverdue:
    """`dueSoon` and `overdue` are separate counts, and the split is the point.

    Merging them would let a goal three weeks late be reported as "due this week", which is the one
    reading of that tile that would make a learner relax about it.
    """

    def _kwargs(self, **overrides):
        defaults = {
            "status": "ACTIVE",
            "progress": 40.0,
            "target_date": NOW + timedelta(days=3),
            "now": NOW,
        }
        return {**defaults, **overrides}

    def test_a_deadline_inside_the_window_is_due_soon(self):
        assert goal_metrics.is_due_soon(**self._kwargs()) is True

    def test_the_window_boundary_is_inclusive(self):
        exactly = NOW + timedelta(days=goal_metrics.DUE_SOON_DAYS)
        assert goal_metrics.is_due_soon(**self._kwargs(target_date=exactly)) is True
        just_outside = exactly + timedelta(seconds=1)
        assert goal_metrics.is_due_soon(**self._kwargs(target_date=just_outside)) is False

    def test_a_passed_deadline_is_overdue_and_not_due_soon(self):
        passed = self._kwargs(target_date=NOW - timedelta(days=21))
        assert goal_metrics.is_due_soon(**passed) is False
        assert goal_metrics.is_overdue(**passed) is True

    def test_an_open_ended_goal_is_neither(self):
        none_dated = self._kwargs(target_date=None)
        assert goal_metrics.is_due_soon(**none_dated) is False
        assert goal_metrics.is_overdue(**none_dated) is False

    def test_finished_work_creates_no_deadline_pressure(self):
        """100% with nobody having marked it complete. Chasing it would tell the learner to do
        something they have already done."""
        done = self._kwargs(progress=100.0, target_date=NOW - timedelta(days=1))
        assert goal_metrics.is_due_soon(**done) is False
        assert goal_metrics.is_overdue(**done) is False

    @pytest.mark.parametrize("status", ["COMPLETED", "ARCHIVED", "CANCELLED"])
    def test_only_active_goals_have_deadlines_that_count(self, status):
        assert goal_metrics.is_due_soon(**self._kwargs(status=status)) is False
        assert (
            goal_metrics.is_overdue(
                **self._kwargs(status=status, target_date=NOW - timedelta(days=2))
            )
            is False
        )


class TestGoalPortfolio:
    async def test_a_cancelled_goal_no_longer_drags_the_average_down(self):
        """The bug this fixes: a cancelled goal counted towards `averageProgress` while appearing in
        neither `active` nor `completed`, so abandoning a goal at 5% moved the average with no
        visible cause. Archived goals were already excluded on exactly this reasoning."""
        created = NOW - timedelta(days=10)
        rows = [
            ("ACTIVE", 80.0, created, None),
            ("CANCELLED", 5.0, created, None),
        ]
        portfolio = await _portfolio_from_rows(rows)

        assert portfolio.active == 1
        assert portfolio.completed == 0
        assert portfolio.average_progress == 80.0

    async def test_archived_goals_are_excluded_too(self):
        created = NOW - timedelta(days=10)
        rows = [("ACTIVE", 60.0, created, None), ("ARCHIVED", 0.0, created, None)]
        portfolio = await _portfolio_from_rows(rows)

        assert portfolio.active == 1
        assert portfolio.average_progress == 60.0

    async def test_no_goals_averages_to_null_rather_than_zero(self):
        """No goals is not the same as no progress (Decision I)."""
        portfolio = await _portfolio_from_rows([])

        assert portfolio.average_progress is None
        assert portfolio.active == 0
        assert portfolio.due_soon == 0
        assert portfolio.overdue == 0

    async def test_the_five_counts_are_independent(self):
        created = NOW - timedelta(days=30)
        rows = [
            # Active, on pace, deadline far off.
            ("ACTIVE", 90.0, created, NOW + timedelta(days=60)),
            # Active and due in three days.
            ("ACTIVE", 50.0, created, NOW + timedelta(days=3)),
            # Active and three weeks late.
            ("ACTIVE", 20.0, created, NOW - timedelta(days=21)),
            ("COMPLETED", 100.0, created, None),
        ]
        portfolio = await _portfolio_from_rows(rows)

        assert portfolio.active == 3
        assert portfolio.completed == 1
        assert portfolio.due_soon == 1
        assert portfolio.overdue == 1
        # The overdue one and the behind-pace one are both at risk; the 90% one is not.
        assert portfolio.at_risk == 2


class TestTheAverageIsMeasuredNotStored:
    """`Goal.progress` is a column nothing writes — `update_progress` has no callers anywhere in
    `src`. The portfolio average used to be the average of that column, so a learner whose course went
    from 0 to 60 percent saw the figure their goals section leads with stay exactly where it was."""

    async def test_a_measured_goal_contributes_its_measured_progress(self):
        created = NOW - timedelta(days=10)
        goal = _portfolio_goal(
            "ACTIVE",
            0.0,  # the stored column, never written
            created,
            None,
            id="g1",
            metric_kind="course_progress",
            course_id="c1",
            target_value=100.0,
        )
        portfolio = await _portfolio_from_rows(
            [goal],
            measurements={"g1": goal_metrics.GoalMeasurement(current_value=60.0, measured=True)},
        )

        assert portfolio.average_progress == 60.0

    async def test_a_measured_goal_at_its_target_counts_as_complete_for_deadline_pressure(self):
        """`is_due_soon` and `is_overdue` both exclude a goal at 100. Reading the stale column would
        chase a learner about a deadline for work they have already finished."""
        created = NOW - timedelta(days=30)
        goal = _portfolio_goal(
            "ACTIVE",
            0.0,
            created,
            NOW + timedelta(days=2),
            id="g1",
            metric_kind="prep_readiness",
            prep_id="p1",
            target_value=85.0,
        )
        portfolio = await _portfolio_from_rows(
            [goal],
            measurements={"g1": goal_metrics.GoalMeasurement(current_value=85.0, measured=True)},
        )

        assert portfolio.average_progress == 100.0
        assert portfolio.due_soon == 0
        assert portfolio.at_risk == 0

    async def test_an_unmeasured_goal_keeps_its_stored_figure(self):
        created = NOW - timedelta(days=10)
        goal = _portfolio_goal(
            "ACTIVE", 40.0, created, None, id="g1", metric_kind="course_progress", course_id=None
        )
        portfolio = await _portfolio_from_rows(
            [goal],
            measurements={"g1": goal_metrics.GoalMeasurement(current_value=None, measured=False)},
        )

        assert portfolio.average_progress == 40.0


class TestDerivedProgress:
    """One definition, used by the route, the portfolio and the nightly snapshot."""

    def _goal(self, **overrides):
        return _portfolio_goal(
            "ACTIVE", overrides.pop("progress", 0.0), NOW, None, **overrides
        )

    def test_a_manual_goal_keeps_the_learners_own_figure(self):
        goal = self._goal(progress=35.0, metric_kind="manual")
        measurement = goal_metrics.GoalMeasurement(current_value=99.0, measured=False)

        assert goal_metrics.derived_progress(goal, measurement) == 35.0

    def test_a_state_kind_with_no_stated_target_uses_the_scale_maximum(self):
        """`course_progress` is already a percentage, so 100 is the scale's maximum rather than a
        guess about what the learner wants."""
        goal = self._goal(metric_kind="course_progress", course_id="c1", target_value=None)
        measurement = goal_metrics.GoalMeasurement(current_value=42.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 42.0

    def test_progress_is_measured_against_the_stated_target(self):
        """A preparation aiming at 85 percent readiness is finished at 85."""
        goal = self._goal(metric_kind="prep_readiness", prep_id="p1", target_value=85.0)
        measurement = goal_metrics.GoalMeasurement(current_value=85.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 100.0

    def test_an_accumulating_kind_with_no_target_keeps_the_stored_value(self):
        """Minutes have no natural maximum, so there is no fraction to compute and nothing is
        invented."""
        goal = self._goal(progress=12.0, metric_kind="focused_minutes", target_value=None)
        measurement = goal_metrics.GoalMeasurement(current_value=600.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 12.0

    def test_an_accumulating_kind_with_a_target_is_a_share_of_it(self):
        goal = self._goal(metric_kind="focused_minutes", target_value=600.0)
        measurement = goal_metrics.GoalMeasurement(current_value=150.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 25.0

    def test_it_never_exceeds_one_hundred(self):
        """`GoalResponse.progress` is `le=100.0`, so an over-delivered goal would be a 500."""
        goal = self._goal(metric_kind="focused_minutes", target_value=100.0)
        measurement = goal_metrics.GoalMeasurement(current_value=450.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 100.0

    def test_a_negative_measurement_floors_at_zero(self):
        goal = self._goal(metric_kind="focused_minutes", target_value=100.0)
        measurement = goal_metrics.GoalMeasurement(current_value=-5.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 0.0

    def test_a_zero_target_does_not_divide_by_zero(self):
        goal = self._goal(progress=7.0, metric_kind="focused_minutes", target_value=0.0)
        measurement = goal_metrics.GoalMeasurement(current_value=10.0, measured=True)

        assert goal_metrics.derived_progress(goal, measurement) == 7.0

    def test_no_measurement_at_all_keeps_the_stored_value(self):
        goal = self._goal(progress=18.0, metric_kind="course_progress", course_id="c1")

        assert goal_metrics.derived_progress(goal, None) == 18.0

    def test_the_result_stays_inside_the_response_bounds(self):
        """Pins the contract the field declares, rather than the arithmetic that happens to satisfy
        it today."""
        goal = self._goal(metric_kind="course_progress", course_id="c1")
        for value in (-100.0, 0.0, 33.3, 100.0, 1000.0):
            result = goal_metrics.derived_progress(
                goal, goal_metrics.GoalMeasurement(current_value=value, measured=True)
            )
            assert 0.0 <= result <= 100.0


class TestPortfolioHasOneHome:
    def test_reflect_aggregates_re_exports_rather_than_reimplements(self):
        """Same guard as `is_at_risk`: two implementations of a count the learner reads on two
        surfaces is what Decision N's one-threshold clause exists to prevent."""
        from src.domains.personal_learning.services import reflect_aggregates

        assert reflect_aggregates.get_goal_portfolio is goal_metrics.get_goal_portfolio
        assert reflect_aggregates.GoalPortfolio is goal_metrics.GoalPortfolio


class TestSummaryRouteOrdering:
    def test_summary_is_declared_before_the_goal_id_route(self):
        """FastAPI matches in declaration order, so `/goals/summary` must come first.

        The other way round, the literal path arrives as a goal called "summary" and the endpoint
        404s — with nothing in the code looking wrong, which is why this is pinned rather than left to
        a reviewer noticing the order of two decorators.
        """
        from src.domains.progress.routes import router

        paths = [route.path for route in router.routes]
        assert "/goals/summary" in paths, "the summary route is gone"
        assert "/goals/{goal_id}" in paths
        assert paths.index("/goals/summary") < paths.index("/goals/{goal_id}")


async def _momentum_from_rows(rows, *, weeks: int, now: datetime) -> list:
    """Run `get_goal_momentum`'s bucketing over given `(startAt, completedAt)` rows."""
    from unittest.mock import patch

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

    with patch.object(goal_metrics, "get_session_factory", lambda: _Session):
        return await goal_metrics.get_goal_momentum(
            user_id="u", goal_id="g", weeks=weeks, now=now
        )


class TestGoalMomentum:
    """Planned versus completed, per week.

    `planned` was always derivable — a count of `ScheduleBlock` rows for the goal. `completed` was
    recorded nowhere until `completedAt` was added for this, so it reads zero rather than being inferred
    from a `StudySession` that happens to overlap the block's window (Decision Y).
    """

    # A Wednesday, so the bucketing has to find the Monday rather than getting it for free.
    WEDNESDAY = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)

    async def test_buckets_by_the_monday_of_each_week(self):
        rows = [
            # Two in the current week (Mon 24 Aug), one completed.
            (datetime(2026, 8, 24, 9, 0, tzinfo=UTC), datetime(2026, 8, 24, 10, 0, tzinfo=UTC)),
            (datetime(2026, 8, 26, 9, 0, tzinfo=UTC), None),
            # One the week before (Mon 17 Aug).
            (datetime(2026, 8, 19, 9, 0, tzinfo=UTC), None),
        ]
        weeks = await _momentum_from_rows(rows, weeks=2, now=self.WEDNESDAY)

        assert [w.week_start for w in weeks] == [date(2026, 8, 17), date(2026, 8, 24)]
        assert [w.planned for w in weeks] == [1, 2]
        assert [w.completed for w in weeks] == [0, 1]

    async def test_a_week_with_nothing_planned_is_included_at_zero(self):
        """The opposite of the activity feed's daily counts, where a missing day is omitted. There an
        absent row means nothing was recorded; here a week the learner scheduled nothing is itself part
        of the answer to "did the plan get done"."""
        rows = [(datetime(2026, 8, 24, 9, 0, tzinfo=UTC), None)]
        weeks = await _momentum_from_rows(rows, weeks=3, now=self.WEDNESDAY)

        assert len(weeks) == 3
        assert [w.planned for w in weeks] == [0, 0, 1]
        assert [w.week_start for w in weeks] == [
            date(2026, 8, 10),
            date(2026, 8, 17),
            date(2026, 8, 24),
        ]

    async def test_the_series_always_ends_with_the_current_week(self):
        weeks = await _momentum_from_rows([], weeks=4, now=self.WEDNESDAY)

        assert weeks[-1].week_start == date(2026, 8, 24)
        assert len(weeks) == 4

    async def test_a_block_beyond_the_window_is_not_folded_into_the_nearest_week(self):
        """Counting it anywhere would overstate that week's plan."""
        rows = [
            (datetime(2026, 8, 24, 9, 0, tzinfo=UTC), None),
            # Six weeks earlier, outside a two-week window.
            (datetime(2026, 7, 13, 9, 0, tzinfo=UTC), None),
        ]
        weeks = await _momentum_from_rows(rows, weeks=2, now=self.WEDNESDAY)

        assert sum(w.planned for w in weeks) == 1

    async def test_completed_counts_only_blocks_with_a_timestamp(self):
        rows = [
            (datetime(2026, 8, 24, 9, 0, tzinfo=UTC), None),
            (datetime(2026, 8, 25, 9, 0, tzinfo=UTC), None),
            (datetime(2026, 8, 26, 9, 0, tzinfo=UTC), datetime(2026, 8, 27, 8, 0, tzinfo=UTC)),
        ]
        weeks = await _momentum_from_rows(rows, weeks=1, now=self.WEDNESDAY)

        assert weeks[0].planned == 3
        # Marked done the day after it was scheduled — still counts for the week it was planned for.
        assert weeks[0].completed == 1

    async def test_an_empty_plan_returns_zeroed_weeks_rather_than_nothing(self):
        """A goal with no plan yet still gets an axis, so the chart renders empty rather than absent."""
        weeks = await _momentum_from_rows([], weeks=4, now=self.WEDNESDAY)

        assert len(weeks) == 4
        assert all(w.planned == 0 and w.completed == 0 for w in weeks)


async def _portfolio_momentum_from_rows(rows, *, weeks: int, now: datetime) -> list:
    """Run `get_portfolio_momentum`'s bucketing over given `(startAt, completedAt)` rows."""
    from unittest.mock import patch

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

    with patch.object(goal_metrics, "get_session_factory", lambda: _Session):
        return await goal_metrics.get_portfolio_momentum(user_id="u", weeks=weeks, now=now)


class TestPortfolioMomentum:
    """The chart above the goals list, across every goal rather than one.

    Its own reader rather than a client summing per-goal calls: the page draws one chart, and summing
    responses would cost one request per goal to do it.
    """

    WEDNESDAY = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

    async def test_blocks_from_several_goals_land_in_one_series(self):
        rows = [
            (datetime(2026, 8, 24, 9, 0, tzinfo=UTC), None),
            (datetime(2026, 8, 25, 9, 0, tzinfo=UTC), datetime(2026, 8, 25, 10, 0, tzinfo=UTC)),
            (datetime(2026, 8, 26, 9, 0, tzinfo=UTC), None),
        ]
        weeks = await _portfolio_momentum_from_rows(rows, weeks=1, now=self.WEDNESDAY)

        assert len(weeks) == 1
        assert (weeks[0].planned, weeks[0].completed) == (3, 1)

    async def test_it_shares_the_bucketing_with_the_per_goal_read(self):
        """One implementation, so the portfolio chart and the goal charts cannot disagree.

        If these diverged, the total above the list would stop matching the sum of the charts beneath
        it and nothing on the page would explain the gap.
        """
        rows = [(datetime(2026, 8, 24, 9, 0, tzinfo=UTC), None)]
        portfolio = await _portfolio_momentum_from_rows(rows, weeks=4, now=self.WEDNESDAY)
        per_goal = await _momentum_from_rows(rows, weeks=4, now=self.WEDNESDAY)

        assert [(w.week_start, w.planned, w.completed) for w in portfolio] == [
            (w.week_start, w.planned, w.completed) for w in per_goal
        ]

    async def test_it_reads_only_blocks_attached_to_a_goal(self):
        """`ScheduleBlock.goalId` is nullable, and an unattached block is not part of any goal's plan.

        Counting it would make this chart taller than the sum of the per-goal charts beneath it.
        """
        import inspect

        source = inspect.getsource(goal_metrics.get_portfolio_momentum)
        body = source.split('"""')[-1]
        assert "goal_id.is_not(None)" in body

    async def test_an_empty_portfolio_still_gets_an_axis(self):
        weeks = await _portfolio_momentum_from_rows([], weeks=4, now=self.WEDNESDAY)

        assert len(weeks) == 4
        assert all(w.planned == 0 and w.completed == 0 for w in weeks)


def _portfolio(**overrides) -> goal_metrics.GoalPortfolio:
    defaults = {
        "active": 2,
        "completed": 1,
        "at_risk": 0,
        "due_soon": 0,
        "overdue": 0,
        "average_progress": 50.0,
    }
    defaults.update(overrides)
    return goal_metrics.GoalPortfolio(**defaults)


class TestPortfolioHeadline:
    """Which fact the goals page leads with.

    **A token, not a sentence.** The fixture's hero baked two numbers into prose — "You have 4 active
    goals with an average progress of 58%…" — which is a claim free to disagree with the tiles beneath
    it. The counts and this token let the client write the sentence from the same fields it renders.

    The *ladder* is server-side for the reason Decision O gives about action targets: which fact is most
    urgent is a judgement about the learner's data, and two clients making it separately would
    eventually disagree.
    """

    def test_no_goals_is_not_steady_at_zero(self):
        """A portfolio that does not exist must not be described as one that is holding level."""
        assert (
            goal_metrics.portfolio_headline(
                _portfolio(active=0, completed=0, average_progress=None)
            )
            == "none"
        )

    def test_overdue_outranks_at_risk(self):
        """Already true beats projected."""
        assert (
            goal_metrics.portfolio_headline(_portfolio(overdue=1, at_risk=1, due_soon=1))
            == "overdue"
        )

    def test_at_risk_outranks_due_soon(self):
        assert goal_metrics.portfolio_headline(_portfolio(at_risk=1, due_soon=1)) == "at_risk"

    def test_due_soon_when_nothing_is_wrong_yet(self):
        assert goal_metrics.portfolio_headline(_portfolio(due_soon=2)) == "due_soon"

    def test_everything_finished_is_its_own_state(self):
        """The only case where the next move is to set a goal rather than to work on one."""
        assert (
            goal_metrics.portfolio_headline(
                _portfolio(active=0, completed=3, average_progress=100.0)
            )
            == "all_complete"
        )

    def test_a_low_average_is_steady_not_strong(self):
        """The page must not congratulate a portfolio sitting at 12%."""
        assert goal_metrics.portfolio_headline(_portfolio(average_progress=12.0)) == "steady"

    def test_a_high_average_is_strong(self):
        assert goal_metrics.portfolio_headline(_portfolio(average_progress=80.0)) == "strong"

    def test_the_strong_boundary_is_inclusive(self):
        at_threshold = goal_metrics.STRONG_PORTFOLIO_PROGRESS
        assert goal_metrics.portfolio_headline(_portfolio(average_progress=at_threshold)) == "strong"
        assert (
            goal_metrics.portfolio_headline(_portfolio(average_progress=at_threshold - 0.1))
            == "steady"
        )

    def test_every_headline_is_reachable(self):
        """A token nothing can produce is a contract the client would handle for no reason."""
        produced = {
            goal_metrics.portfolio_headline(_portfolio(**kwargs))
            for kwargs in (
                {"active": 0, "completed": 0, "average_progress": None},
                {"overdue": 1},
                {"at_risk": 1},
                {"due_soon": 1},
                {"active": 0, "completed": 2, "average_progress": 100.0},
                {"average_progress": 90.0},
                {"average_progress": 10.0},
            )
        }
        assert produced == {
            "none",
            "overdue",
            "at_risk",
            "due_soon",
            "all_complete",
            "strong",
            "steady",
        }

    def test_it_reads_no_figure_the_response_does_not_publish(self):
        """The greeting and the tiles are rendered from one set of fields, so they cannot disagree."""
        published = {"active", "completed", "at_risk", "due_soon", "overdue", "average_progress"}
        import inspect

        source = inspect.getsource(goal_metrics.portfolio_headline)
        body = source.split('"""')[-1]
        # `dataclasses.fields`, not `dir`: these fields have no defaults, so they are annotations
        # rather than class attributes and never appear in `dir` of the class.
        import dataclasses

        read = {
            field.name
            for field in dataclasses.fields(goal_metrics.GoalPortfolio)
            if f"portfolio.{field.name}" in body
        }
        assert read
        assert read <= published


class TestBlockResponseCarriesAnOffset:
    """A schedule block's times reach the client with an explicit offset.

    `ScheduleBlock.startAt` is `timestamp without time zone`, so it arrives naive and a bare
    `.isoformat()` produced `"2026-08-23T09:00:00"`. A browser reads an offset-less string as *local*
    time, while the planner writes blocks at 09:00 **UTC** — so every learner not on UTC was shown the
    wrong time for their own sessions, off by exactly their offset.
    """

    def _block(self, **overrides):
        from types import SimpleNamespace

        base = {
            "id": "block-1",
            "user_id": "u1",
            "title": "Study session",
            "description": None,
            "start_at": datetime(2026, 8, 23, 9, 0),  # naive, as the column returns it
            "end_at": datetime(2026, 8, 23, 10, 0),
            "recurring_rule": None,
            "course_id": None,
            "topic_id": None,
            "goal_id": None,
            "review_item_id": None,
            "google_calendar_event_id": None,
            "google_calendar_synced_at": None,
            "completed_at": None,
            "created_at": datetime(2026, 8, 1, 0, 0),
            "updated_at": datetime(2026, 8, 1, 0, 0),
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_every_instant_is_published_with_an_offset(self):
        from src.domains.progress.routes import _to_block_response

        response = _to_block_response(self._block())

        assert response.startAt == "2026-08-23T09:00:00+00:00"
        assert response.endAt == "2026-08-23T10:00:00+00:00"
        assert response.createdAt.endswith("+00:00")
        assert response.updatedAt.endswith("+00:00")

    def test_an_aware_column_keeps_its_instant(self):
        """`completedAt` is written by the app with `datetime.now(UTC)`, so it may already be aware.
        Normalising must convert, not relabel — relabelling would move the event."""
        from datetime import timedelta, timezone

        from src.domains.progress.routes import _to_block_response

        lagos = datetime(2026, 8, 23, 11, 0, tzinfo=timezone(timedelta(hours=1)))
        response = _to_block_response(self._block(completed_at=lagos))

        assert response.completedAt == "2026-08-23T10:00:00+00:00"

    def test_a_block_that_was_never_completed_reports_null(self):
        from src.domains.progress.routes import _to_block_response

        response = _to_block_response(self._block(completed_at=None))

        assert response.completedAt is None
        assert response.googleCalendarSyncedAt is None
