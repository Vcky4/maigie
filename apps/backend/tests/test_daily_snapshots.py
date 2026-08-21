"""Daily learning snapshots — the only history behind Reflect's trends.

Three properties carry the weight here, and each of them is a defect that was live in the code
before these tests existed rather than a hypothetical.

**The day is the learner's, not UTC's.** A session at 23:30 in Lagos is the next day in UTC, so
bucketing by UTC date either merges two of the learner's days or splits one across two. The
readiness snapshot writer truncates to a UTC date and is on record as having that bug; these
tests pin that this one does not.

**Consistency must be replayed for the day being recorded.** `_compute_consistency_score` cut its
window off relative to `datetime.now(UTC)`, so replaying it over a historical day dropped every
session as out-of-window and returned `0.0` — a fabricated measurement that looks exactly like a
real one. `test_a_past_day_without_as_of_would_have_scored_zero` is the regression.

**A reconstruction must not overwrite a measurement.** Rows written on the day measured mastery
against the topic count as it stood then; a backfill measures against today's. Letting the
backfill win would downgrade real rows to estimates and flip their flag to say so.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.domains.personal_learning.services import (
    behaviour_service,
    reflect_aggregates,
)
from src.domains.personal_learning.services import (
    daily_snapshot_service as snapshots,
)
from src.shared.time import UNKNOWN_TIMEZONE, LearnerTimezone

OWNER = "user-owner"

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
)
NEW_YORK = LearnerTimezone(
    zone=ZoneInfo("America/New_York"), name="America/New_York", is_known=True, source="DEVICE"
)


def _evidence(**overrides) -> SimpleNamespace:
    """A `MetricEvidence` stand-in. Only the fields `snapshot_values` reads."""
    defaults = {
        "activity_instants": [datetime(2026, 7, 12, 9, 0, tzinfo=UTC)],
        "tracked_minutes": 60.0,
        "study_sessions_ended": 1,
        "quizzes_completed": 1,
        "quiz_answers_total": 10,
        "reviews_total": 20,
        "reviews_lapsed": 4,
        "topics_touched": {"topic-1", "topic-2"},
        "topics_mastered": ["Topic One"],
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRepo:
    def __init__(self):
        # (user_id, snapshot_date) -> values
        self.snapshots: dict[tuple[str, date], dict] = {}
        self.profiles: list[SimpleNamespace] = []
        self.upserts = 0
        #: Days and learners the write refuses, so failure isolation can be exercised.
        self.fail_on: set[date] = set()
        self.fail_users: set[str] = set()

    def add_profile(self, user_id: str):
        self.profiles.append(SimpleNamespace(user_id=user_id, consistency_score=None))

    def add_snapshot(self, user_id: str, snapshot_date: date, **values):
        self.snapshots[(user_id, snapshot_date)] = {"reconstructed": False, **values}

    async def upsert_daily_snapshot(self, *, user_id: str, snapshot_date: date, values: dict):
        if snapshot_date in self.fail_on or user_id in self.fail_users:
            raise RuntimeError("write refused")
        self.upserts += 1
        self.snapshots[(user_id, snapshot_date)] = dict(values)
        return SimpleNamespace(user_id=user_id, snapshot_date=snapshot_date, **values)

    async def list_daily_snapshots(self, user_id: str, *, since: date, until: date | None = None):
        rows = [
            SimpleNamespace(user_id=uid, snapshot_date=day, **values)
            for (uid, day), values in self.snapshots.items()
            if uid == user_id and day >= since and (until is None or day <= until)
        ]
        return sorted(rows, key=lambda row: row.snapshot_date)

    async def list_active_profiles(self, *, skip: int = 0, take: int = 100):
        return self.profiles[skip : skip + take]


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()

    # `capture_day` imports the repo inside the function body, so the module attribute has to be
    # patched where it is looked up rather than on the service.
    import src.domains.personal_learning.repository as repository_module

    monkeypatch.setattr(repository_module, "personal_learning_repo", fake)
    return fake


@pytest.fixture
def sources(monkeypatch):
    """Serve evidence, mastery, timezone and session history without a database."""
    state = SimpleNamespace(
        evidence=_evidence(),
        mastery=reflect_aggregates.MasteryOnDay(
            by_course={"course-1": 50.0}, overall_percent=50.0, topics_total=4, topics_completed=2
        ),
        timezone=LAGOS,
        session_times=[],
        days_asked=[],
    )

    async def _load_evidence(*, user_id, period_start, period_end):
        state.days_asked.append((period_start, period_end))
        return state.evidence

    async def _mastery_on(*, user_id, as_of):
        return state.mastery

    async def _resolve_many(user_ids):
        return {user_id: state.timezone for user_id in user_ids}

    async def _resolve_one(user_id):
        return state.timezone

    async def _load_session_times(*, user_id, since, timezone_):
        return list(state.session_times)

    from src.domains.personal_learning.services import reflection_metrics
    from src.shared import time as shared_time

    monkeypatch.setattr(reflection_metrics, "load_evidence", _load_evidence)
    monkeypatch.setattr(reflect_aggregates, "subject_mastery_on", _mastery_on)
    monkeypatch.setattr(shared_time, "resolve_many", _resolve_many)
    monkeypatch.setattr(shared_time, "resolve_learner_timezone", _resolve_one)
    monkeypatch.setattr(behaviour_service, "load_local_session_times", _load_session_times)
    return state


# ---------------------------------------------------------------------------
# TestEffortScore
# ---------------------------------------------------------------------------


class TestEffortScore:
    def test_no_work_is_a_measured_zero_not_an_absence(self):
        """Effort is *defined* as the volume of its four inputs, so none of them is zero.

        Deliberately unlike `recallPercent`, which is null when no card was reviewed because
        nothing observed the learner's recall. A day off is a fact about the week and should read
        as one rather than as a gap in the record.
        """
        assert (
            snapshots.compute_effort_score(
                focused_minutes=None, cards_reviewed=0, quiz_answers=0, topics_touched=0
            )
            == 0.0
        )

    def test_every_input_at_its_cap_is_one_hundred(self):
        assert (
            snapshots.compute_effort_score(
                focused_minutes=snapshots.EFFORT_MINUTES_CAP,
                cards_reviewed=int(snapshots.EFFORT_CARDS_CAP),
                quiz_answers=int(snapshots.EFFORT_QUIZ_ANSWERS_CAP),
                topics_touched=int(snapshots.EFFORT_TOPICS_CAP),
            )
            == 100.0
        )

    def test_exceeding_a_cap_adds_nothing(self):
        """The caps are the point at which more of one input stops being evidence."""
        at_cap = snapshots.compute_effort_score(
            focused_minutes=snapshots.EFFORT_MINUTES_CAP,
            cards_reviewed=0,
            quiz_answers=0,
            topics_touched=0,
        )
        far_past = snapshots.compute_effort_score(
            focused_minutes=snapshots.EFFORT_MINUTES_CAP * 10,
            cards_reviewed=0,
            quiz_answers=0,
            topics_touched=0,
        )
        assert at_cap == far_past

    def test_each_input_contributes_its_own_weight(self):
        """A single input at its cap scores exactly that input's weight, times 100."""
        assert snapshots.compute_effort_score(
            focused_minutes=snapshots.EFFORT_MINUTES_CAP,
            cards_reviewed=0,
            quiz_answers=0,
            topics_touched=0,
        ) == pytest.approx(snapshots.EFFORT_MINUTES_WEIGHT * 100)
        assert snapshots.compute_effort_score(
            focused_minutes=None,
            cards_reviewed=int(snapshots.EFFORT_CARDS_CAP),
            quiz_answers=0,
            topics_touched=0,
        ) == pytest.approx(snapshots.EFFORT_CARDS_WEIGHT * 100)

    def test_time_outweighs_the_others_individually(self):
        """Weighted toward time because it is the input every learner produces."""
        assert snapshots.EFFORT_MINUTES_WEIGHT > max(
            snapshots.EFFORT_CARDS_WEIGHT,
            snapshots.EFFORT_QUIZ_ANSWERS_WEIGHT,
            snapshots.EFFORT_TOPICS_WEIGHT,
        )

    def test_the_weights_sum_to_one(self):
        """Otherwise the score could not reach 100, or could exceed it."""
        assert (
            snapshots.EFFORT_MINUTES_WEIGHT
            + snapshots.EFFORT_CARDS_WEIGHT
            + snapshots.EFFORT_QUIZ_ANSWERS_WEIGHT
            + snapshots.EFFORT_TOPICS_WEIGHT
        ) == pytest.approx(1.0)

    def test_negative_inputs_clamp_rather_than_subtract(self):
        """Nothing should write these, but a negative must not reduce a real score."""
        assert (
            snapshots.compute_effort_score(
                focused_minutes=-500, cards_reviewed=-5, quiz_answers=-5, topics_touched=-5
            )
            == 0.0
        )

    def test_score_never_leaves_the_zero_to_one_hundred_range(self):
        for minutes, cards, answers, topics in (
            (0, 0, 0, 0),
            (10, 1, 1, 1),
            (180, 40, 20, 3),
            (10_000, 10_000, 10_000, 10_000),
        ):
            score = snapshots.compute_effort_score(
                focused_minutes=minutes,
                cards_reviewed=cards,
                quiz_answers=answers,
                topics_touched=topics,
            )
            assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# TestLearnerLocalDay
