"""Phase 2 of the Reflect surface: the arithmetic behind every reflection metric.

`reflection_metrics` is deliberately split into a loader and a pure `compute`, and this file is
why. Database tests here are opt-in (`RUN_DB_TESTS=1`), so arithmetic reachable only through a
live query is arithmetic that does not run in CI — and this is arithmetic where a wrong answer
looks exactly like a right one until someone checks it against the rows.

The three properties under test are the ones the old code got wrong:

1. Unmeasured is `None`, never `0`. The previous failure path wrote zeros for every field,
   which made a broken generation indistinguishable from an inactive week.
2. Calendar questions use the learner's calendar. "Five active days" is a claim about their
   week, and every timestamp in the database is UTC.
3. The model receives numbers and produces none. The prompt is asserted on directly, because
   it is the only place that contract is expressed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domains.personal_learning import models
from src.domains.personal_learning.services import reflect_aggregates as aggregates
from src.domains.personal_learning.services import reflection_metrics as metrics_module
from src.domains.personal_learning.services import reflection_service
from src.shared.time import UNKNOWN_TIMEZONE, LearnerTimezone

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
)
LOS_ANGELES = LearnerTimezone(
    zone=ZoneInfo("America/Los_Angeles"), name="America/Los_Angeles", is_known=True, source="MANUAL"
)


def evidence(**overrides) -> metrics_module.MetricEvidence:
    """An active learner, so each test can vary the one thing it is about."""
    base = {
        "activity_instants": [datetime(2026, 8, 18, 10, 0, tzinfo=UTC)],
        "tracked_minutes": 326.4,
        "study_sessions_ended": 2,
        "quizzes_completed": 3,
        "quiz_answers_total": 20,
        "quiz_answers_correct": 17,
        "reviews_total": 128,
        "reviews_lapsed": 14,
        "notes_created": 4,
        "topics_touched": {"t1", "t2", "t3"},
        "topics_mastered": ["Graph representations", "Bayes' theorem"],
        "own_topics_total": 40,
        "own_topics_done_at_start": 10,
        "own_topics_done_at_end": 14,
        "streak_current": 12,
        "streak_best": 13,
        "milestones": ["Ten-day rhythm"],
        "consistency_score": 86.0,
        "average_session_minutes": 34.0,
        "best_day_of_week": "Thursday",
    }
    base.update(overrides)
    return metrics_module.MetricEvidence(**base)


class TestNothingIsInvented:
    def test_a_learner_with_no_history_gets_all_nulls(self):
        computed = metrics_module.compute(metrics_module.MetricEvidence(), UNKNOWN_TIMEZONE)
        dumped = computed.model_dump()
        assert all(value is None for value in dumped.values()), {
            key: value for key, value in dumped.items() if value is not None
        }

    def test_an_active_learner_with_nothing_to_report_gets_measured_zeros(self):
        """The other half of rule 1. Once there is evidence of showing up, a zero is a finding.

        A learner who opened a quiz and reviewed no cards genuinely reviewed zero cards, and
        reporting `None` there would be as wrong as reporting `0` for the learner who was
        never measured.
        """
        computed = metrics_module.compute(
            metrics_module.MetricEvidence(
                activity_instants=[datetime(2026, 8, 18, 9, 0, tzinfo=UTC)],
                quizzes_completed=1,
            ),
            UNKNOWN_TIMEZONE,
        )
        assert computed.flashcards_reviewed == 0
        assert computed.notes_created == 0
        assert computed.active_days == 1

    def test_untracked_time_is_null_rather_than_zero_minutes(self):
        """`QuizSession.durationSeconds` is nullable and only set when a client sends it, and
        almost nothing writes `StudySession`. Absent time means untracked far more often than
        it means idle, so it must not render as "0m focused"."""
        computed = metrics_module.compute(evidence(tracked_minutes=None), LAGOS)
        assert computed.focused_minutes is None

    def test_goals_advanced_is_left_unmeasured(self):
        """`Goal.updatedAt` moves when a title is edited, so counting it would report a renamed
        goal as an advanced one. Null until there is progress history to diff."""
        assert metrics_module.compute(evidence(), LAGOS).goals_advanced is None

    def test_a_learner_who_never_studied_has_no_streak_rather_than_a_zero_one(self):
        """`None` before the first session, `0` once a streak lapses. The design has to be able
        to show those differently."""
        assert metrics_module.compute(evidence(streak_current=None), LAGOS).streak_current is None
        assert metrics_module.compute(evidence(streak_current=0), LAGOS).streak_current == 0


class TestRatios:
    def test_recall_comes_from_the_stored_lapse_verdict(self):
        computed = metrics_module.compute(
            evidence(reviews_total=128, reviews_lapsed=14), UNKNOWN_TIMEZONE
        )
        assert computed.recall_percent == 89.1

    def test_recall_is_null_with_no_reviews_not_zero_percent(self):
        """Zero percent recall is a claim that the learner forgot everything."""
        assert (
            metrics_module.compute(
                evidence(reviews_total=0, reviews_lapsed=0), LAGOS
            ).recall_percent
            is None
        )

    def test_perfect_and_total_failure_are_both_expressible(self):
        assert metrics_module.compute(evidence(reviews_lapsed=0), LAGOS).recall_percent == 100.0
        assert (
            metrics_module.compute(
                evidence(reviews_total=10, reviews_lapsed=10), LAGOS
            ).recall_percent
            == 0.0
        )

    def test_accuracy_is_null_with_no_answers(self):
        assert (
            metrics_module.compute(
                evidence(quiz_answers_total=0, quiz_answers_correct=0), LAGOS
            ).accuracy_percent
            is None
        )

    def test_mastery_gain_is_the_difference_in_completion(self):
        """10 of 40 to 14 of 40 is 25% to 35%, so ten percentage points."""
        computed = metrics_module.compute(
            evidence(own_topics_total=40, own_topics_done_at_start=10, own_topics_done_at_end=14),
            LAGOS,
        )
        assert computed.mastery_gained_percent == 10.0

    def test_mastery_gain_is_null_when_the_learner_has_no_topics(self):
        """Dividing by an empty curriculum, rather than reporting no growth."""
        assert (
            metrics_module.compute(evidence(own_topics_total=0), LAGOS).mastery_gained_percent
            is None
        )

    def test_a_period_with_no_completions_gains_zero_rather_than_null(self):
        computed = metrics_module.compute(
            evidence(own_topics_total=40, own_topics_done_at_start=14, own_topics_done_at_end=14),
            LAGOS,
        )
        assert computed.mastery_gained_percent == 0.0


class TestTheLearnersCalendar:
    #: 23:30 UTC and 00:30 UTC are one local day apart in Lagos (UTC+1) and two in UTC.
    LATE = datetime(2026, 8, 17, 23, 30, tzinfo=UTC)
    EARLY = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)

    def test_active_days_counts_the_learners_days_not_utc_days(self):
        """The reason this matters, in the words of `_compute_consistency_score`: a session at
        23:30 in Lagos is the next day in UTC, so counting UTC dates either merges two of the
        learner's days into one or splits one across two."""
        instants = [self.LATE, self.EARLY]
        assert metrics_module.compute(evidence(activity_instants=instants), LAGOS).active_days == 1
        assert (
            metrics_module.compute(
                evidence(activity_instants=instants), UNKNOWN_TIMEZONE
            ).active_days
            == 2
        )

    def test_a_western_timezone_splits_what_utc_merges(self):
        """The same bug in the other direction, so the fix is not an off-by-one that happens to
        work east of Greenwich."""
        instants = [
            datetime(2026, 8, 18, 3, 0, tzinfo=UTC),  # 2026-08-17 20:00 in Los Angeles
            datetime(2026, 8, 18, 20, 0, tzinfo=UTC),  # 2026-08-18 13:00 in Los Angeles
        ]
        assert (
            metrics_module.compute(
                evidence(activity_instants=instants), UNKNOWN_TIMEZONE
            ).active_days
            == 1
        )
        assert (
            metrics_module.compute(evidence(activity_instants=instants), LOS_ANGELES).active_days
            == 2
        )

    def test_the_strongest_day_is_withheld_when_the_timezone_is_unknown(self):
        """`UserPreferences.timezone` is NOT NULL with a `"UTC"` default, so an unresolved
        learner looks like a learner in London. "Your strongest day is Thursday" would then be
        confidently wrong for most of the world."""
        assert metrics_module.compute(evidence(), LAGOS).best_day == "Thursday"
        assert metrics_module.compute(evidence(), UNKNOWN_TIMEZONE).best_day is None


