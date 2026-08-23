"""Phase 6.5's written interpretation: growth drivers, subject insight, goal insight.

Three panels, one cache, one tier gate. The cases here are the claims that would otherwise go
unchecked, and four of them cover defects this task found in live data rather than hypotheticals:

**A series flat at zero is not a driver.** A learner with no tracked study has an effort series of
zeros, and the first live run duly published a card reading "steady · 0 effort · 0 focused minutes".

**Prose is invalidated by the prompt as well as by the figures.** The cache is keyed on the measured
skeleton, so without a revision in the fingerprint a prompt fix would leave every learner reading the
wording it replaced.

**A brief must not contain a timestamp.** `GoalResponse.targetDate` is a full `isoformat()`, and a live
goal insight read "your target date of 2026-03-31T23:59:59".

**`Goal.targetDate` comes back naive.** The column is `timestamp without time zone` while the ORM
declares `timezone=True`, so every pace predicate compared naive against aware and `GET /progress/goals`
answered `500` for any goal with a deadline.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning import models  # noqa: E402
from src.domains.personal_learning.services import (  # noqa: E402
    growth_narrative,
    narrative_cache,
)
from src.domains.progress import models as progress_models  # noqa: E402
from src.domains.progress.services import goal_insight_service, goal_metrics  # noqa: E402


def _point(**kw) -> models.GrowthTrendPoint:
    base = {
        "day": "2026-08-01",
        "focused_minutes": None,
        "effort_score": None,
        "consistency_score": None,
        "mastery_percent": None,
        "cards_reviewed": 0,
        "recall_percent": None,
        "topics_completed": 0,
        "active_day": False,
        "reconstructed": False,
    }
    base.update(kw)
    return models.GrowthTrendPoint(**base)


def _trends(**kw) -> models.GrowthTrendsResponse:
    base = {"range": "30d", "days": 30}
    base.update(kw)
    return models.GrowthTrendsResponse(**base)


def _delta(first, last) -> models.GrowthDelta:
    if first is None:
        return models.GrowthDelta()
    return models.GrowthDelta(first=first, last=last, change=round(last - first, 1))


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_key_order_does_not_change_the_hash(self):
        """Two spellings of the same skeleton are one cache entry, not two."""
        assert narrative_cache.fingerprint({"a": 1, "b": 2}) == narrative_cache.fingerprint(
            {"b": 2, "a": 1}
        )

    def test_a_moved_figure_is_a_miss(self):
        assert narrative_cache.fingerprint({"mastery": 74}) != narrative_cache.fingerprint(
            {"mastery": 75}
        )

    def test_a_date_in_the_skeleton_does_not_raise(self):
        """A serialisation error here would surface as a silently missing insight panel."""
        assert narrative_cache.fingerprint({"day": datetime(2026, 8, 1, tzinfo=UTC)})

    def test_the_revision_is_part_of_the_hash(self, monkeypatch):
        """A prompt change must retire the prose written under the old one.

        The skeleton cannot express an instruction change, so without this a corrected prompt would
        leave every learner reading the wording it replaced until their own numbers moved.
        """
        before = narrative_cache.fingerprint({"mastery": 74})
        monkeypatch.setattr(
            narrative_cache, "NARRATIVE_REVISION", narrative_cache.NARRATIVE_REVISION + 1
        )
        assert narrative_cache.fingerprint({"mastery": 74}) != before


class TestResolve:
    @pytest.mark.asyncio
    async def test_a_matching_hash_returns_the_stored_prose_without_composing(self, monkeypatch):
        inputs = {"mastery": 74}
        row = SimpleNamespace(
            inputs_hash=narrative_cache.fingerprint(inputs), payload={"title": "stored"}
        )
        monkeypatch.setattr(narrative_cache, "_load", _returns(row))
        calls = []

        async def compose():
            calls.append(1)
            return {"title": "fresh"}

        result = await narrative_cache.resolve(
            user_id="u", kind="goal_insight", inputs=inputs, compose=compose
        )
        assert result == {"title": "stored"}
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_stale_hash_recomposes_and_stores(self, monkeypatch):
        row = SimpleNamespace(inputs_hash="stale", payload={"title": "old"})
        monkeypatch.setattr(narrative_cache, "_load", _returns(row))
        stored = {}

        async def _store(**kw):
            stored.update(kw)

        monkeypatch.setattr(narrative_cache, "_store", _store)

        async def compose():
            return {"title": "fresh"}

        result = await narrative_cache.resolve(
            user_id="u", kind="goal_insight", inputs={"mastery": 75}, compose=compose
        )
        assert result == {"title": "fresh"}
        assert stored["payload"] == {"title": "fresh"}

    @pytest.mark.asyncio
    async def test_a_failed_composition_is_never_stored(self, monkeypatch):
        """One language model timeout must not freeze a blank panel in place.

        Storing `{}` would make the panel permanently empty, unfixable until the learner's figures
        happened to move — a silent, permanent failure in place of a retried one.
        """
        monkeypatch.setattr(narrative_cache, "_load", _returns(None))
        writes = []
        monkeypatch.setattr(narrative_cache, "_store", _record(writes))

        async def compose():
            return None

        result = await narrative_cache.resolve(
            user_id="u", kind="goal_insight", inputs={"x": 1}, compose=compose
        )
        assert result == {}
        assert writes == []

    @pytest.mark.asyncio
    async def test_a_non_dict_payload_is_treated_as_a_miss(self, monkeypatch):
        inputs = {"x": 1}
        row = SimpleNamespace(inputs_hash=narrative_cache.fingerprint(inputs), payload=["not", "a", "dict"])
        monkeypatch.setattr(narrative_cache, "_load", _returns(row))
        monkeypatch.setattr(narrative_cache, "_store", _record([]))

        async def compose():
            return {"title": "fresh"}

        assert await narrative_cache.resolve(
            user_id="u", kind="goal_insight", inputs=inputs, compose=compose
        ) == {"title": "fresh"}


class TestPlusGate:
    @pytest.mark.asyncio
    async def test_plus_reads_the_prose(self, monkeypatch):
        _tier(monkeypatch, "plus")
        assert await narrative_cache.plus_gate("u", reason="r") is None

    @pytest.mark.asyncio
    async def test_free_gets_a_notice_not_an_error(self, monkeypatch):
        """Decision Z: a locked panel is a 200 carrying this, never a 403."""
        _tier(monkeypatch, "free")
        notice = await narrative_cache.plus_gate("u", reason="because")
        assert notice.locked is True
        assert notice.reason == "because"
        assert notice.upgrade_url == "/subscription"
        assert notice.upgrade_value

    @pytest.mark.asyncio
    async def test_the_capability_is_the_existing_reflection_entry(self, monkeypatch):
        """Not a new matrix key.

        `reflection`'s upgrade copy already promises deeper insight with specific next steps, which is
        exactly what these three panels are. A second entry would mean two upsell messages for one
        promise, free to drift apart — and `trial_service` already records this one as used.
        """
        from src.domains.personal_learning.services import feature_tier_service

        _tier(monkeypatch, "free")
        notice = await narrative_cache.plus_gate("u", reason="r")
        assert notice.capability == "reflection"
        assert notice.capability in feature_tier_service.FEATURE_TIER_MATRIX


# ---------------------------------------------------------------------------
# Growth drivers
# ---------------------------------------------------------------------------


class TestDriverSkeleton:
    def test_an_unmeasured_series_is_not_a_driver(self):
        """`change is None` means fewer than two days were captured. One point is not a movement."""
        skeleton = growth_narrative.build_drivers(_trends(points=[_point(effort_score=40.0)]))
        assert skeleton == []

    def test_a_series_flat_at_zero_is_not_a_driver(self):
        """The live defect: "steady · 0 effort · 0 focused minutes" is a card about nothing."""
        trends = _trends(
            points=[_point(effort_score=0.0), _point(effort_score=0.0)],
            effort=_delta(0.0, 0.0),
        )
        assert [c["id"] for c in growth_narrative.build_drivers(trends)] == []

    def test_a_series_flat_at_a_real_value_is_kept(self):
        """Holding consistency at 100 for a month is a finding, unlike holding it at nothing."""
        trends = _trends(
            points=[_point(consistency_score=100.0), _point(consistency_score=100.0)],
            consistency=_delta(100.0, 100.0),
            active_days=1,
        )
        skeleton = growth_narrative.build_drivers(trends)
        assert [c["id"] for c in skeleton] == ["consistency"]
        assert skeleton[0]["impact"] == "steady"

    def test_ordering_is_by_size_of_movement_so_a_decline_leads(self):
        trends = _trends(
            points=[
                _point(consistency_score=90.0, effort_score=50.0, mastery_percent=70.0),
                _point(consistency_score=88.0, effort_score=51.0, mastery_percent=40.0),
            ],
            consistency=_delta(90.0, 88.0),
            effort=_delta(50.0, 51.0),
            mastery=_delta(70.0, 40.0),
            active_days=2,
        )
        assert [c["id"] for c in growth_narrative.build_drivers(trends)] == [
            "mastery",
            "consistency",
            "effort",
        ]

    def test_retrieval_needs_cards_as_well_as_two_recall_readings(self):
        """Recall with no cards behind it is a percentage of nothing."""
        trends = _trends(
            points=[
                _point(recall_percent=80.0, cards_reviewed=0),
                _point(recall_percent=90.0, cards_reviewed=0),
            ]
        )
        assert [c["id"] for c in growth_narrative.build_drivers(trends)] == []

        with_cards = _trends(
            points=[
                _point(recall_percent=80.0, cards_reviewed=5),
                _point(recall_percent=90.0, cards_reviewed=7),
            ]
        )
        skeleton = growth_narrative.build_drivers(with_cards)
        assert [c["id"] for c in skeleton] == ["retrieval"]
        assert "12 cards reviewed" in skeleton[0]["evidence"]


class TestDriverImpact:
    @pytest.mark.parametrize(
        "change,expected",
        [
            (12.0, "high"),
            (10.0, "high"),
            (9.9, "growing"),
            (0.1, "growing"),
            (0.0, "steady"),
            (-0.1, "slipping"),
            (-30.0, "slipping"),
        ],
    )
    def test_bands(self, change, expected):
        assert growth_narrative._impact(change) == expected

    def test_any_loss_is_slipping_regardless_of_size(self):
        """A decline is not graded. The panel's job is to surface it, not to reassure about it."""
        assert growth_narrative._impact(-0.5) == growth_narrative._impact(-40.0)