# ---------------------------------------------------------------------------


class TestLearnerLocalDay:
    def test_late_evening_east_of_utc_belongs_to_the_next_utc_day(self):
        """The case Decision D names, and the one a UTC-truncating writer gets wrong.

        23:30 UTC is 00:30 the following morning in Lagos. The learner's calendar says the new
        day; UTC says the old one.
        """
        instant = datetime(2026, 7, 12, 23, 30, tzinfo=UTC)

        assert snapshots.snapshot_day_for(instant, LAGOS) == date(2026, 7, 13)
        assert instant.date() == date(2026, 7, 12)

    def test_an_unknown_timezone_falls_back_to_utc(self):
        """A limitation of the learner's data, not a claim about where they are."""
        instant = datetime(2026, 7, 12, 23, 30, tzinfo=UTC)

        assert snapshots.snapshot_day_for(instant, UNKNOWN_TIMEZONE) == date(2026, 7, 12)
        assert UNKNOWN_TIMEZONE.is_known is False

    def test_bounds_cover_the_whole_local_day_and_nothing_else(self):
        day = snapshots.local_day_bounds(date(2026, 7, 13), LAGOS)

        assert day.day == date(2026, 7, 13)
        # Lagos is UTC+1, so its day starts an hour before UTC's.
        assert day.start == datetime(2026, 7, 12, 23, 0, tzinfo=UTC)
        assert snapshots.snapshot_day_for(day.start, LAGOS) == date(2026, 7, 13)
        assert snapshots.snapshot_day_for(day.end, LAGOS) == date(2026, 7, 13)
        assert snapshots.snapshot_day_for(day.start - timedelta(seconds=1), LAGOS) == date(
            2026, 7, 12
        )

    @pytest.mark.parametrize(
        ("day", "hours"),
        [
            (date(2026, 3, 8), 23.0),  # spring forward
            (date(2026, 11, 1), 25.0),  # fall back
            (date(2026, 6, 1), 24.0),
        ],
    )
    def test_a_dst_day_is_still_exactly_one_row(self, day, hours):
        """23 and 25 hour days exist. Each is one of the learner's days regardless."""
        bounds = snapshots.local_day_bounds(day, NEW_YORK)

        assert (bounds.end - bounds.start).total_seconds() == pytest.approx(hours * 3600, abs=1)
        assert bounds.day == day

    def test_recent_days_are_contiguous_oldest_first_and_do_not_overlap(self):
        days = snapshots.recent_local_days(
            days=5, timezone_=LAGOS, now=datetime(2026, 7, 13, 5, 0, tzinfo=UTC)
        )

        assert [d.day for d in days] == [
            date(2026, 7, 9),
            date(2026, 7, 10),
            date(2026, 7, 11),
            date(2026, 7, 12),
            date(2026, 7, 13),
        ]
        for earlier, later in zip(days, days[1:], strict=False):
            assert earlier.end < later.start


