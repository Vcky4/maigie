"""Reflect Phase 4's endpoints: growth trends, subjects, the dashboard, and the narrative composer.

The cases here are chosen for the claims that would otherwise go unchecked, and several of them cover
rules that were live defects earlier in this programme.

**Trend differencing over a range with gaps** was deferred from Phase 3, which could not write it: the
trend read did not exist yet. It is the case that matters most, because a gap filled with zero and a
gap filled by carrying the previous value forward are both fabrications and both look plausible.

**A locked range is a `200`, not a `403`.** Free must be able to press the third toggle.

**The model never supplies a number, and never chooses a target.** Decisions A and O, pinned by
inspecting what the prompt is handed and what the composer keeps.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning import models
from src.domains.personal_learning.services import (
    growth_service,
    reflect_dashboard_service,
    reflection_narrative,
    reflection_service,
)

OWNER = "user-owner"
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
#: The window ends at yesterday, because the writer records the last *finished* local day.
YESTERDAY = date(2026, 8, 21)


def _snapshot(day: date, **overrides) -> SimpleNamespace:
    defaults = {
        "snapshot_date": day,
        "focused_minutes": 30.0,
        "sessions_completed": 1,
        "active_day": True,
        "consistency_score": 50.0,
        "overall_mastery_percent": 10.0,
        "cards_reviewed": 5,
        "recall_percent": 80.0,
        "topics_completed": 0,
        "effort_score": 20.0,
        "subject_mastery": {"course-1": 10.0},
        "reconstructed": False,
    }
    return SimpleNamespace(**{**defaults, **overrides})


@pytest.fixture
def snapshots(monkeypatch):
    """Serve `list_daily_snapshots` from a list, filtered the way the real one filters."""
    store: list[SimpleNamespace] = []

    async def _list(user_id, *, since, until=None, session=None):
        # Sorted oldest-first, because that is what the real `list_daily_snapshots` guarantees and
        # what every delta here depends on. A fake that returned insertion order would let the
        # service look correct while `first`/`last` were reversed.
        return sorted(
            (
                s
                for s in store
                if s.snapshot_date >= since and (until is None or s.snapshot_date <= until)
            ),
            key=lambda s: s.snapshot_date,
        )

    monkeypatch.setattr(growth_service.repo, "list_daily_snapshots", _list)
    return store


@pytest.fixture
def plus(monkeypatch):
    from src.domains.personal_learning.services import feature_tier_service

    async def _tier(user_id):
        return "plus"

    monkeypatch.setattr(feature_tier_service, "get_quality_tier", _tier)


@pytest.fixture
def free(monkeypatch):
    from src.domains.personal_learning.services import feature_tier_service

    async def _tier(user_id):
        return "free"

    async def _trial(user_id):
        return True

    monkeypatch.setattr(feature_tier_service, "get_quality_tier", _tier)
    monkeypatch.setattr(feature_tier_service, "trial_available", _trial)


# ---------------------------------------------------------------------------
# TestTrendWindow
# ---------------------------------------------------------------------------


class TestTrendWindow:
    async def test_the_window_ends_at_yesterday(self, snapshots, free):
        """The newest day the snapshot can hold.

        The nightly writer records each learner's last *finished* local day, so today has no row by
        design. Ending the window at today made every range report one fewer captured day than it
        held — a 7-day chart showing six points, which reads as missing data rather than as the shape
        of the schedule.
        """
        snapshots.extend(_snapshot(YESTERDAY - timedelta(days=n)) for n in range(7))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.captured_days == 7
        assert trends.points[-1].day == YESTERDAY
        assert trends.points[0].day == YESTERDAY - timedelta(days=6)

    async def test_a_row_for_today_is_outside_the_window(self, snapshots, free):
        snapshots.append(_snapshot(NOW.date()))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.captured_days == 0

    @pytest.mark.parametrize(("range_", "days"), [("7d", 7), ("30d", 30)])
    async def test_each_range_asks_for_its_own_span(self, snapshots, free, range_, days):
        trends = await growth_service.get_trends(user_id=OWNER, range_=range_, now=NOW)
        assert trends.days == days


# ---------------------------------------------------------------------------
# TestTrendGaps
# ---------------------------------------------------------------------------


class TestTrendGaps:
    """Deferred from Phase 3 because the trend read did not exist there. The central case."""

    async def test_a_missing_day_is_absent_rather_than_zero(self, snapshots, free):
        """Zero asserts the learner failed that day; carrying forward asserts a measurement nobody
        took. Both look plausible on a chart, which is why this is pinned."""
        snapshots.append(_snapshot(YESTERDAY - timedelta(days=6)))
        snapshots.append(_snapshot(YESTERDAY))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.captured_days == 2
        assert [p.day for p in trends.points] == [
            YESTERDAY - timedelta(days=6),
            YESTERDAY,
        ]

    async def test_a_delta_spans_the_gap_using_first_and_last_measured_values(
        self, snapshots, free
    ):
        """The change is between what was actually measured, not between window edges."""
        snapshots.append(
            _snapshot(YESTERDAY - timedelta(days=6), consistency_score=20.0, effort_score=5.0)
        )
        snapshots.append(_snapshot(YESTERDAY, consistency_score=80.0, effort_score=45.0))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.consistency.first == 20.0
        assert trends.consistency.last == 80.0
        assert trends.consistency.change == 60.0
        assert trends.effort.change == 40.0

    async def test_a_null_metric_is_skipped_by_its_own_delta_only(self, snapshots, free):
        """A day that measured effort but not recall still contributes to the effort delta.

        Per-series filtering rather than dropping the whole day, because the three series are measured
        independently and a day missing one of them is not a day missing all of them.
        """
        snapshots.append(_snapshot(YESTERDAY - timedelta(days=3), consistency_score=None))
        snapshots.append(_snapshot(YESTERDAY - timedelta(days=2), consistency_score=None))
        snapshots.append(_snapshot(YESTERDAY, consistency_score=90.0, effort_score=60.0))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        # One consistency value across the range, so no change can be claimed.
        assert trends.consistency.change is None
        assert trends.consistency.last == 90.0
        # Three effort values, so a change can.
        assert trends.effort.change is not None

    async def test_one_captured_day_reports_no_change(self, snapshots, free):
        """One observation is not a trend. `change: 0` would claim a flat line nobody measured."""
        snapshots.append(_snapshot(YESTERDAY))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.captured_days == 1
        assert trends.mastery.change is None
        assert trends.mastery.last == 10.0

    async def test_an_empty_window_is_an_empty_series_not_an_error(self, snapshots, free):
        """A new learner has no history. That is a state to render, not a failure."""
        trends = await growth_service.get_trends(user_id=OWNER, range_="30d", now=NOW)

        assert trends.points == []
        assert trends.captured_days == 0
        assert trends.active_days == 0
        assert trends.mastery.change is None

    async def test_reconstructed_days_are_counted_and_flagged(self, snapshots, free):
        """The client footnotes them rather than hiding them (Decision P)."""
        snapshots.append(_snapshot(YESTERDAY - timedelta(days=1), reconstructed=True))
        snapshots.append(_snapshot(YESTERDAY, reconstructed=False))

        trends = await growth_service.get_trends(user_id=OWNER, range_="7d", now=NOW)

        assert trends.reconstructed_days == 1
        assert [p.reconstructed for p in trends.points] == [True, False]


# ---------------------------------------------------------------------------
# TestLockedRange
# ---------------------------------------------------------------------------


class TestLockedRange:
    async def test_ninety_days_on_free_is_locked_not_refused(self, snapshots, free):
        """A `200` with a notice.

        The design renders three toggles and Free must be able to press the third. An error makes the
        control look broken; quietly serving 30 days under the 90-day label is a lie told in a chart.
        """
        snapshots.extend(_snapshot(YESTERDAY - timedelta(days=n)) for n in range(40))

        trends = await growth_service.get_trends(user_id=OWNER, range_="90d", now=NOW)

        assert trends.locked is not None
        assert trends.locked.locked is True
        assert "Plus" in trends.locked.reason
        assert trends.locked.capability
        assert trends.locked.trial_available is True
        # And emphatically not a substituted shorter series.
        assert trends.points == []
        assert trends.captured_days == 0
        assert trends.days == 90

    async def test_ninety_days_on_plus_returns_the_series(self, snapshots, plus):
        snapshots.extend(_snapshot(YESTERDAY - timedelta(days=n)) for n in range(40))

        trends = await growth_service.get_trends(user_id=OWNER, range_="90d", now=NOW)

        assert trends.locked is None
        assert trends.captured_days == 40

    @pytest.mark.parametrize("range_", ["7d", "30d"])
    async def test_the_short_ranges_are_never_locked(self, snapshots, free, range_):
        """The learner's own measurements are not gated. Only history depth is."""
        trends = await growth_service.get_trends(user_id=OWNER, range_=range_, now=NOW)
        assert trends.locked is None

    def test_the_free_ranges_are_a_subset_of_the_offered_ones(self):
        assert models.FREE_GROWTH_RANGES < set(models.GROWTH_RANGE_DAYS)