class TestDriverEvidence:
    def test_one_is_singular(self):
        """This string is published, so "1 active days" is not acceptable."""
        assert growth_narrative._count(1, "active day", "active days") == "1 active day"
        assert growth_narrative._count(2, "active day", "active days") == "2 active days"

    def test_a_participle_noun_is_not_pluralised_by_a_suffix_rule(self):
        """The live bug: appending `s` to "card reviewed" produced "12 card revieweds"."""
        assert (
            growth_narrative._count(12, "card reviewed", "cards reviewed") == "12 cards reviewed"
        )
        assert growth_narrative._count(1, "card reviewed", "cards reviewed") == "1 card reviewed"

    def test_a_score_carries_its_denominator(self):
        assert growth_narrative._score("consistency", 86.0) == "consistency 86/100"

    def test_a_ratio_is_rounded_before_it_is_published(self):
        """A consistency score is a division and arrives as 57.14285714285714."""
        assert growth_narrative._score("consistency", 400 / 7) == "consistency 57.1/100"

    def test_a_part_with_no_value_is_dropped_rather_than_rendered_empty(self):
        assert growth_narrative._evidence(None, "2 topics completed") == "2 topics completed"


class TestDriversPrompt:
    def test_the_figures_are_supplied_and_the_model_is_told_not_to_restate_them(self):
        """Decision A. The service measured every number; the model may only word it."""
        trends = _trends(
            points=[_point(mastery_percent=60.0), _point(mastery_percent=74.0, topics_completed=3)],
            mastery=_delta(60.0, 74.0),
        )
        skeleton = growth_narrative.build_drivers(trends)
        prompt = growth_narrative.build_drivers_prompt(range_="30d", skeleton=skeleton)
        assert "mastery percent 74" in prompt
        assert "must not compute a new figure" in prompt
        # The live defect: a detail reading "resulting in a movement of +0 points across the range".
        assert "do not restate the point movement" in prompt