# ---------------------------------------------------------------------------
# TestConsistencyReplay
# ---------------------------------------------------------------------------


class TestConsistencyReplay:
    def test_a_past_day_without_as_of_would_have_scored_zero(self):
        """The regression this parameter exists for.

        Ten consecutive days of practice, replayed long afterwards. Anchored to the window so the
        test cannot rot: the sessions sit far enough back that a now-relative cutoff drops all of
        them, and the old code then reported `0.0` — indistinguishable from a learner who did
        nothing, and stored as though measured.
        """
        as_of = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        start = as_of - timedelta(days=behaviour_service.BEHAVIOUR_WINDOW_DAYS + 60)
        times = [start + timedelta(days=offset) for offset in range(10)]

        assert behaviour_service._compute_consistency_score(times) == 0.0
        assert behaviour_service.consistency_score_from(
            times, as_of=times[-1] + timedelta(hours=1)
        ) == pytest.approx(100.0)

    def test_nothing_in_the_window_is_none_rather_than_zero(self):
        """A learner who had not started yet has no consistency score.

        Zero would draw a floor on the chart they never stood on, and it would be
        indistinguishable from a learner who was active and then stopped.
        """
        as_of = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        long_ago = [as_of - timedelta(days=400)]

        assert behaviour_service.consistency_score_from(long_ago, as_of=as_of) is None
        assert behaviour_service.consistency_score_from([], as_of=as_of) is None

    def test_work_after_the_day_is_not_visible_to_it(self):
        """A score for a past day must not see work the learner had not done yet."""
        first_three = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        times = [first_three + timedelta(days=offset) for offset in range(10)]

        # As of the third day only three days exist, all of them practised.
        assert behaviour_service.consistency_score_from(
            times, as_of=datetime(2026, 8, 3, 23, 59, tzinfo=UTC)
        ) == pytest.approx(100.0)

    def test_practising_every_other_day_scores_below_full(self):
        times = [datetime(2026, 8, 1, 9, 0, tzinfo=UTC) + timedelta(days=2 * n) for n in range(5)]

        score = behaviour_service.consistency_score_from(
            times, as_of=datetime(2026, 8, 9, 23, 59, tzinfo=UTC)
        )

        # Five days practised across a nine-day span.
        assert score == pytest.approx(5 / 9 * 100)