# ---------------------------------------------------------------------------
# TestSubjectChange
# ---------------------------------------------------------------------------


class TestSubjectChange:
    @pytest.fixture
    def subject_source(self, monkeypatch):
        from src.domains.personal_learning.services import reflect_aggregates

        subjects = [
            reflect_aggregates.SubjectMastery(
                course_id="course-1",
                title="Linear Algebra",
                category="maths",
                mastery_percent=30.0,
                topics_total=10,
                topics_completed=3,
            )
        ]

        async def _list(*, user_id, limit=None):
            return subjects[:limit] if limit else subjects

        monkeypatch.setattr(reflect_aggregates, "list_subject_mastery", _list)
        return subjects

    async def test_change_is_differenced_from_the_snapshot(self, snapshots, free, subject_source):
        """The field that was permanently `None` from Phase 2 until the snapshot existed."""
        snapshots.append(
            _snapshot(YESTERDAY - timedelta(days=5), subject_mastery={"course-1": 12.0})
        )
        snapshots.append(_snapshot(YESTERDAY, subject_mastery={"course-1": 30.0}))

        response = await growth_service.get_subjects(user_id=OWNER, range_="30d", now=NOW)

        assert response.items[0].change == 18.0

    async def test_a_null_mastery_dict_is_not_treated_as_zero(
        self, snapshots, free, subject_source
    ):
        """A reconstruction that could not date the learner's completions stores null.

        Reading that as `0` would report every subject as having gained its entire current mastery
        inside the range — a fabricated success story.
        """
        snapshots.append(
            _snapshot(YESTERDAY - timedelta(days=5), subject_mastery=None, reconstructed=True)
        )
        snapshots.append(_snapshot(YESTERDAY, subject_mastery={"course-1": 30.0}))

        response = await growth_service.get_subjects(user_id=OWNER, range_="30d", now=NOW)

        assert response.items[0].change is None
        assert response.items[0].mastery_percent == 30.0

    async def test_a_null_dict_at_the_newest_end_does_not_erase_the_comparison(
        self, snapshots, free, subject_source
    ):
        """This is what the null filter is actually for.

        The other direction is already safe — a null earliest day yields an empty `first`, and every
        course is then absent from it, so no change is claimed. The risk is at the *newest* end: if the
        most recent day happens to carry no mastery dict, reading it as the endpoint would wipe out a
        comparison the learner has real data for. The filter finds the nearest days that hold mastery
        rather than the window's edges.
        """
        snapshots.append(
            _snapshot(YESTERDAY - timedelta(days=5), subject_mastery={"course-1": 12.0})
        )
        snapshots.append(
            _snapshot(YESTERDAY - timedelta(days=1), subject_mastery={"course-1": 30.0})
        )
        snapshots.append(_snapshot(YESTERDAY, subject_mastery=None, reconstructed=True))

        response = await growth_service.get_subjects(user_id=OWNER, range_="30d", now=NOW)

        assert response.items[0].change == 18.0

    async def test_one_snapshot_yields_no_change(self, snapshots, free, subject_source):
        snapshots.append(_snapshot(YESTERDAY, subject_mastery={"course-1": 30.0}))

        response = await growth_service.get_subjects(user_id=OWNER, range_="30d", now=NOW)

        assert response.items[0].change is None

    async def test_a_course_absent_from_the_earlier_end_gets_no_change(
        self, snapshots, free, subject_source
    ):
        """A course created mid-range has nothing to be compared against."""
        snapshots.append(_snapshot(YESTERDAY - timedelta(days=5), subject_mastery={"other": 5.0}))
        snapshots.append(_snapshot(YESTERDAY, subject_mastery={"course-1": 30.0}))

        response = await growth_service.get_subjects(user_id=OWNER, range_="30d", now=NOW)

        assert response.items[0].change is None

    async def test_another_learners_course_is_not_found(self, snapshots, free, subject_source):
        """`404`, not `403` — the id must not be probeable."""
        from src.shared.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await growth_service.get_subject_detail(
                user_id=OWNER, course_id="not-mine", range_="30d", now=NOW
            )


# ---------------------------------------------------------------------------
# TestNarrativeComposition
# ---------------------------------------------------------------------------


