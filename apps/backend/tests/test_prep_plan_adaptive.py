"""Tests for adaptive study-plan scheduling.

`generate_plan` computed `is_adaptive = quality_tier == "plus"` and no code path
branched on it. A Plus learner's plan was byte-for-byte a Free learner's — same even
walk through topics in `orderIndex` order, same flat 15-minute review on whichever
first third of the list — while the docstring promised scheduling that "adjusts
based on quiz performance and behaviour" and the commercial surface sold "Adaptive
study plans".

These tests are the difference being real. Three properties, each of which would
otherwise be an unverifiable claim:

- **Order follows need**, not `orderIndex`, and agrees with what an adaptive
  practice session would choose, because both use `prep_adaptive.rank_topics`.
- **Time follows the gap**, bounded, so a weak topic gets more minutes without one
  topic eating the plan.
- **Revisits follow the band**, spaced and per topic, rather than one flat review
  applied to a slice of the list.

Pure module, so no database and no LLM.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import prep_competence, prep_plan_adaptive

START = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _topic(topic_id: str, *, title: str, order: int = 0, minutes: int = 30):
    return SimpleNamespace(
        id=topic_id,
        prep_id="prep-1",
        title=title,
        description=None,
        category=None,
        order_index=order,
        estimated_minutes=minutes,
        mastery_score=0.0,
        target_mastery=None,
        status="IN_PROGRESS",
    )


def _competence(topic_id: str, retention: float | None, *, observations: int = 6):
    """A measurable estimate. `observations` below 3 makes it unmeasurable, which is
    the state the scheduler has to treat as "no evidence" rather than "weak"."""
    return prep_competence.TopicCompetence(
        topic_id=topic_id,
        observations=observations,
        effective_weight=float(observations),
        retention=retention,
        fluency=None,
        independence=None,
        reliability=None,
    )


def _unmeasured(topic_id: str):
    return prep_competence.TopicCompetence(
        topic_id=topic_id,
        observations=1,
        effective_weight=1.0,
        retention=None,
        fluency=None,
        independence=None,
        reliability=None,
    )


class TestTimeMultiplier:
    def test_an_unmeasured_topic_gets_exactly_its_estimate(self):
        """We do not know it needs more, and inflating it would take minutes from a
        topic measured as weak — the one case there is evidence for."""
        assert prep_plan_adaptive.time_multiplier(_unmeasured("t")) == 1.0
        assert prep_plan_adaptive.time_multiplier(None) == 1.0

    def test_a_weak_topic_gets_more_time_than_a_strong_one(self):
        weak = prep_plan_adaptive.time_multiplier(_competence("t", 30.0))
        strong = prep_plan_adaptive.time_multiplier(_competence("t", 90.0))
        assert weak > strong

    def test_the_multiplier_stays_within_its_bounds(self):
        for retention in (0.0, 15.0, 45.0, 70.0, 85.0, 100.0):
            multiplier = prep_plan_adaptive.time_multiplier(_competence("t", retention))
            assert prep_plan_adaptive.MIN_TIME_MULTIPLIER <= multiplier
            assert multiplier <= prep_plan_adaptive.MAX_TIME_MULTIPLIER

    def test_a_topic_at_zero_does_not_consume_the_plan(self):
        """Bounded on purpose: unbounded weighting turns a plan for an exam into a
        plan for one topic."""
        assert prep_plan_adaptive.time_multiplier(_competence("t", 0.0)) <= 1.6

    def test_a_topic_at_the_target_needs_no_extra(self):
        multiplier = prep_plan_adaptive.time_multiplier(_competence("t", 80.0), target_mastery=80.0)
        assert multiplier == pytest.approx(prep_plan_adaptive.MIN_TIME_MULTIPLIER)

    def test_a_topic_past_the_target_is_not_penalised_further(self):
        """The gap is floored at zero, so exceeding the target does not keep
        shrinking the session towards nothing."""
        at = prep_plan_adaptive.time_multiplier(_competence("t", 80.0), target_mastery=80.0)
        past = prep_plan_adaptive.time_multiplier(_competence("t", 100.0), target_mastery=80.0)
        assert at == past

    def test_a_higher_target_asks_for_more_time_at_the_same_retention(self):
        modest = prep_plan_adaptive.time_multiplier(_competence("t", 60.0), target_mastery=70.0)
        ambitious = prep_plan_adaptive.time_multiplier(_competence("t", 60.0), target_mastery=95.0)
        assert ambitious > modest


class TestRevisitCount:
    def test_the_weakest_band_earns_the_most_revisits(self):
        assert prep_plan_adaptive.revisit_count(_competence("t", 40.0)) == 2
        assert prep_plan_adaptive.revisit_count(_competence("t", 75.0)) == 1
        assert prep_plan_adaptive.revisit_count(_competence("t", 95.0)) == 0

    def test_a_strong_topic_gets_none_rather_than_a_token_one(self):
        """Spacing is what makes a revisit worth scheduling. A revisit nobody needs
        is time taken from a topic that does."""
        assert prep_plan_adaptive.revisit_count(_competence("t", 92.0)) == 0

    def test_an_unmeasured_topic_gets_one(self):
        assert prep_plan_adaptive.revisit_count(_unmeasured("t")) == 1
        assert prep_plan_adaptive.revisit_count(None) == 1


class TestScheduleOrder:
    def test_need_beats_order_index(self):
        """The whole point. `orderIndex` is the order topics were extracted in, and
        has nothing to do with what the learner should do first."""
        topics = [
            _topic("t-strong", title="Strong", order=0),
            _topic("t-weak", title="Weak", order=1),
        ]
        competence = {
            "t-strong": _competence("t-strong", 95.0),
            "t-weak": _competence("t-weak", 35.0),
        }

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=60
        )

        study = [item for item in items if item.item_type == "STUDY"]
        assert study[0].prep_topic_id == "t-weak"

    def test_an_unmeasured_topic_outranks_a_measured_weak_one(self):
        """Consistent with `prep_adaptive`: a topic nobody has measured could be the
        worst, and finding out is the useful next step."""
        topics = [
            _topic("t-weak", title="Weak", order=0),
            _topic("t-unknown", title="Unknown", order=1),
        ]
        competence = {
            "t-weak": _competence("t-weak", 35.0),
            "t-unknown": _unmeasured("t-unknown"),
        }

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=60
        )

        study = [item for item in items if item.item_type == "STUDY"]
        assert study[0].prep_topic_id == "t-unknown"

    def test_the_order_agrees_with_an_adaptive_practice_session(self):
        """Both read `prep_adaptive.rank_topics`, so a plan and a session cannot
        disagree about what needs work."""
        from src.domains.personal_learning.services import prep_adaptive

        topics = [
            _topic("t-a", title="A", order=0),
            _topic("t-b", title="B", order=1),
            _topic("t-c", title="C", order=2),
        ]
        competence = {
            "t-a": _competence("t-a", 92.0),
            "t-b": _competence("t-b", 41.0),
            "t-c": _competence("t-c", 74.0),
        }

        ranked = [t.id for t in prep_adaptive.rank_topics(topics, competence)]
        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=40, start=START, max_daily_minutes=1000
        )
        scheduled = [item.prep_topic_id for item in items if item.item_type == "STUDY"]

        assert scheduled == ranked

    def test_items_come_back_ordered_by_date(self):
        topics = [_topic(f"t-{i}", title=f"T{i}", order=i) for i in range(4)]
        competence = {f"t-{i}": _competence(f"t-{i}", 30.0 + i * 20) for i in range(4)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=45
        )

        dates = [item.scheduled_date for item in items]
        assert dates == sorted(dates)


class TestScheduleContent:
    def test_a_weak_topic_is_given_more_minutes_than_a_strong_one(self):
        topics = [
            _topic("t-weak", title="Weak", minutes=30),
            _topic("t-strong", title="Strong", minutes=30),
        ]
        competence = {
            "t-weak": _competence("t-weak", 25.0),
            "t-strong": _competence("t-strong", 95.0),
        }

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=1000
        )
        by_topic = {i.prep_topic_id: i for i in items if i.item_type == "STUDY"}

        assert by_topic["t-weak"].estimated_minutes > by_topic["t-strong"].estimated_minutes

    def test_revisits_are_spaced_and_expand(self):
        topics = [_topic("t-weak", title="Weak")]
        competence = {"t-weak": _competence("t-weak", 30.0)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=1000
        )

        study = next(i for i in items if i.item_type == "STUDY")
        revisits = [i for i in items if i.item_type == "REVIEW"]
        offsets = [(r.scheduled_date - study.scheduled_date).days for r in revisits]
        assert offsets == list(prep_plan_adaptive.REVISIT_OFFSETS)

    def test_a_revisit_with_no_room_before_the_date_is_dropped(self):
        """Not clamped onto the last day. A revisit the day before the exam is not
        spaced practice, and it would collide with the exam itself."""
        topics = [_topic("t-weak", title="Weak")]
        competence = {"t-weak": _competence("t-weak", 30.0)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=5, start=START, max_daily_minutes=1000
        )

        revisits = [i for i in items if i.item_type == "REVIEW"]
        # Offsets are 3 and 9; only the first fits inside five days.
        assert len(revisits) == 1
        assert all((i.scheduled_date - START).days <= 4 for i in items)

    def test_a_strong_topic_produces_no_revisit(self):
        topics = [_topic("t-strong", title="Strong")]
        competence = {"t-strong": _competence("t-strong", 96.0)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=1000
        )

        assert [i.item_type for i in items] == ["STUDY"]

    def test_every_item_explains_itself(self):
        """A plan that reorders someone's topics owes them a reason for the order."""
        topics = [
            _topic("t-weak", title="Weak"),
            _topic("t-unknown", title="Unknown"),
            _topic("t-strong", title="Strong"),
        ]
        competence = {
            "t-weak": _competence("t-weak", 35.0),
            "t-unknown": _unmeasured("t-unknown"),
            "t-strong": _competence("t-strong", 95.0),
        }

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=1000
        )

        assert all(item.description for item in items)
        weak = next(i for i in items if i.prep_topic_id == "t-weak" and i.item_type == "STUDY")
        # The number the learner can check against their own topic list.
        assert "35%" in weak.description

    def test_the_daily_budget_is_respected(self):
        topics = [_topic(f"t-{i}", title=f"T{i}", minutes=40) for i in range(6)]
        competence = {f"t-{i}": _unmeasured(f"t-{i}") for i in range(6)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=30, start=START, max_daily_minutes=60
        )

        per_day: dict[object, int] = {}
        for item in items:
            if item.item_type != "STUDY":
                continue
            key = item.scheduled_date.date()
            per_day[key] = per_day.get(key, 0) + item.estimated_minutes
        # 40-minute sessions against a 60-minute budget means one a day.
        assert max(per_day.values()) <= 60

    def test_every_topic_appears_even_with_fewer_days_than_topics(self):
        topics = [_topic(f"t-{i}", title=f"T{i}", minutes=60) for i in range(8)]
        competence = {f"t-{i}": _unmeasured(f"t-{i}") for i in range(8)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=3, start=START, max_daily_minutes=60
        )

        scheduled = {i.prep_topic_id for i in items if i.item_type == "STUDY"}
        assert scheduled == {f"t-{i}" for i in range(8)}

    def test_no_item_is_scheduled_past_the_window(self):
        topics = [_topic(f"t-{i}", title=f"T{i}") for i in range(5)]
        competence = {f"t-{i}": _competence(f"t-{i}", 20.0) for i in range(5)}

        items = prep_plan_adaptive.schedule(
            topics, competence, days_available=7, start=START, max_daily_minutes=60
        )

        assert all((i.scheduled_date - START).days <= 6 for i in items)


