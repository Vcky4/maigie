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