class TestNarrativeComposition:
    def _metrics(self, **overrides) -> models.ReflectionMetrics:
        defaults = {
            "focused_minutes": 120,
            "consistency_score": 40.0,
            "recall_percent": 55.0,
            "active_days": 3,
        }
        return models.ReflectionMetrics(**{**defaults, **overrides})

    def _subjects(self):
        from src.domains.personal_learning.services import reflect_aggregates

        return [
            reflect_aggregates.SubjectMastery(
                course_id="course-1",
                title="Linear Algebra",
                category="maths",
                mastery_percent=30.0,
                topics_total=10,
                topics_completed=3,
                change=4.0,
            )
        ]

    def test_a_ratio_is_rendered_at_one_decimal_not_full_precision(self):
        """The brief permits restating a figure *exactly as given*, so precision is an instruction.

        `consistencyScore` is a division and arrives as `57.14285714285714`. A live deep narrative
        restated all fourteen decimals into prose a learner reads, which is the model obeying the
        prompt rather than misbehaving. The figure it may repeat is now the figure we would publish.
        """
        facts = reflection_service._render_facts(
            self._metrics(consistency_score=400 / 7, active_days=4)
        )

        assert "57.1" in facts
        assert "57.14285714285714" not in facts
        # And a whole number does not acquire a decimal point.
        assert "4 days" in facts or "4" in facts

    def test_an_integer_metric_keeps_no_trailing_decimal(self):
        facts = reflection_service._render_facts(self._metrics(focused_minutes=120.0))

        assert "120.0" not in facts
        assert "120" in facts

    def test_the_narrative_prompt_rounds_the_same_way_the_summary_prompt_does(self):
        """`%g` kept six significant digits, so the narrative prompt leaked `57.1429` where the
        summary prompt had already been fixed. One rule, both prompts."""
        prompt = reflection_narrative.build_prompt(
            type_=models.ReflectionType.WEEKLY,
            period_start=NOW,
            period_end=NOW,
            facts="",
            signals=reflection_narrative.build_signals(self._metrics(consistency_score=400 / 7)),
            subjects=reflection_narrative.build_subjects(self._subjects()),
            actions=[],
        )

        assert "57.1" in prompt
        assert "57.1429" not in prompt
        assert "57.14285714285714" not in prompt

    def test_a_negative_subject_change_keeps_its_sign_in_the_prompt(self):
        """`:+g` supplied the sign; `render_figure` does not, so the caller must."""
        from src.domains.personal_learning.services import reflect_aggregates

        declining = [
            reflect_aggregates.SubjectMastery(
                course_id="course-1",
                title="Linear Algebra",
                category="maths",
                mastery_percent=30.0,
                topics_total=10,
                topics_completed=3,
                change=-3.0,
            )
        ]
        prompt = reflection_narrative.build_prompt(
            type_=models.ReflectionType.WEEKLY,
            period_start=NOW,
            period_end=NOW,
            facts="",
            signals=[],
            subjects=reflection_narrative.build_subjects(declining),
            actions=[],
        )

        assert "change -3 points" in prompt

    def test_only_measured_metrics_become_signals(self):
        """A null metric produces no card rather than a card reading zero."""
        signals = reflection_narrative.build_signals(
            self._metrics(recall_percent=None, consistency_score=None)
        )

        ids = {s.id for s in signals}
        assert "focus" in ids
        assert "recall" not in ids
        assert "consistency" not in ids

    def test_a_signal_carries_its_number_before_the_model_is_asked_anything(self):
        signals = reflection_narrative.build_signals(self._metrics())

        focus = next(s for s in signals if s.id == "focus")
        assert focus.value == 120.0
        # And no prose yet — that is the model's only job.
        assert focus.description is None

    def test_the_prompt_states_every_figure_and_forbids_new_ones(self):
        signals = reflection_narrative.build_signals(self._metrics())
        subjects = reflection_narrative.build_subjects(self._subjects())

        prompt = reflection_narrative.build_prompt(
            type_=models.ReflectionType.WEEKLY,
            period_start=NOW - timedelta(days=7),
            period_end=NOW,
            facts="- Tracked focused minutes: 120 min",
            signals=signals,
            subjects=subjects,
            actions=[],
        )

        assert "must not compute a new figure" in prompt
        # Each figure is handed over, keyed by id so a reply can be matched back.
        assert 'id "focus"' in prompt
        assert 'id "course-1"' in prompt
        assert "mastery 30%" in prompt

    def test_the_service_chooses_the_target_and_it_is_a_real_owned_id(self):
        """Decision O. A model free to emit an `entityId` would eventually cite someone else's."""
        chosen = reflection_narrative.choose_actions(
            metrics=self._metrics(), subjects=self._subjects(), limit=3
        )

        by_id = dict((id_, target) for id_, target, _ in chosen)
        assert by_id["weakest-subject"].kind is models.ReflectionActionKind.COURSE
        assert by_id["weakest-subject"].entity_id == "course-1"
        # Weak recall points at the review queue, which spans decks — so no entity id at all.
        assert by_id["recall"].kind is models.ReflectionActionKind.FLASHCARD_REVIEW
        assert by_id["recall"].entity_id is None

    def test_free_gets_one_recommendation_and_plus_gets_three(self):
        assert reflection_narrative.recommendation_limit(deep=False) == 1
        assert reflection_narrative.recommendation_limit(deep=True) == 3

        chosen_free = reflection_narrative.choose_actions(
            metrics=self._metrics(), subjects=self._subjects(), limit=1
        )
        assert len(chosen_free) == 1

    def test_an_action_ships_even_when_its_prose_is_missing(self):
        """The target is the part that has to be right; the sentence can be plain."""
        chosen = reflection_narrative.choose_actions(
            metrics=self._metrics(), subjects=self._subjects(), limit=3
        )

        actions = reflection_narrative.assemble_actions(chosen=chosen, written={})

        assert len(actions) == len(chosen)
        assert all(action.title for action in actions)
        assert all(action.target.kind for action in actions)

    def test_free_gets_a_shorter_page_not_a_holed_one(self):
        """Decision T2. Everything measured is kept; only the paid prose is absent."""
        narrative = reflection_narrative.assemble(
            deep=False,
            summary="One honest paragraph.",
            written={"closing": "should be ignored", "signals": {"focus": {"description": "x"}}},
            signals=reflection_narrative.build_signals(self._metrics()),
            subjects=reflection_narrative.build_subjects(self._subjects()),
            rhythm=[models.ReflectionRhythmDay(day="2026-08-21", minutes=30.0, active=True)],
            highlights=["3 active days"],
        )

        # Kept, because it is measured.
        assert narrative.subjects[0].mastery == 30.0
        assert narrative.subjects[0].change == 4.0
        assert narrative.rhythm
        assert narrative.highlights == ["3 active days"]
        assert narrative.opening == ["One honest paragraph."]
        # Withheld, because it is paid prose — and not smuggled in from `written`.
        assert narrative.subjects[0].insight is None
        assert narrative.signals == []
        assert narrative.closing is None
        assert narrative.patterns.keep is None

    def test_plus_gets_the_prose_attached_to_the_right_rows(self):
        narrative = reflection_narrative.assemble(
            deep=True,
            summary="fallback",
            written={
                "opening": ["First.", "Second."],
                "theme": "consolidation",
                "signals": {"focus": {"description": "Good focus.", "evidence": "120 min"}},
                "subjects": {"course-1": "Steady progress here."},
                "patterns": {"keep": {"title": "Keep", "body": "Short sessions."}},
                "closing": "Well done.",
            },
            signals=reflection_narrative.build_signals(self._metrics()),
            subjects=reflection_narrative.build_subjects(self._subjects()),
            rhythm=[],
            highlights=[],
        )

        assert narrative.opening == ["First.", "Second."]
        assert narrative.theme == "consolidation"
        assert next(s for s in narrative.signals if s.id == "focus").description == "Good focus."
        assert narrative.subjects[0].insight == "Steady progress here."
        assert narrative.patterns.keep.title == "Keep"
        assert narrative.patterns.watch is None
        assert narrative.closing == "Well done."

    def test_a_half_written_pattern_is_dropped_entirely(self):
        """A titled pattern with no body renders as a heading over blank space."""
        narrative = reflection_narrative.assemble(
            deep=True,
            summary="s",
            written={"patterns": {"keep": {"title": "Keep going"}}},
            signals=[],
            subjects=[],
            rhythm=[],
            highlights=[],
        )

        assert narrative.patterns.keep is None

    def test_a_closing_cut_off_by_truncation_is_dropped(self):
        """Found by running the deep path against a live model, not by reading the code.

        `generate_content_json` repairs a truncated reply by closing the JSON, which rescues the
        fields that arrived whole and leaves the last one mid-word. The observed result published
        "…you will start turning" as the closing quote — the largest text block on the page.
        """
        narrative = reflection_narrative.assemble(
            deep=True,
            summary="s",
            written={"closing": "Keep showing up and you will start turning"},
            signals=[],
            subjects=[],
            rhythm=[],
            highlights=[],
        )

        assert narrative.closing is None

    def test_a_finished_closing_is_kept(self):
        for ending in ("Well done.", "Well done!", "Keep going?", 'He said "go".', "Onwards…"):
            narrative = reflection_narrative.assemble(
                deep=True,
                summary="s",
                written={"closing": ending},
                signals=[],
                subjects=[],
                rhythm=[],
                highlights=[],
            )
            assert narrative.closing == ending

    def test_a_pattern_body_cut_off_by_truncation_drops_the_whole_card(self):
        """Same rule as the missing body: a fragment is worse than a gap."""
        narrative = reflection_narrative.assemble(
            deep=True,
            summary="s",
            written={"patterns": {"keep": {"title": "Short sessions", "body": "You built a streak of"}}},
            signals=[],
            subjects=[],
            rhythm=[],
            highlights=[],
        )

        assert narrative.patterns.keep is None

    def test_plus_falls_back_to_the_summary_when_the_opening_is_missing(self):
        """The hero the page is built around still renders."""
        narrative = reflection_narrative.assemble(
            deep=True,
            summary="The honest short version.",
            written={},
            signals=[],
            subjects=[],
            rhythm=[],
            highlights=[],
        )

        assert narrative.opening == ["The honest short version."]

    def test_the_rhythm_omits_days_with_no_snapshot(self):
        rhythm = reflection_narrative.build_rhythm(
            [_snapshot(YESTERDAY, focused_minutes=None, active_day=False)]
        )

        assert len(rhythm) == 1
        assert rhythm[0].minutes is None
        assert rhythm[0].active is False