# ---------------------------------------------------------------------------
# TestSnapshotValues
# ---------------------------------------------------------------------------


class TestSnapshotValues:
    def test_recall_is_null_when_no_card_was_reviewed(self):
        """Not-measured is not zero: nothing observed this learner's recall."""
        values = snapshots.snapshot_values(
            _evidence(reviews_total=0, reviews_lapsed=0),
            consistency_score=None,
            overall_mastery_percent=None,
            subject_mastery=None,
        )

        assert values["recall_percent"] is None
        assert values["cards_reviewed"] == 0

    def test_recall_comes_from_the_stored_per_row_verdict(self):
        values = snapshots.snapshot_values(
            _evidence(reviews_total=20, reviews_lapsed=4),
            consistency_score=None,
            overall_mastery_percent=None,
            subject_mastery=None,
        )

        assert values["recall_percent"] == pytest.approx(80.0)

    def test_a_day_with_no_activity_at_all_is_not_active(self):
        values = snapshots.snapshot_values(
            _evidence(activity_instants=[]),
            consistency_score=None,
            overall_mastery_percent=None,
            subject_mastery=None,
        )

        assert values["active_day"] is False

    def test_activity_counts_work_no_counter_here_measures(self):
        """`activeDay` is attendance, and a knowledge-check attempt is attendance.

        It reads `activity_instants`, the union the evidence loader assembles, rather than
        summing the counters — so a learner who only attempted a check still shows up.
        """
        values = snapshots.snapshot_values(
            _evidence(
                activity_instants=[datetime(2026, 7, 12, 9, 0, tzinfo=UTC)],
                tracked_minutes=None,
                study_sessions_ended=0,
                quizzes_completed=0,
                reviews_total=0,
                quiz_answers_total=0,
                topics_mastered=[],
            ),
            consistency_score=None,
            overall_mastery_percent=None,
            subject_mastery=None,
        )

        assert values["active_day"] is True
        assert values["sessions_completed"] == 0
        assert values["focused_minutes"] is None

    def test_sessions_completed_counts_both_sources(self):
        values = snapshots.snapshot_values(
            _evidence(study_sessions_ended=2, quizzes_completed=3),
            consistency_score=None,
            overall_mastery_percent=None,
            subject_mastery=None,
        )

        assert values["sessions_completed"] == 5

    def test_passed_in_figures_are_stored_untouched(self):
        """The caller owns consistency and mastery, because only it knows which day it is on."""
        values = snapshots.snapshot_values(
            _evidence(),
            consistency_score=42.5,
            overall_mastery_percent=61.0,
            subject_mastery={"course-1": 61.0},
            reconstructed=True,
        )

        assert values["consistency_score"] == 42.5
        assert values["overall_mastery_percent"] == 61.0
        assert values["subject_mastery"] == {"course-1": 61.0}
        assert values["reconstructed"] is True

    def test_values_cover_every_writable_column(self):
        """A missing key would be a column silently left at its default on every write."""
        from src.domains.personal_learning.db_models import DailyLearningSnapshot

        # Read off the mapper, not the table: `Column.key` is the *database* name here, and the
        # upsert is keyed by Python attribute name because it goes through `setattr` and kwargs.
        managed = {"id", "user_id", "snapshot_date", "created_at", "updated_at"}
        expected = {
            attribute.key
            for attribute in DailyLearningSnapshot.__mapper__.column_attrs
            if attribute.key not in managed
        }

        produced = set(
            snapshots.snapshot_values(
                _evidence(),
                consistency_score=None,
                overall_mastery_percent=None,
                subject_mastery=None,
            )
        )

        assert produced == expected