class TestAssembleDrivers:
    def test_a_driver_with_no_heading_is_dropped(self):
        """A driver card is nothing but its interpretation; the figures are already on the chart."""
        skeleton = [{"id": "mastery", "change": 4.0, "impact": "growing", "evidence": "e"}]
        assert growth_narrative.assemble_drivers(skeleton=skeleton, written={}) == []

    def test_an_unfinished_sentence_is_dropped_but_the_card_survives(self):
        """A fragment under a chart reads as a finding. The heading is a phrase and survives."""
        skeleton = [{"id": "mastery", "change": 4.0, "impact": "growing", "evidence": "e"}]
        drivers = growth_narrative.assemble_drivers(
            skeleton=skeleton,
            written={"mastery": {"title": "You returned more often", "detail": "you will start turning"}},
        )
        assert len(drivers) == 1
        assert drivers[0].detail is None
        assert drivers[0].title == "You returned more often"

    def test_the_measured_half_never_comes_from_the_model(self):
        skeleton = [{"id": "mastery", "change": 4.0, "impact": "growing", "evidence": "74% mastery"}]
        drivers = growth_narrative.assemble_drivers(
            skeleton=skeleton,
            written={
                "mastery": {
                    "title": "Steadier revision",
                    "detail": "You revisited topics sooner.",
                    "evidence": "999 things",
                    "impact": "high",
                    "change": 99.0,
                }
            },
        )
        assert drivers[0].evidence == "74% mastery"
        assert drivers[0].impact == "growing"
        assert drivers[0].change == 4.0