# ---------------------------------------------------------------------------
# TestDashboardDegradation
# ---------------------------------------------------------------------------


class TestDashboardDegradation:
    def test_every_section_is_reachable_from_some_source(self):
        """A section no source feeds could never be reported degraded, so a failure there would
        render as an empty state — which is the confusion this whole mechanism exists to prevent."""
        covered: set[str] = set()
        for sections in reflect_dashboard_service._SOURCE_SECTIONS.values():
            covered.update(sections)

        assert covered == set(reflect_dashboard_service._SECTION_ORDER)

    def test_the_degraded_list_is_ordered_deterministically(self):
        """Set iteration order would make the list vary between identical requests."""
        order = reflect_dashboard_service._SECTION_ORDER
        assert len(order) == len(set(order))
        assert order[0] == "summary"

    def test_no_source_degrades_a_section_it_does_not_feed(self):
        sections = reflect_dashboard_service._SOURCE_SECTIONS
        assert "achievements" not in sections["trends"]
        assert "trends" not in sections["achievements"]
        assert sections["activity"] == {"activity"}



class TestSubjectActivity:
    """Per-subject activity, and the two rules that decide whether a figure may be published.

    §7.2's audit found that most of what the subject pages wanted is **not attributable to a course**:
    quiz accuracy belongs to an `ExamPrep`, flashcard reviews to a deck. What is attributable is study
    time, from `StudySession.courseId`, and knowledge-check correctness, through `Topic → Module`.
    """

    LAGOS = None  # set in `_activity`

    async def _activity(self, *, session_rows, check_rows, since=None, until=None):
        """Run `list_subject_activity`'s folding over supplied rows."""
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from src.domains.personal_learning.services import reflect_aggregates
        from src.shared.time import LearnerTimezone

        lagos = LearnerTimezone(
            zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="DEVICE"
        )

        calls = {"n": 0}

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            async def execute(self, *_a, **_k):
                calls["n"] += 1
                return _Result(session_rows if calls["n"] == 1 else check_rows)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        with patch.object(reflect_aggregates, "get_session_factory", lambda: _Session):
            return await reflect_aggregates.list_subject_activity(
                user_id="u",
                since=since or date(2026, 8, 17),
                until=until or date(2026, 8, 23),
                timezone_=lagos,
            )

    async def _by_course(self, **kwargs):
        return (await self._activity(**kwargs)).by_course

    async def test_a_learner_who_tracks_no_time_gets_nulls_not_zeros(self):
        """Nothing in `StudySession` at all means unmeasured, and `0 sessions` would be a claim.

        Phase 2 recorded that `StudySession` is written by one endpoint most learners never touch, so
        this is the common case rather than an edge one.
        """
        activity = await self._by_course(
            session_rows=[],
            check_rows=[("course-1", 4, 3)],
        )

        row = activity["course-1"]
        assert row.sessions is None
        assert row.focused_minutes is None
        assert row.active_days is None
        # The checks were measured, so they are reported.
        assert row.knowledge_checks_answered == 4
        assert row.knowledge_check_accuracy_percent == 75.0

    async def test_zero_is_published_once_the_learner_tracks_time_somewhere(self):
        """The converse of Decision I, settled by `reflection_metrics.compute`'s `had_activity` gate:
        once there is evidence the learner records time, "no sessions on this subject" is a finding."""
        activity = await self._by_course(
            # A session on another course, so tracking is evidently happening.
            session_rows=[
                ("course-other", datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 30.0),
            ],
            check_rows=[("course-1", 2, 1)],
        )

        assert activity["course-1"].sessions == 0
        assert activity["course-1"].focused_minutes == 0.0
        assert activity["course-1"].active_days == 0
        assert activity["course-other"].sessions == 1

    async def test_sessions_minutes_and_days_are_counted_per_course(self):
        activity = await self._by_course(
            session_rows=[
                ("course-1", datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 30.0),
                ("course-1", datetime(2026, 8, 20, 14, 0, tzinfo=UTC), 25.5),
                ("course-1", datetime(2026, 8, 21, 9, 0, tzinfo=UTC), 10.0),
                ("course-2", datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 5.0),
            ],
            check_rows=[],
        )

        one = activity["course-1"]
        assert one.sessions == 3
        assert one.focused_minutes == 65.5
        # Two sittings on the 20th collapse to one day.
        assert one.active_days == 2
        assert activity["course-2"].sessions == 1

    async def test_active_days_use_the_learner_calendar_not_utc(self):
        """23:30 UTC on the 20th is 00:30 on the **21st** in Lagos, so these are two local days, not
        one. Bucketing in SQL would have made them one and disagreed with
        `DailyLearningSnapshot.snapshotDate` at every boundary."""
        activity = await self._by_course(
            session_rows=[
                ("course-1", datetime(2026, 8, 20, 12, 0, tzinfo=UTC), 10.0),
                ("course-1", datetime(2026, 8, 20, 23, 30, tzinfo=UTC), 10.0),
            ],
            check_rows=[],
        )

        assert activity["course-1"].active_days == 2

    async def test_a_session_outside_the_local_window_is_excluded(self):
        """The UTC query is widened by a day on each side so a late-evening local session is not lost;
        the precise filter is then applied in the learner's own calendar."""
        activity = await self._by_course(
            session_rows=[
                # 16 Aug in Lagos — one day before the window opens.
                ("course-1", datetime(2026, 8, 16, 12, 0, tzinfo=UTC), 40.0),
                ("course-1", datetime(2026, 8, 20, 12, 0, tzinfo=UTC), 10.0),
            ],
            check_rows=[],
        )

        assert activity["course-1"].sessions == 1
        assert activity["course-1"].focused_minutes == 10.0

    async def test_accuracy_over_no_attempts_is_null_not_zero(self):
        """A percentage with an empty denominator is unmeasured. `prepare_dashboard_service` already
        follows this rule for quiz accuracy."""
        activity = await self._by_course(
            session_rows=[("course-1", datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 10.0)],
            check_rows=[],
        )

        assert activity["course-1"].knowledge_checks_answered == 0
        assert activity["course-1"].knowledge_check_accuracy_percent is None

    async def test_a_session_with_no_course_counts_as_tracking_but_no_subject(self):
        """It is evidence the learner records time — which unlocks `0` for other subjects — while
        belonging to no subject itself."""
        activity = await self._by_course(
            session_rows=[(None, datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 45.0)],
            check_rows=[("course-1", 1, 1)],
        )

        assert None not in activity
        assert activity["course-1"].sessions == 0, "tracking happened, so zero is a finding"


    async def test_a_subject_with_no_row_reads_zero_when_the_learner_tracks_time(self):
        """Found by running this against real data, not by reading it.

        `by_course` only holds courses with something recorded, so the first version returned
        `activity: null` for every other course — a dash on the page where `0 sessions` is the truth for
        a learner who tracks time and simply did not open that subject. `for_course` fills the gap using
        the learner-level `tracked_any_session` flag.
        """
        activity_map = await self._activity(
            session_rows=[("course-1", datetime(2026, 8, 20, 9, 0, tzinfo=UTC), 30.0)],
            check_rows=[],
        )

        untouched = activity_map.for_course("course-never-opened")
        assert untouched.sessions == 0
        assert untouched.focused_minutes == 0.0
        assert untouched.active_days == 0
        assert untouched.knowledge_check_accuracy_percent is None

    async def test_a_subject_with_no_row_reads_null_when_nothing_is_tracked(self):
        """The other half of the same rule: no evidence of tracking anywhere means unmeasured."""
        activity_map = await self._activity(session_rows=[], check_rows=[])

        untouched = activity_map.for_course("course-never-opened")
        assert untouched.sessions is None
        assert untouched.focused_minutes is None
        assert untouched.active_days is None