# ---------------------------------------------------------------------------
# TestCapture
# ---------------------------------------------------------------------------


class TestCapture:
    async def test_records_the_previous_local_day_not_today(self, repo, sources):
        """A single nightly UTC run cannot be end-of-day everywhere.

        At 01:15 UTC a learner in Lagos is already into the new day, so recording "today" would
        store ninety minutes of it and mark the day finished.
        """
        now = datetime(2026, 7, 13, 1, 15, tzinfo=UTC)

        written = await snapshots.capture_for_users([OWNER], now=now)

        assert written == 1
        assert (OWNER, date(2026, 7, 12)) in repo.snapshots
        assert (OWNER, date(2026, 7, 13)) not in repo.snapshots

    async def test_the_evidence_window_is_the_learner_local_day(self, repo, sources):
        await snapshots.capture_for_users([OWNER], now=datetime(2026, 7, 13, 1, 15, tzinfo=UTC))

        period_start, period_end = sources.days_asked[-1]
        # Lagos is UTC+1: 12 July local runs 23:00 on the 11th to 22:59:59 on the 12th, in UTC.
        assert period_start == datetime(2026, 7, 11, 23, 0, tzinfo=UTC)
        assert period_end.astimezone(UTC).hour == 22

    async def test_recapturing_the_same_day_updates_rather_than_duplicating(self, repo, sources):
        now = datetime(2026, 7, 13, 1, 15, tzinfo=UTC)

        await snapshots.capture_for_users([OWNER], now=now)
        await snapshots.capture_for_users([OWNER], now=now)

        assert len([key for key in repo.snapshots if key[0] == OWNER]) == 1
        assert repo.upserts == 2

    async def test_a_nightly_row_is_not_marked_reconstructed(self, repo, sources):
        await snapshots.capture_for_users([OWNER], now=datetime(2026, 7, 13, 1, 15, tzinfo=UTC))

        assert repo.snapshots[(OWNER, date(2026, 7, 12))]["reconstructed"] is False

    async def test_one_learner_failing_does_not_cost_the_batch(self, repo, sources):
        """A failure costs one row, not the night's work.

        The write is idempotent and the backfill can recover the missed day, so swallowing and
        logging is the right trade — but only if the loop genuinely continues past it, which is
        what this pins.
        """
        repo.fail_users = {"learner-b"}

        written = await snapshots.capture_for_users(
            ["learner-a", "learner-b", "learner-c"],
            now=datetime(2026, 7, 13, 1, 15, tzinfo=UTC),
        )

        assert written == 2
        assert (OWNER, date(2026, 7, 12)) not in repo.snapshots
        assert ("learner-a", date(2026, 7, 12)) in repo.snapshots
        assert ("learner-b", date(2026, 7, 12)) not in repo.snapshots
        assert ("learner-c", date(2026, 7, 12)) in repo.snapshots

    async def test_no_users_is_not_an_error(self, repo, sources):
        assert await snapshots.capture_for_users([]) == 0

    async def test_capture_all_pages_through_every_profile(self, repo, sources):
        for index in range(3):
            repo.add_profile(f"user-{index}")

        written, seen = await snapshots.capture_all(now=datetime(2026, 7, 13, 1, 15, tzinfo=UTC))

        assert (written, seen) == (3, 3)
        assert len(repo.snapshots) == 3