# ---------------------------------------------------------------------------
# Subject insight
# ---------------------------------------------------------------------------


def _concept(status, title="Topic") -> models.SubjectConcept:
    return models.SubjectConcept(topic_id=f"t-{status}-{title}", title=title, status=status)


def _detail(*, concepts=(), sessions=3) -> models.GrowthSubjectDetailResponse:
    return models.GrowthSubjectDetailResponse(
        subject=models.GrowthSubject(
            course_id="c1",
            title="Algorithms",
            mastery_percent=61.0,
            topics_total=len(concepts),
            topics_completed=sum(1 for c in concepts if c.status == "strong"),
            change=4.0,
            activity=models.SubjectActivitySummary(sessions=sessions, focused_minutes=42.0),
        ),
        range="30d",
        days=30,
        concepts=list(concepts),
    )


class TestChooseNextStep:
    def test_no_sessions_points_at_the_schedule_not_at_a_topic(self):
        """Recommending topic work to someone who has not opened the subject skips the real gap."""
        _, label, target = growth_narrative.choose_next_step(
            _detail(concepts=[_concept("needs_attention")], sessions=0)
        )
        assert target.kind is models.ReflectionActionKind.SCHEDULE
        assert label == "Plan a session"

    def test_a_weak_topic_is_named_and_points_at_its_course(self):
        reason, label, target = growth_narrative.choose_next_step(
            _detail(concepts=[_concept("strong"), _concept("needs_attention", "Graphs")])
        )
        assert "Graphs" in reason
        assert target.kind is models.ReflectionActionKind.COURSE
        assert target.entity_id == "c1"

    def test_unstarted_topics_come_after_weak_ones(self):
        reason, _, _ = growth_narrative.choose_next_step(
            _detail(concepts=[_concept("not_started"), _concept("needs_attention", "Graphs")])
        )
        assert "Graphs" in reason

    def test_everything_secure_recommends_review(self):
        _, label, target = growth_narrative.choose_next_step(
            _detail(concepts=[_concept("strong"), _concept("strong", "B")])
        )
        assert target.kind is models.ReflectionActionKind.FLASHCARD_REVIEW
        assert label == "Review to keep it"

    def test_the_label_travels_with_the_destination(self):
        """A model-written caption over a service-chosen route is a button that lies."""
        for detail in (
            _detail(concepts=[_concept("needs_attention")], sessions=0),
            _detail(concepts=[_concept("needs_attention")]),
            _detail(concepts=[_concept("strong")]),
        ):
            _, label, target = growth_narrative.choose_next_step(detail)
            assert label, target.kind