class TestSubjectActivityNaming:
    def test_the_accuracy_field_is_not_called_recall(self):
        """§7.2: quiz accuracy cannot be attributed to a course, so a field called `recall` on a subject
        would be measuring one thing and named after another. If `recall` appears here, either a join
        from `ExamPrep` to `Course` now exists — in which case this test should be replaced — or
        something has been renamed into a claim it cannot support.
        """
        fields = set(models.SubjectActivitySummary.model_fields)

        assert "knowledge_check_accuracy_percent" in fields
        assert not any("recall" in name for name in fields)



class TestConceptStatus:
    """The three-band ladder, plus the fourth state that keeps it honest.

    `mastery_band` is imported from `prep_readiness`, not redefined — the same arrangement `is_at_risk`
    has. Two sets of thresholds would mean a topic called "strong" on one screen and "needs attention" on
    another.
    """

    def test_the_ladder_is_the_shared_one(self):
        from src.domains.personal_learning.services import prep_readiness, reflect_aggregates

        assert reflect_aggregates.mastery_band is prep_readiness.mastery_band
        assert reflect_aggregates.MASTERY_STRONG_THRESHOLD == 80.0
        assert reflect_aggregates.MASTERY_FOCUS_THRESHOLD == 70.0

    def test_an_untouched_topic_is_not_started_rather_than_needing_attention(self):
        """The reason the fourth state exists.

        `mastery_band(0.0)` is `focus`, which the design renders as "Needs attention" — right for a topic
        worked and not grasped, wrong for one never opened. A live course showed 24 of 25 topics at 0%;
        without this state every one of them would have been flagged as a problem on first paint.
        """
        from src.domains.personal_learning.services import reflect_aggregates

        assert (
            reflect_aggregates.concept_status(mastery_percent=0.0, touched=False) == "not_started"
        )
        assert (
            reflect_aggregates.concept_status(mastery_percent=0.0, touched=True)
            == "needs_attention"
        )

    @pytest.mark.parametrize(
        ("percent", "expected"),
        [
            (0.0, "needs_attention"),
            (69.9, "needs_attention"),
            (70.0, "growing"),
            (79.9, "growing"),
            (80.0, "strong"),
            (100.0, "strong"),
        ],
    )
    def test_the_band_boundaries(self, percent, expected):
        from src.domains.personal_learning.services import reflect_aggregates

        assert (
            reflect_aggregates.concept_status(mastery_percent=percent, touched=True) == expected
        )


class TestConceptMastery:
    async def _concepts(self, rows):
        """Run `list_concept_mastery`'s folding over `(id, title, completed, total, done)` rows."""
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

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

        with patch.object(reflect_aggregates, "get_session_factory", lambda: _Session):
            return await reflect_aggregates.list_concept_mastery(user_id="u", course_id="c")

    async def test_a_topic_with_sections_gets_a_real_gradation(self):
        concepts = await self._concepts([("t1", "Sleep", False, 6, 3)])

        assert concepts[0].mastery_percent == 50.0
        assert concepts[0].source == "sections"
        assert concepts[0].status == "needs_attention", "half done and worked on"

    async def test_a_topic_without_sections_is_binary_and_says_so(self):
        """`Topic` has no mastery column, so 0 or 100 is all there is. `source` publishes that rather
        than letting a client read precision into a figure that has none — and in this database 94% of
        topics are in this branch."""
        incomplete, complete = await self._concepts(
            [("t1", "Cues", False, 0, 0), ("t2", "Safety", True, 0, 0)]
        )

        assert incomplete.source == "completion"
        assert incomplete.mastery_percent == 0.0
        assert incomplete.status == "not_started"
        assert complete.mastery_percent == 100.0
        assert complete.status == "strong"

    async def test_opening_one_section_counts_as_touched_before_finishing_any(self):
        """A learner part-way through the first section has started. Zero *completed* sections with a
        section count above zero is still 0%, but it is not `not_started`... unless nothing is done yet,
        which is the case here — so this pins the boundary deliberately."""
        concepts = await self._concepts([("t1", "Cues", False, 6, 0)])

        assert concepts[0].mastery_percent == 0.0
        assert concepts[0].status == "not_started", "no section finished and not marked complete"

    async def test_a_topic_marked_complete_counts_as_touched_even_with_no_sections_ticked(self):
        """Sections can be added after a topic was completed, or never ticked individually. The
        completion flag is still evidence of work."""
        concepts = await self._concepts([("t1", "Sleep", True, 4, 0)])

        assert concepts[0].completed is True
        assert concepts[0].status != "not_started"

    async def test_section_counts_are_published_alongside_the_percentage(self):
        """So a client can show "3 of 6 sections" instead of only "50%", which is the more useful
        sentence and cannot be reconstructed from a rounded percentage."""
        concepts = await self._concepts([("t1", "Sleep", False, 7, 3)])

        assert concepts[0].sections_total == 7
        assert concepts[0].sections_completed == 3

    async def test_a_course_with_no_topics_returns_an_empty_list(self):
        assert await self._concepts([]) == []


class TestConceptMasteryScope:
    def test_no_shared_course_branch_exists(self):
        """`UserTopicProgress` holds progress on *shared* courses, and this read is only ever reached for
        a course the learner owns — `list_subject_mastery` filters on `Course.userId`, and a subject is a
        course (Decision H). A shared branch here would be code no request can reach, which is worse than
        no branch because it would look maintained.
        """
        import inspect

        from src.domains.personal_learning.services import reflect_aggregates

        source = inspect.getsource(reflect_aggregates.list_concept_mastery)
        assert "UserTopicProgress" not in source.split('"""')[2], (
            "a shared-course branch appeared; either the scope changed or this is dead code"
        )

    def test_no_per_topic_knowledge_check_verdict(self):
        """`TopicCheckAttempt` is one question per topic, re-answerable after the answer is revealed, so a
        per-topic `correct` would be a coin flip dressed as mastery. Its aggregate is published at subject
        level instead, where averaging over many topics makes it mean something."""
        from src.domains.personal_learning.services import reflect_aggregates

        fields = {f.name for f in reflect_aggregates.ConceptMastery.__dataclass_fields__.values()}
        assert not any("check" in name or "correct" in name for name in fields)