class TestHighlights:
    def test_highlights_are_formatted_facts(self):
        built = metrics_module.build_highlights(metrics_module.compute(evidence(), LAGOS))
        assert "12-day learning streak" in built
        assert "5h 26m focused" in built

    def test_a_single_day_is_not_pluralised(self):
        """These strings render verbatim on the page, so "1 active days" is a visible defect."""
        built = metrics_module.build_highlights(
            metrics_module.compute(
                evidence(activity_instants=[datetime(2026, 8, 18, 9, 0, tzinfo=UTC)]), LAGOS
            )
        )
        assert "1 active day" in built
        assert "1 active days" not in built

    def test_a_null_metric_produces_no_chip_rather_than_a_zero_chip(self):
        built = metrics_module.build_highlights(
            metrics_module.compute(metrics_module.MetricEvidence(), UNKNOWN_TIMEZONE)
        )
        assert built == []

    def test_highlights_are_bounded(self):
        """The design lays out a fixed row; an unbounded list would wrap over the layout."""
        assert len(metrics_module.build_highlights(metrics_module.compute(evidence(), LAGOS))) <= 4


class TestThePromptCarriesFactsAndForbidsNewOnes:
    def _prompt(self, computed):
        return reflection_service._build_prompt(
            type_=models.ReflectionType.WEEKLY,
            period_start=datetime(2026, 8, 12, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, tzinfo=UTC),
            deep=False,
            metrics=computed,
        )

    def test_measured_figures_are_supplied_to_the_model(self):
        prompt = self._prompt(metrics_module.compute(evidence(), LAGOS))
        assert "Flashcards reviewed: 128" in prompt
        # `86`, not `86.0`. The brief lets the model restate a figure verbatim, so a score that is a
        # whole number must not be handed over wearing a decimal point it would then print.
        assert "Consistency score: 86" in prompt
        assert "Consistency score: 86.0" not in prompt
        assert "Graph representations" in prompt

    def test_the_model_is_forbidden_from_computing_a_new_figure(self):
        prompt = self._prompt(metrics_module.compute(evidence(), LAGOS))
        assert "must not" in prompt
        assert "compute a new one" in prompt

    def test_null_metrics_are_omitted_rather_than_labelled_unknown(self):
        """A prompt line reading "Recall: not measured" invites a paragraph explaining a gap the
        learner did not ask about."""
        prompt = self._prompt(
            metrics_module.compute(evidence(reviews_total=0, reviews_lapsed=0), LAGOS)
        )
        assert "Recall" not in prompt

    def test_a_learner_with_nothing_measured_gets_a_no_statistics_brief(self):
        prompt = self._prompt(
            metrics_module.compute(metrics_module.MetricEvidence(), UNKNOWN_TIMEZONE)
        )
        assert "Nothing was measured" in prompt
        assert "state no figures at all" in prompt

    def test_no_metric_label_duplicates_another(self):
        """Two lines both reading "Topics completed", one a count and one a list, is an
        invitation to add them together."""
        prompt = self._prompt(metrics_module.compute(evidence(), LAGOS))
        labels = [line.split(":")[0] for line in prompt.splitlines() if line.startswith("- ")]
        assert len(labels) == len(set(labels)), labels