class TestSubjectSkeleton:
    def test_bands_are_counted_rather_than_every_topic_listed(self):
        """A fifty-topic course must not put fifty titles into a prompt for two paragraphs."""
        skeleton = growth_narrative.build_subject_skeleton(
            _detail(
                concepts=[
                    _concept("strong", "A"),
                    _concept("strong", "B"),
                    _concept("needs_attention", "C"),
                    _concept("not_started", "D"),
                ]
            )
        )
        assert skeleton["bands"] == {
            "strong": 2,
            "growing": 0,
            "needs_attention": 1,
            "not_started": 1,
        }
        assert skeleton["weakestTopic"] == "C"

    def test_only_the_weakest_topic_is_named(self):
        skeleton = growth_narrative.build_subject_skeleton(
            _detail(concepts=[_concept("needs_attention", "First"), _concept("needs_attention", "Second")])
        )
        assert skeleton["weakestTopic"] == "First"
        assert "Second" not in str(skeleton)


class TestAssembleSubject:
    def test_both_headings_or_no_insight(self):
        """The page renders a matched pair; one filled and one empty reads as a failure."""
        insight, _ = growth_narrative.assemble_subject(
            written={"strength": "Retrieval is automatic", "focus": ""},
            label="Open the course",
            target=models.ReflectionActionTarget(),
            reason="grounds",
        )
        assert insight is None

    def test_a_step_falls_back_to_the_services_own_grounds(self):
        """The target is the part that has to be right; the sentence can be plain."""
        _, step = growth_narrative.assemble_subject(
            written={"step": {"title": "Close the gap", "detail": "unfinished fragment"}},
            label="Open the course",
            target=models.ReflectionActionTarget(kind=models.ReflectionActionKind.COURSE),
            reason="3 of 5 topics have not been started",
        )
        assert step.detail == "3 of 5 topics have not been started"
        assert step.label == "Open the course"

    def test_the_model_cannot_choose_the_destination(self):
        """Decision O. A model free to name an entity would cite one the learner does not own."""
        _, step = growth_narrative.assemble_subject(
            written={
                "step": {"title": "Do this", "detail": "Go now."},
                "target": {"kind": "course", "entityId": "someone-elses-course"},
                "label": "Steal",
            },
            label="Plan a session",
            target=models.ReflectionActionTarget(kind=models.ReflectionActionKind.SCHEDULE),
            reason="grounds",
        )
        assert step.target.kind is models.ReflectionActionKind.SCHEDULE
        assert step.target.entity_id is None
        assert step.label == "Plan a session"


# ---------------------------------------------------------------------------
# Goal insight
# ---------------------------------------------------------------------------


def _goal_response(**kw) -> progress_models.GoalResponse:
    base = {
        "id": "g1",
        "userId": "u1",
        "title": "Master statistical inference",
        "status": "ACTIVE",
        "progress": 40.0,
        "statusLabel": "ON_TRACK",
        "createdAt": "2026-08-01T00:00:00+00:00",
        "updatedAt": "2026-08-01T00:00:00+00:00",
    }
    base.update(kw)
    return progress_models.GoalResponse(**base)


class TestDeriveSignal:
    @pytest.mark.parametrize(
        "status_label,pace,expected",
        [
            ("COMPLETED", None, "achieved"),
            ("COMPLETED", 40.0, "achieved"),
            ("ON_TRACK", None, "not_paced"),
            ("ON_TRACK", 130.0, "ahead"),
            ("ON_TRACK", 110.0, "ahead"),
            ("ON_TRACK", 100.0, "on_track"),
            ("ON_TRACK", 90.0, "on_track"),
            ("NEEDS_ATTENTION", 40.0, "behind"),
        ],
    )
    def test_bands(self, status_label, pace, expected):
        assert (
            goal_insight_service.derive_signal(status_label=status_label, pace_percent=pace)
            == expected
        )

    def test_a_goal_with_no_deadline_is_not_called_on_track(self):
        """It has no schedule to be ahead or behind of. `on_track` would be an unmeasured claim."""
        assert (
            goal_insight_service.derive_signal(status_label="ON_TRACK", pace_percent=None)
            == "not_paced"
        )