class TestCourseEvidence:
    """Evidence comes from the domain tables. The feed cannot supply it.

    §7.2: `ActivityFeedEntry` has no course column — `entityType`/`entityId` are keys inside a nullable
    `context` JSON — and no writer has ever tagged an entry with `course` or `topic`, though both are in
    the `ActivityEntity` literal. A feed filter would have passed review and returned zero rows for every
    learner.
    """

    async def _evidence(self, *, topics=(), sections=(), sessions=(), checks=(), limit=12):
        """Run `list_course_evidence`'s merge over supplied rows for each of its four queries."""
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

        batches = [list(topics), list(sections), list(sessions), list(checks)]
        calls = {"n": 0}

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            async def execute(self, *_a, **_k):
                rows = batches[calls["n"]] if calls["n"] < len(batches) else []
                calls["n"] += 1
                return _Result(rows)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        with patch.object(reflect_aggregates, "get_session_factory", lambda: _Session):
            return await reflect_aggregates.list_course_evidence(
                user_id="u", course_id="c", limit=limit
            )

    async def test_the_four_sources_are_merged_newest_first(self):
        items = await self._evidence(
            topics=[("t1", "Recursion", "Module 2", datetime(2026, 8, 10, 9, 0, tzinfo=UTC))],
            sections=[("s1", "Base cases", "Recursion", datetime(2026, 8, 12, 9, 0, tzinfo=UTC))],
            sessions=[("ss1", datetime(2026, 8, 14, 9, 0, tzinfo=UTC), 34.0)],
            checks=[("c1", "Recursion", True, datetime(2026, 8, 11, 9, 0, tzinfo=UTC))],
        )

        assert [item.kind for item in items] == [
            "study_session",
            "section_completed",
            "knowledge_check",
            "topic_completed",
        ]

    async def test_a_session_carries_minutes_as_a_number_not_a_string(self):
        """The fixture had `result: "34 min"`. A number the client formats cannot disagree with the
        figure beside it, and a string cannot be summed."""
        items = await self._evidence(
            sessions=[("ss1", datetime(2026, 8, 14, 9, 0, tzinfo=UTC), 34.0)]
        )

        assert items[0].value == 34.0
        assert items[0].unit == "min"
        assert items[0].correct is None, "a study session is not correct or incorrect"

    async def test_a_knowledge_check_carries_its_verdict_and_no_value(self):
        items = await self._evidence(
            checks=[("c1", "Recursion", False, datetime(2026, 8, 11, 9, 0, tzinfo=UTC))]
        )

        assert items[0].correct is False
        assert items[0].value is None

    async def test_ids_are_namespaced_so_two_tables_cannot_collide(self):
        """Four sources with independent id spaces land in one list; an unprefixed id would let a topic
        and a session share a React key."""
        items = await self._evidence(
            topics=[("x", "T", "M", datetime(2026, 8, 10, tzinfo=UTC))],
            sessions=[("x", datetime(2026, 8, 11, tzinfo=UTC), 5.0)],
        )

        assert {item.id for item in items} == {"topic:x", "session:x"}

    async def test_the_limit_applies_after_the_merge(self):
        """Each query is capped at `limit`, so the merge reads at most four times that and then trims —
        otherwise the newest few items of one kind could crowd out newer items of another."""
        items = await self._evidence(
            topics=[
                ("t1", "A", "M", datetime(2026, 8, 1, tzinfo=UTC)),
                ("t2", "B", "M", datetime(2026, 8, 2, tzinfo=UTC)),
            ],
            sessions=[("ss1", datetime(2026, 8, 20, tzinfo=UTC), 5.0)],
            limit=2,
        )

        assert len(items) == 2
        assert items[0].kind == "study_session", "the newest item survives the trim"

    async def test_nothing_recorded_returns_an_empty_list(self):
        assert await self._evidence() == []


class TestEvidenceExcludesUndatedWork:
    def test_the_queries_require_a_completion_timestamp(self):
        """About half the completed topics in this database have no `completedAt`.

        An evidence list is a timeline, so an undated item has nowhere to sit on it. Substituting
        `updatedAt` would place the learner's work on the day a row was last touched by anything — which
        is the kind of plausible-looking date this programme exists to keep out.
        """
        import inspect

        from src.domains.personal_learning.services import reflect_aggregates

        source = inspect.getsource(reflect_aggregates.list_course_evidence)
        assert source.count("completed_at.is_not(None)") == 2, (
            "both the topic and section reads must exclude undated completions"
        )


class TestGoalEvidence:
    async def _goal_evidence(self, goal, *, course_evidence=None):
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

        async def _fake_course_evidence(*, user_id, course_id, limit=12):
            return course_evidence or []

        with patch.object(reflect_aggregates, "list_course_evidence", _fake_course_evidence):
            return await reflect_aggregates.list_goal_evidence(user_id="u", goal=goal, limit=5)

    async def test_a_course_linked_goal_reads_its_course(self):
        from types import SimpleNamespace

        item = SimpleNamespace(id="session:1")
        items = await self._goal_evidence(
            SimpleNamespace(course_id="c1", topic_id=None, prep_id=None),
            course_evidence=[item],
        )

        assert items == [item]

    async def test_an_unlinked_goal_returns_nothing_rather_than_general_activity(self):
        """One of the six goals in this database is exactly this: `manual`, with no course, topic or prep.

        Falling back to everything the learner did would attach unrelated work to a goal and make the
        panel look informative while being wrong.
        """
        from types import SimpleNamespace

        items = await self._goal_evidence(
            SimpleNamespace(course_id=None, topic_id=None, prep_id=None),
            course_evidence=[SimpleNamespace(id="should-not-appear")],
        )

        assert items == []



class TestActivityTypeCounts:
    """The activity mix, and why it is raw types rather than categories.

    `days` answers *when* things happened; this answers *what*. Both go through the shared
    `_feed_conditions`, which is what stops the three feed reads disagreeing about the same window.
    """

    def test_the_counts_share_the_feed_condition_builder(self):
        """One builder, so the paged read, the per-day counts and the per-type counts cannot describe
        different windows. Copying the predicate into a third reader is the failure this prevents."""
        import inspect

        from src.domains.personal_learning.repository import PersonalLearningRepository

        source = inspect.getsource(PersonalLearningRepository.count_feed_entries_by_type)
        assert "self._feed_conditions(" in source

    def test_the_service_does_not_group_into_categories(self):
        """The client already owns the activityType-to-category map — it needs one to choose an icon and
        a label per entry. A second grouping in the service would drift from it, and the page would end
        up showing a bar chart that disagreed with its own filter chips.
        """
        import inspect

        from src.domains.personal_learning.services import activity_feed_service

        source = inspect.getsource(activity_feed_service.list_type_counts)
        # The docstring explains the rule and names the categories, so only the body is inspected —
        # otherwise the explanation itself trips the assertion.
        body = source.split('"""')[-1]
        for category in ("practice", "mastery", "notes", "community", "milestones"):
            assert f'"{category}"' not in body, (
                f"a category grouping for {category!r} appeared in the service body"
            )

    def test_total_is_summed_from_the_day_counts_not_queried_again(self):
        """So the figure above the strip and the strip itself cannot disagree — the rule
        `reflect_dashboard_service` follows when it composes its summary from already-loaded sources."""
        import inspect

        from src.domains.personal_learning import routes

        source = inspect.getsource(routes.get_activity_daily_counts)
        assert "total=sum(count for _, count in days)" in source

    def test_the_response_carries_both_the_mix_and_the_full_vocabulary(self):
        """`byType` omits kinds with nothing in the window, so a client that wants to draw an empty bar
        for an unused type needs `availableTypes` as well."""
        fields = set(models.ActivityDayCountsResponse.model_fields)

        assert {"days", "total", "by_type", "available_types"} <= fields