class TestGoalRisk:
    CREATED = datetime(2026, 8, 1, tzinfo=UTC)
    TARGET = datetime(2026, 8, 31, tzinfo=UTC)

    def test_a_goal_keeping_pace_is_not_at_risk(self):
        # Half the window elapsed, half the work done.
        assert not aggregates.is_at_risk(
            progress=50.0,
            created_at=self.CREATED,
            target_date=self.TARGET,
            now=datetime(2026, 8, 16, tzinfo=UTC),
        )

    def test_a_goal_lagging_beyond_the_threshold_is_at_risk(self):
        assert aggregates.is_at_risk(
            progress=10.0,
            created_at=self.CREATED,
            target_date=self.TARGET,
            now=datetime(2026, 8, 16, tzinfo=UTC),
        )

    def test_a_goal_without_a_deadline_is_never_at_risk(self):
        """There is no pace to fall behind without a deadline, and calling an open-ended goal
        "needs attention" invents a commitment the learner never made."""
        assert not aggregates.is_at_risk(
            progress=1.0,
            created_at=self.CREATED,
            target_date=None,
            now=datetime(2027, 1, 1, tzinfo=UTC),
        )

    def test_an_overdue_unfinished_goal_is_at_risk(self):
        assert aggregates.is_at_risk(
            progress=90.0,
            created_at=self.CREATED,
            target_date=self.TARGET,
            now=datetime(2026, 9, 5, tzinfo=UTC),
        )

    def test_a_finished_goal_is_never_at_risk_even_when_overdue(self):
        assert not aggregates.is_at_risk(
            progress=100.0,
            created_at=self.CREATED,
            target_date=self.TARGET,
            now=datetime(2026, 9, 5, tzinfo=UTC),
        )

    def test_the_threshold_is_a_named_constant_shared_by_every_surface(self):
        """`/goals` and `/reflect/goals` labelling the same goal differently is the failure this
        prevents."""
        assert aggregates.AT_RISK_LAG_POINTS == 15.0