class TestChooseNextAction:
    def _call(self, **kw):
        base = {
            "goal": SimpleNamespace(id="g1", course_id=None, prep_id=None),
            "status_label": "ON_TRACK",
            "planned": 4,
            "completed": 4,
            "completion_tracked": True,
        }
        base.update(kw)
        return goal_insight_service.choose_next_action(**base)

    def test_a_finished_goal_points_at_the_next_one(self):
        _, label, target = self._call(status_label="COMPLETED")
        assert target.kind == "goal"
        assert target.entityId is None
        assert label == "Set your next goal"

    def test_nothing_planned_points_at_planning(self):
        _, label, target = self._call(planned=0)
        assert target.kind == "schedule"
        assert label == "Plan your sessions"

    def test_untracked_completion_does_not_report_the_learner_as_behind(self):
        """`ScheduleBlock.completedAt` reads zero for every learner until they mark a block.

        Without the guard, `completed < planned` would tell everyone in the database that they are
        behind on a plan nobody has ever recorded against (Decision Y).
        """
        reason, _, target = self._call(planned=4, completed=0, completion_tracked=False)
        assert "marked done" not in reason
        assert target.kind != "schedule" or "no sessions are scheduled" in reason

    def test_tracked_shortfall_points_at_the_schedule(self):
        reason, label, target = self._call(planned=4, completed=1, completion_tracked=True)
        assert target.kind == "schedule"
        assert "1 of 4" in reason
        assert label == "Open your schedule"

    def test_a_prep_linked_goal_practises_it(self):
        _, label, target = self._call(
            goal=SimpleNamespace(id="g1", course_id="c1", prep_id="p1")
        )
        assert (target.kind, target.entityId, target.mode) == ("preparation_practice", "p1", "weak")
        assert label == "Start focused practice"

    def test_a_course_linked_goal_opens_the_course(self):
        _, label, target = self._call(goal=SimpleNamespace(id="g1", course_id="c1", prep_id=None))
        assert (target.kind, target.entityId) == ("course", "c1")
        assert label == "Open the course"

    def test_a_manual_goal_gets_advice_with_no_button(self):
        """A button to the page the learner is already on would be worse than none."""
        _, label, target = self._call()
        assert target.kind == "none"
        assert label == ""


class TestGoalPrompt:
    def _prompt(self, response, **kw):
        base = {
            "planned": 4,
            "completed": 2,
            "completion_tracked": True,
            "recorded_days": 10,
            "evidence_count": 3,
        }
        base.update(kw)
        skeleton = goal_insight_service.build_skeleton(goal_response=response, **base)
        return skeleton, goal_insight_service.build_prompt(
            skeleton=skeleton, signal="behind", reason="grounds"
        )

    def test_the_target_date_is_a_date_not_a_timestamp(self):
        """The live defect: prose reading "your target date of 2026-03-31T23:59:59"."""
        _, prompt = self._prompt(_goal_response(targetDate="2026-03-31T23:59:59+00:00"))
        assert "target date: 2026-03-31" in prompt
        assert "23:59:59" not in prompt

    def test_an_absent_deadline_is_stated_rather_than_omitted(self):
        """A brief that simply lacks a pace reads as one that forgot it, and gets filled in."""
        _, prompt = self._prompt(_goal_response(targetDate=None))
        assert "no schedule to be ahead or behind of" in prompt

    def test_untracked_completion_is_stated_as_unrecorded(self):
        _, prompt = self._prompt(_goal_response(), completion_tracked=False, completed=0)
        assert "never been recorded" in prompt
        assert "marked done" not in prompt

    def test_a_goal_with_no_history_forbids_a_trend_claim(self):
        _, prompt = self._prompt(_goal_response(), recorded_days=1)
        assert "no trajectory yet" in prompt

    def test_the_model_is_forbidden_from_inventing_a_figure(self):
        _, prompt = self._prompt(_goal_response(progress=40.0))
        assert "must not compute a new figure" in prompt
        assert "40%" in prompt

    def test_an_asserted_value_is_marked_as_asserted(self):
        """A `manual` goal's value was typed by the learner, not measured."""
        _, prompt = self._prompt(
            _goal_response(targetValue=6.0, currentValue=4.0, currentValueMeasured=False)
        )
        assert "entered by the learner, not measured" in prompt