class TestGrowthMilestones:
    """Two milestone tables, one published list — and why Decision Q's choice of table was wrong.

    Decision Q asked for a single Reflect milestone source and named `Achievement`. Auditing the data
    found nothing writes it: `create_achievement` is called from nowhere in `src`, its four rows are
    Prisma-era and belong to one learner, while `LearningMilestone` is written live for five. Reading
    only the named table would publish a permanently empty panel for almost everyone; reading only the
    live one would drop four real records.
    """

    async def _milestones(self, *, milestones=(), achievements=(), **kwargs):
        from types import SimpleNamespace
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

        batches = [list(milestones), list(achievements)]
        calls = {"n": 0}

        class _Scalars:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return _Scalars(self._rows)

        class _Session:
            async def execute(self, *_a, **_k):
                rows = batches[calls["n"]] if calls["n"] < len(batches) else []
                calls["n"] += 1
                return _Result(rows)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        _ = SimpleNamespace
        with patch.object(reflect_aggregates, "get_session_factory", lambda: _Session):
            return await reflect_aggregates.list_growth_milestones(user_id="u", **kwargs)

    def _milestone_row(self, milestone_id, at, row_id="m1"):
        from types import SimpleNamespace

        return SimpleNamespace(id=row_id, milestone_id=milestone_id, achieved_at=at)

    def _achievement_row(self, at, row_id="a1"):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=row_id,
            achievement_type="TOPIC_COMPLETION",
            title="10 Topics Completed",
            description="Great progress",
            icon="🏆",
            unlocked_at=at,
        )

    async def test_both_tables_appear_in_one_list_newest_first(self):
        items = await self._milestones(
            milestones=[self._milestone_row("7_day_streak", datetime(2026, 8, 20, tzinfo=UTC))],
            achievements=[self._achievement_row(datetime(2026, 8, 22, tzinfo=UTC))],
        )

        assert [item.source for item in items] == ["achievement", "milestone"]

    async def test_a_milestone_takes_its_copy_from_the_catalogue(self):
        """Title, description and icon live in `milestone_service.MILESTONES`; the row holds only an id."""
        items = await self._milestones(
            milestones=[self._milestone_row("7_day_streak", datetime(2026, 8, 20, tzinfo=UTC))]
        )

        assert items[0].title == "7-Day Study Streak"
        assert items[0].description == "Studied on seven consecutive days."
        assert items[0].kind == "streak"
        assert items[0].icon

    async def test_an_unknown_milestone_id_still_appears(self):
        """It happened. Hiding a retired milestone would make it look as though it never occurred."""
        items = await self._milestones(
            milestones=[self._milestone_row("retired_thing", datetime(2026, 8, 20, tzinfo=UTC))]
        )

        assert len(items) == 1
        assert items[0].title == "retired_thing"
        assert items[0].description is None

    async def test_ids_are_namespaced_across_the_two_tables(self):
        """Two independent id spaces in one list."""
        items = await self._milestones(
            milestones=[self._milestone_row("7_day_streak", datetime(2026, 8, 20, tzinfo=UTC), "x")],
            achievements=[self._achievement_row(datetime(2026, 8, 21, tzinfo=UTC), "x")],
        )

        assert {item.id for item in items} == {"milestone:x", "achievement:x"}

    async def test_the_window_is_inclusive_on_both_ends(self):
        items = await self._milestones(
            milestones=[
                self._milestone_row("7_day_streak", datetime(2026, 8, 17, 9, 0, tzinfo=UTC), "a"),
                self._milestone_row("first_document", datetime(2026, 8, 23, 9, 0, tzinfo=UTC), "b"),
                self._milestone_row("plan_complete", datetime(2026, 8, 24, 9, 0, tzinfo=UTC), "c"),
            ],
            since=date(2026, 8, 17),
            until=date(2026, 8, 23),
        )

        assert {item.id for item in items} == {"milestone:a", "milestone:b"}

    async def test_the_limit_applies_after_the_merge(self):
        """Sliced after merging, so the newest few of one table cannot crowd out newer rows of the
        other — which is exactly what slicing per table would have done."""
        items = await self._milestones(
            milestones=[
                self._milestone_row("7_day_streak", datetime(2026, 8, 1, tzinfo=UTC), "old"),
            ],
            achievements=[self._achievement_row(datetime(2026, 8, 25, tzinfo=UTC), "new")],
            limit=1,
        )

        assert [item.id for item in items] == ["achievement:new"]


class TestDashboardAchievementsReadBothTables:
    def test_the_dashboard_no_longer_reads_only_the_frozen_table(self):
        """It read `progress_repo.list_achievements` alone, which meant four frozen rows for one learner
        and an empty list for everyone else — on screen, indistinguishable from having achieved nothing.
        """
        import inspect

        from src.domains.personal_learning.services import reflect_dashboard_service

        source = inspect.getsource(reflect_dashboard_service._load_achievements)
        assert "list_growth_milestones" in source
        assert "progress_repo.list_achievements" not in source

    def test_every_catalogue_entry_has_the_copy_the_panel_renders(self):
        """A milestone with no description would render a titled card over blank space."""
        from src.domains.personal_learning.services.milestone_service import MILESTONES

        for entry in MILESTONES:
            assert entry.get("title"), entry["id"]
            assert entry.get("description"), f"{entry['id']} has no description"
            assert entry.get("icon"), entry["id"]


class TestActivityBreakdownUsesOneConnection:
    """The density-strip payload is three queries on one session, not three concurrent reads.

    The route used to `asyncio.gather` them. That opened a connection per leg, and the configured
    database is reached through Supabase's session-mode pooler, whose tenant allowance is far smaller
    than this application's own pool ceiling — so adding a third leg was enough to make
    `GET /learning/activity-feed/daily-counts` return intermittent `500`s. It reproduced only when the
    route was exercised alongside others, which is why a service-level test never saw it.

    All three legs read `ActivityFeedEntry` through the same `_feed_conditions`. They were never
    independent work, so there was nothing to parallelise.
    """

    def test_the_route_does_not_fan_out(self):
        import inspect

        from src.domains.personal_learning import routes

        source = inspect.getsource(routes.get_activity_daily_counts)
        # Comments stripped as well as the docstring: the comment explaining *why* this route no
        # longer gathers names `asyncio.gather`, and matching that would fail the fix it documents.
        # The same trap caught the no-category guard, whose docstring listed the categories it forbids.
        body = "\n".join(
            line
            for line in source.split('"""')[-1].splitlines()
            if not line.strip().startswith("#")
        )

        assert "gather" not in body
        assert "list_activity_breakdown" in body

    def test_one_session_is_passed_to_all_three_reads(self):
        """Sequentially, on one session.

        Three coroutines sharing one `AsyncSession` would be concurrent use of a single connection,
        which SQLAlchemy rejects outright — so passing a session and gathering would trade an
        intermittent pool error for a certain one.
        """
        import inspect

        from src.domains.personal_learning.services import activity_feed_service

        source = inspect.getsource(activity_feed_service.list_activity_breakdown)
        body = source.split('"""')[-1]

        assert body.count("session=session") == 3
        assert "gather" not in body

    async def test_it_returns_the_three_parts_in_order(self):
        from unittest.mock import patch

        from src.domains.personal_learning.services import activity_feed_service

        async def _by_day(_user_id, **_kw):
            return [(date(2026, 8, 1), 3)]

        async def _by_type(_user_id, **_kw):
            return [("note_created", 3)]

        async def _types(_user_id, **_kw):
            return ["note_created"]

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        with (
            patch.object(activity_feed_service, "get_session_factory", lambda: _Session, create=True),
            patch(
                "src.shared.database.get_session_factory", lambda: _Session
            ),
            patch.object(activity_feed_service.repo, "count_feed_entries_by_day", _by_day),
            patch.object(activity_feed_service.repo, "count_feed_entries_by_type", _by_type),
            patch.object(activity_feed_service.repo, "list_feed_activity_types", _types),
        ):
            days, by_type, available = await activity_feed_service.list_activity_breakdown(
                user_id="u"
            )

        assert days == [(date(2026, 8, 1), 3)]
        assert by_type == [("note_created", 3)]
        assert available == ["note_created"]
        # The figure the route publishes is summed from `days`, so the two cannot disagree.
        assert sum(count for _, count in days) == sum(count for _, count in by_type)