class TestScheduleEdges:
    def test_no_topics_is_no_plan(self):
        assert (
            prep_plan_adaptive.schedule(
                [], {}, days_available=30, start=START, max_daily_minutes=60
            )
            == []
        )

    def test_no_days_is_no_plan(self):
        """Rather than dividing by zero or cramming everything onto one day. The
        caller refuses a past target date before reaching here; this is the floor."""
        topics = [_topic("t-1", title="One")]
        assert (
            prep_plan_adaptive.schedule(
                topics, {}, days_available=0, start=START, max_daily_minutes=60
            )
            == []
        )

    def test_a_topic_with_no_estimate_still_gets_a_session(self):
        topic = _topic("t-1", title="One")
        topic.estimated_minutes = None
        items = prep_plan_adaptive.schedule(
            [topic], {}, days_available=30, start=START, max_daily_minutes=60
        )
        assert items[0].estimated_minutes >= 10

    def test_competence_missing_entirely_is_treated_as_unmeasured(self):
        """The dict is keyed by topic id and a topic can be absent from it. Absent
        must mean "no evidence", not crash and not "weak"."""
        topics = [_topic("t-1", title="One")]
        items = prep_plan_adaptive.schedule(
            topics, {}, days_available=30, start=START, max_daily_minutes=60
        )
        assert [i.item_type for i in items] == ["STUDY", "REVIEW"]


class TestStrategyLabels:
    def test_the_two_strategies_are_distinguishable(self):
        """Recorded on the plan so "adaptive" is a checkable property of a row
        rather than a claim in a docstring — which is how it went unnoticed that
        nothing branched on `is_adaptive`."""
        assert prep_plan_adaptive.STRATEGY_ADAPTIVE == "ADAPTIVE"
        assert prep_plan_adaptive.STRATEGY_EVEN == "EVEN"
        assert prep_plan_adaptive.STRATEGY_ADAPTIVE != prep_plan_adaptive.STRATEGY_EVEN

    def test_the_repository_accepts_the_strategy_key(self):
        """A field map that silently drops it would leave the column NULL forever
        with nothing failing — the same shape as the `ExamPrep.type` defect."""
        from src.domains.personal_learning.repository import PersonalLearningRepository

        mapped = PersonalLearningRepository._map_study_plan(
            {"strategy": prep_plan_adaptive.STRATEGY_ADAPTIVE}
        )
        assert mapped == {"strategy": "ADAPTIVE"}