# ---------------------------------------------------------------------------
# TestBackfill
# ---------------------------------------------------------------------------


class TestBackfill:
    async def test_reconstructed_rows_are_flagged(self, repo, sources):
        written = await snapshots.backfill_for_user(
            user_id=OWNER, days=3, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert written == 3
        assert all(values["reconstructed"] is True for values in repo.snapshots.values())

    async def test_it_reconstructs_finished_days_only(self, repo, sources):
        """Today is still in progress, and storing a partial day as complete is the failure the
        nightly schedule exists to avoid."""
        await snapshots.backfill_for_user(
            user_id=OWNER, days=3, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert sorted(day for _, day in repo.snapshots) == [
            date(2026, 7, 10),
            date(2026, 7, 11),
            date(2026, 7, 12),
        ]

    async def test_a_day_already_recorded_is_left_alone(self, repo, sources):
        """A nightly row measured mastery against that day's topic count, which is exact. A
        reconstruction uses today's, which is not — so overwriting would downgrade a real
        measurement and flip its flag to say it was estimated."""
        repo.add_snapshot(OWNER, date(2026, 7, 12), overall_mastery_percent=99.0)

        written = await snapshots.backfill_for_user(
            user_id=OWNER, days=3, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert written == 2
        untouched = repo.snapshots[(OWNER, date(2026, 7, 12))]
        assert untouched["overall_mastery_percent"] == 99.0
        assert untouched["reconstructed"] is False

    async def test_a_fully_recorded_window_writes_nothing(self, repo, sources):
        for offset in (1, 2, 3):
            repo.add_snapshot(OWNER, date(2026, 7, 13) - timedelta(days=offset))

        written = await snapshots.backfill_for_user(
            user_id=OWNER, days=3, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert written == 0
        assert repo.upserts == 0

    async def test_session_history_is_loaded_once_for_the_whole_window(
        self, repo, sources, monkeypatch
    ):
        """Ninety days reconstructed with a query per day would be ninety queries per learner."""
        calls = []

        async def _counting_loader(*, user_id, since, timezone_):
            calls.append(since)
            return []

        monkeypatch.setattr(behaviour_service, "load_local_session_times", _counting_loader)

        await snapshots.backfill_for_user(
            user_id=OWNER, days=10, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert len(calls) == 1

    async def test_one_bad_day_does_not_abandon_the_rest(self, repo, sources):
        repo.fail_on = {date(2026, 7, 11)}

        written = await snapshots.backfill_for_user(
            user_id=OWNER, days=3, now=datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        )

        assert written == 2
        assert (OWNER, date(2026, 7, 11)) not in repo.snapshots
        assert (OWNER, date(2026, 7, 10)) in repo.snapshots
        assert (OWNER, date(2026, 7, 12)) in repo.snapshots

    async def test_the_default_window_is_the_longest_range_the_design_offers(self):
        assert snapshots.BACKFILL_DAYS == 90
        assert snapshots.MAX_TREND_DAYS == 90