class TestAssembleGoal:
    def _assemble(self, written):
        return goal_insight_service.assemble(
            written=written,
            signal="behind",
            label="Open your schedule",
            target=progress_models.GoalNextActionTarget(kind="schedule"),
            reason="1 of 4 scheduled sessions were marked done",
        )

    def test_no_heading_no_panel(self):
        insight, _ = self._assemble({"detail": "A finished sentence."})
        assert insight is None

    def test_an_unfinished_sentence_is_dropped_and_the_signal_survives(self):
        insight, _ = self._assemble({"title": "This goal is behind", "detail": "you will start turn"})
        assert insight.detail is None
        assert insight.signal == "behind"

    def test_the_signal_is_never_taken_from_the_model(self):
        insight, _ = self._assemble({"title": "Doing great", "signal": "ahead"})
        assert insight.signal == "behind"

    def test_the_action_falls_back_to_the_grounds(self):
        _, action = self._assemble(
            {"title": "t", "action": {"title": "Catch up", "detail": "unfinished"}}
        )
        assert action.detail == "1 of 4 scheduled sessions were marked done"
        assert action.target.kind == "schedule"


# ---------------------------------------------------------------------------
# The naive/aware regression
# ---------------------------------------------------------------------------


class TestNaiveTargetDate:
    """`Goal.targetDate` is `timestamp without time zone` while the ORM declares `timezone=True`.

    asyncpg honours the database, so both `targetDate` and `createdAt` arrive naive and every predicate
    compared them against an aware `datetime.now(UTC)`. `GET /progress/goals` answered `500` for any
    goal that had a deadline at all, which is most of them.
    """

    NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def test_is_overdue_accepts_a_naive_deadline(self):
        assert goal_metrics.is_overdue(
            status="ACTIVE",
            progress=10.0,
            target_date=datetime(2026, 7, 1, 0, 0),
            now=self.NOW,
        )

    def test_is_due_soon_accepts_a_naive_deadline(self):
        assert goal_metrics.is_due_soon(
            status="ACTIVE",
            progress=10.0,
            target_date=datetime(2026, 8, 15, 0, 0),
            now=self.NOW,
        )

    def test_is_at_risk_accepts_naive_dates(self):
        assert goal_metrics.is_at_risk(
            progress=0.0,
            created_at=datetime(2026, 1, 1, 0, 0),
            target_date=datetime(2026, 7, 1, 0, 0),
            now=self.NOW,
        )

    def test_elapsed_percent_accepts_naive_dates(self):
        assert goal_metrics.elapsed_percent(
            created_at=datetime(2026, 8, 3, 12, 0),
            target_date=datetime(2026, 8, 23, 12, 0),
            now=self.NOW,
        ) == pytest.approx(50.0)

    def test_the_predicates_agree_about_one_deadline(self):
        """A goal counted overdue by one and not-at-risk by another publishes two contradictory labels.

        This is why normalisation lives in one place rather than at each call site.
        """
        naive = datetime(2026, 7, 1, 0, 0)
        aware = naive.replace(tzinfo=UTC)
        for kwargs in (
            {"status": "ACTIVE", "progress": 10.0},
        ):
            assert goal_metrics.is_overdue(
                target_date=naive, now=self.NOW, **kwargs
            ) == goal_metrics.is_overdue(target_date=aware, now=self.NOW, **kwargs)

    def test_a_naive_now_is_also_tolerated(self):
        """Some callers derive `now` from a stored row rather than from `datetime.now(UTC)`."""
        assert goal_metrics.is_overdue(
            status="ACTIVE",
            progress=10.0,
            target_date=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
            now=datetime(2026, 8, 13, 12, 0),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _returns(value):
    async def _fn(**_kw):
        return value

    return _fn


def _record(sink):
    async def _fn(**kw):
        sink.append(kw)

    return _fn


def _tier(monkeypatch, tier: str) -> None:
    from src.domains.personal_learning.services import feature_tier_service

    async def _quality(_user_id):
        return tier

    async def _trial(_user_id):
        return False

    monkeypatch.setattr(feature_tier_service, "get_quality_tier", _quality)
    monkeypatch.setattr(feature_tier_service, "trial_available", _trial)