class TestSubjectChangeIsNotInvented:
    def test_a_subject_reports_no_delta_until_there_is_history(self):
        """`Course.progress` is mutable in place, so yesterday's value is not recoverable and a
        delta cannot be derived from a single current number."""
        subject = aggregates.SubjectMastery(
            course_id="c1",
            title="Algorithms",
            category="Computer Science",
            mastery_percent=82.0,
            topics_total=28,
            topics_completed=23,
        )
        assert subject.change is None


class TestReflectionStreakCountsEngagement:
    @pytest.mark.anyio
    async def test_never_opened_is_null_not_zero(self, monkeypatch):
        from src.domains.personal_learning import repository

        async def none_opened(user_id, *, type_filter=None, limit=60, session=None):
            return []

        monkeypatch.setattr(
            repository.personal_learning_repo, "list_opened_reflection_periods", none_opened
        )
        assert await metrics_module.count_reflection_streak(user_id="u1") is None

    @pytest.mark.anyio
    async def test_a_lapsed_streak_is_zero_not_null(self, monkeypatch):
        """ "Never engaged" and "broke a streak" have to stay distinguishable."""
        from src.domains.personal_learning import repository

        stale = datetime.now(UTC) - timedelta(days=40)

        async def long_ago(user_id, *, type_filter=None, limit=60, session=None):
            return [stale]

        monkeypatch.setattr(
            repository.personal_learning_repo, "list_opened_reflection_periods", long_ago
        )
        assert await metrics_module.count_reflection_streak(user_id="u1") == 0

    @pytest.mark.anyio
    async def test_consecutive_opened_weeks_count(self, monkeypatch):
        from src.domains.personal_learning import repository

        now = datetime.now(UTC)
        periods = [now - timedelta(days=offset) for offset in (0, 7, 14)]

        async def three_weeks(user_id, *, type_filter=None, limit=60, session=None):
            return periods

        monkeypatch.setattr(
            repository.personal_learning_repo, "list_opened_reflection_periods", three_weeks
        )
        assert await metrics_module.count_reflection_streak(user_id="u1") == 3

    @pytest.mark.anyio
    async def test_a_gap_ends_the_run(self, monkeypatch):
        from src.domains.personal_learning import repository

        now = datetime.now(UTC)
        # This week, last week, then a month earlier.
        periods = [now, now - timedelta(days=7), now - timedelta(days=40)]

        async def with_gap(user_id, *, type_filter=None, limit=60, session=None):
            return periods

        monkeypatch.setattr(
            repository.personal_learning_repo, "list_opened_reflection_periods", with_gap
        )
        assert await metrics_module.count_reflection_streak(user_id="u1") == 2


@pytest.fixture
def anyio_backend():
    return "asyncio"