class TestPrepEvidence:
    """A preparation-linked goal has its own evidence reader.

    `ExamPrep` has no join to `Course` anywhere: `QuizSession.prepId` points at `ExamPrep` and its
    `topicId` at `PrepTopic`, a different table from the knowledge `Topic` the course reader walks. So a
    prep-linked goal was returning an empty panel while the learner had done real work.
    """

    async def _prep_evidence(self, *, quizzes=(), answers=(), limit=12):
        """Run `list_prep_evidence`'s merge over supplied rows for each of its two queries."""
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

        batches = [list(quizzes), list(answers)]
        calls = {"n": 0}

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            async def execute(self, *_a, **_k):
                rows = batches[calls["n"]] if calls["n"] < len(batches) else []
                calls["n"] += 1
                return _Result(rows)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        with patch.object(reflect_aggregates, "get_session_factory", lambda: _Session):
            return await reflect_aggregates.list_prep_evidence(
                user_id="u", prep_id="p", limit=limit
            )

    async def test_both_sources_are_merged_newest_first(self):
        items = await self._prep_evidence(
            quizzes=[("q1", "Integrals", datetime(2026, 8, 10, 9, 0, tzinfo=UTC), 80.0)],
            answers=[("o1", "Limits", datetime(2026, 8, 12, 9, 0, tzinfo=UTC), True)],
        )

        assert [item.kind for item in items] == ["practice_answer", "quiz_session"]

    async def test_a_completed_quiz_carries_its_score_as_a_number(self):
        items = await self._prep_evidence(
            quizzes=[("q1", "Integrals", datetime(2026, 8, 10, tzinfo=UTC), 62.5)]
        )

        assert items[0].value == 62.5
        assert items[0].unit == "%"
        assert items[0].correct is None, "a quiz score is not correct or incorrect"
        assert items[0].detail == "Integrals", "the topic the quiz was scoped to"

    async def test_a_quiz_with_no_recorded_score_is_unmeasured_not_zero(self):
        """`scorePercentage` is nullable even on a completed session. Publishing `0.0` would read as
        every answer wrong, which is a different statement from nothing recorded (Decision I)."""
        items = await self._prep_evidence(
            quizzes=[("q1", None, datetime(2026, 8, 10, tzinfo=UTC), None)]
        )

        assert items[0].value is None
        assert items[0].unit is None
        assert items[0].detail is None, "a whole-prep quiz has no topic to name"

    async def test_a_practice_answer_carries_its_verdict_and_no_figure(self):
        items = await self._prep_evidence(
            answers=[("o1", "Limits", datetime(2026, 8, 12, tzinfo=UTC), False)]
        )

        assert items[0].correct is False
        assert items[0].value is None
        assert items[0].title == "Limits"

    async def test_an_answer_with_no_topic_still_reads_as_a_question(self):
        items = await self._prep_evidence(
            answers=[("o1", None, datetime(2026, 8, 12, tzinfo=UTC), True)]
        )

        assert items[0].title == "Practice question"

    async def test_ids_are_namespaced_so_the_two_tables_cannot_collide(self):
        items = await self._prep_evidence(
            quizzes=[("x", None, datetime(2026, 8, 10, tzinfo=UTC), 50.0)],
            answers=[("x", None, datetime(2026, 8, 11, tzinfo=UTC), True)],
        )

        assert {item.id for item in items} == {"quiz:x", "practice:x"}

    async def test_the_limit_applies_after_the_merge(self):
        items = await self._prep_evidence(
            quizzes=[
                ("q1", None, datetime(2026, 8, 1, tzinfo=UTC), 10.0),
                ("q2", None, datetime(2026, 8, 2, tzinfo=UTC), 20.0),
            ],
            answers=[("o1", None, datetime(2026, 8, 20, tzinfo=UTC), True)],
            limit=2,
        )

        assert len(items) == 2
        assert items[0].kind == "practice_answer", "the newest item survives the trim"

    async def test_nothing_recorded_returns_an_empty_list(self):
        assert await self._prep_evidence() == []


class TestPrepEvidenceScope:
    """The two things this reader deliberately does not do."""

    def test_answers_from_a_completed_session_are_excluded(self):
        """A completed session is already published with a score summarising exactly those answers.

        Publishing both would list one five-question quiz as six rows and push everything older off the
        panel. The rule is stated against the session's own state rather than "whatever we already
        emitted", so it cannot change meaning with `limit`.
        """
        import inspect

        from src.domains.personal_learning.services import reflect_aggregates

        source = inspect.getsource(reflect_aggregates.list_prep_evidence)
        body = source.split('"""')[-1]
        lines = [line for line in body.splitlines() if not line.strip().startswith("#")]
        stripped = "\n".join(lines)

        assert "completed_at.is_not(None)" in stripped, "completed sessions are the quiz source"
        assert "completed_at.is_(None)" in stripped, (
            "answers are read only for sessions that never completed"
        )

    def test_no_date_is_borrowed_from_updated_at(self):
        """`PrepTopic` has no `completedAt`, so a "topic mastered" item could only be dated by
        `updatedAt` — the same substitution the course reader refuses, because it dates the learner's
        work to whenever a row was last touched."""
        import inspect

        from src.domains.personal_learning.services import reflect_aggregates

        source = inspect.getsource(reflect_aggregates.list_prep_evidence)
        body = source.split('"""')[-1]
        lines = [line for line in body.splitlines() if not line.strip().startswith("#")]

        assert "updated_at" not in "\n".join(lines)

    def test_the_exam_date_is_never_read(self):
        """`ExamPrep.examDate` is `timestamp without time zone` while the ORM declares
        `DateTime(timezone=True)` — the exact mismatch that made `GET /progress/goals` return a 500 for
        every goal with a target date."""
        import inspect

        from src.domains.personal_learning.services import reflect_aggregates

        source = inspect.getsource(reflect_aggregates.list_prep_evidence)
        body = source.split('"""')[-1]
        lines = [line for line in body.splitlines() if not line.strip().startswith("#")]

        assert "exam_date" not in "\n".join(lines)


class TestPrepLinkedGoalReachesTheReader:
    async def _goal_evidence(self, goal, *, course=None, prep=None):
        from unittest.mock import patch

        from src.domains.personal_learning.services import reflect_aggregates

        async def _fake_course(*, user_id, course_id, limit=12):
            return course or []

        async def _fake_prep(*, user_id, prep_id, limit=12):
            return prep or []

        with (
            patch.object(reflect_aggregates, "list_course_evidence", _fake_course),
            patch.object(reflect_aggregates, "list_prep_evidence", _fake_prep),
        ):
            return await reflect_aggregates.list_goal_evidence(user_id="u", goal=goal, limit=5)

    async def test_a_prep_linked_goal_reads_the_prep_tables(self):
        from types import SimpleNamespace

        item = SimpleNamespace(id="quiz:1")
        items = await self._goal_evidence(
            SimpleNamespace(course_id=None, topic_id=None, prep_id="p1"),
            prep=[item],
        )

        assert items == [item]

    async def test_a_course_link_wins_when_a_goal_carries_both(self):
        """A goal with both is a goal about a course that happens to have a preparation attached, and the
        course reader covers the wider ground."""
        from types import SimpleNamespace

        course_item = SimpleNamespace(id="session:1")
        items = await self._goal_evidence(
            SimpleNamespace(course_id="c1", topic_id=None, prep_id="p1"),
            course=[course_item],
            prep=[SimpleNamespace(id="quiz:1")],
        )

        assert items == [course_item]
