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

from datetime import UTC, datetime, timedelta
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
