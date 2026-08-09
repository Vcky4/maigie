"""Unit tests for adaptive session composition (no DB required).

`ADAPTIVE` was billed as a Plus feature and behaved exactly like the free
quick-review path. These tests pin what now makes it different.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.domains.personal_learning.services import prep_competence
from src.domains.personal_learning.services.prep_adaptive import (
    CALIBRATION_DIFFICULTY,
    plan_session,
    rank_topics,
    target_difficulty,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def _topic(topic_id: str, order: int = 0):
    return SimpleNamespace(id=topic_id, title=f"Topic {topic_id}", order_index=order)


def _competence(retention: float | None, *, measurable: bool = True):
    """Build a real TopicCompetence by feeding it observations, not by faking it."""
    if not measurable:
        return prep_competence.estimate([], now=NOW)

    # Enough observations to clear the evidence threshold, with the right mix of
    # correct and incorrect to land on the retention we want.
    total = 10
    correct = round((retention or 0) / 100 * total)
    observations = [
        SimpleNamespace(
            prep_topic_id="t",
            is_correct=index < correct,
            observed_at=NOW,
            hint_count=0,
            difficulty="MEDIUM",
            response_ms=None,
        )
        for index in range(total)
    ]
    return prep_competence.estimate(observations, now=NOW)


# ---------------------------------------------------------------------------
# TestTargetDifficulty
# ---------------------------------------------------------------------------


class TestTargetDifficulty:
    """Aim at the frontier, not at "easier when struggling"."""

    def test_unmeasured_topics_calibrate_in_the_middle(self):
        """Guessing a learner is weak is as wrong as guessing they are strong."""
        assert target_difficulty(_competence(None, measurable=False)) == CALIBRATION_DIFFICULTY

    def test_none_competence_calibrates(self):
        assert target_difficulty(None) == CALIBRATION_DIFFICULTY

    def test_very_low_retention_steps_down_to_easy(self):
        assert target_difficulty(_competence(20)) == "EASY"

    def test_weak_but_not_lost_gets_medium_not_easy(self):
        """A learner at 50% is not served by easy questions they will also get right
        without learning anything. Medium is their frontier."""
        assert target_difficulty(_competence(50)) == "MEDIUM"

    def test_review_band_stretches_to_hard(self):
        assert target_difficulty(_competence(75)) == "HARD"

    def test_strong_stays_hard(self):
        assert target_difficulty(_competence(100)) == "HARD"

    def test_difficulty_never_decreases_as_competence_rises(self):
        order = {"EASY": 0, "MEDIUM": 1, "HARD": 2}
        levels = [order[target_difficulty(_competence(r))] for r in range(0, 101, 10)]
        assert levels == sorted(levels)


# ---------------------------------------------------------------------------
# TestRankTopics
# ---------------------------------------------------------------------------


class TestRankTopics:
    def test_unmeasured_topics_come_first(self):
        """We cannot tell a learner where they stand while a topic is unmeasured, so
        gathering evidence is itself the useful next step."""
        topics = [_topic("known"), _topic("unknown")]
        competence = {
            "known": _competence(30),
            "unknown": _competence(None, measurable=False),
        }
        assert [t.id for t in rank_topics(topics, competence)] == ["unknown", "known"]

    def test_weaker_topics_come_before_stronger(self):
        topics = [_topic("strong"), _topic("weak"), _topic("middling")]
        competence = {
            "strong": _competence(90),
            "weak": _competence(30),
            "middling": _competence(75),
        }
        assert [t.id for t in rank_topics(topics, competence)] == [
            "weak",
            "middling",
            "strong",
        ]

    def test_ties_fall_back_to_topic_order(self):
        topics = [_topic("b", order=2), _topic("a", order=1)]
        competence = {"a": _competence(50), "b": _competence(50)}
        assert [t.id for t in rank_topics(topics, competence)] == ["a", "b"]

    def test_missing_competence_is_treated_as_unmeasured(self):
        topics = [_topic("known"), _topic("absent")]
        assert rank_topics(topics, {"known": _competence(40)})[0].id == "absent"


# ---------------------------------------------------------------------------
# TestPlanSession
# ---------------------------------------------------------------------------


class TestPlanSession:
    def test_produces_the_requested_number_of_slots(self):
        topics = [_topic("a"), _topic("b")]
        competence = {"a": _competence(40), "b": _competence(50)}
        assert len(plan_session(topics, competence, count=6)) == 6

    def test_no_topics_produces_no_plan(self):
        assert plan_session([], {}, count=5) == []

    def test_zero_count_produces_no_plan(self):
        assert plan_session([_topic("a")], {"a": _competence(50)}, count=0) == []

    def test_concentrates_on_topics_that_need_work(self):
        topics = [_topic("weak"), _topic("strong")]
        competence = {"weak": _competence(30), "strong": _competence(95)}
        slots = plan_session(topics, competence, count=4)
        weak_slots = [s for s in slots if s.topic_id == "weak"]
        assert len(weak_slots) >= 3

    def test_includes_consolidation_when_there_is_strong_material(self):
        """A session composed only of weaknesses is relentless. Relief is built in
        before frustration, not after it."""
        topics = [_topic("weak"), _topic("strong")]
        competence = {"weak": _competence(30), "strong": _competence(95)}
        slots = plan_session(topics, competence, count=10)
        assert any(s.topic_id == "strong" for s in slots)

    def test_no_consolidation_without_strong_material(self):
        topics = [_topic("weak"), _topic("weaker")]
        competence = {"weak": _competence(40), "weaker": _competence(20)}
        slots = plan_session(topics, competence, count=10)
        assert {s.topic_id for s in slots} == {"weak", "weaker"}

    def test_short_sessions_skip_consolidation(self):
        """With three questions, spending one on revision is a poor trade."""
        topics = [_topic("weak"), _topic("strong")]
        competence = {"weak": _competence(30), "strong": _competence(95)}
        slots = plan_session(topics, competence, count=3)
        assert all(s.topic_id == "weak" for s in slots)

    def test_consolidation_never_takes_the_whole_session(self):
        topics = [_topic("strong-a"), _topic("strong-b")]
        competence = {"strong-a": _competence(95), "strong-b": _competence(90)}
        slots = plan_session(topics, competence, count=8)
        assert len(slots) == 8

    def test_difficulty_ramps_gently(self):
        """A session should open with momentum, not with its hardest question."""
        topics = [_topic("lost"), _topic("weak"), _topic("solid")]
        competence = {
            "lost": _competence(15),
            "weak": _competence(55),
            "solid": _competence(85),
        }
        slots = plan_session(topics, competence, count=9)
        order = {"EASY": 0, "MEDIUM": 1, "HARD": 2}
        levels = [order[s.difficulty] for s in slots]
        assert levels == sorted(levels)

    def test_every_slot_carries_a_reason(self):
        """A session should be able to explain why it chose what it chose."""
        topics = [_topic("a"), _topic("b")]
        competence = {"a": _competence(30), "b": _competence(None, measurable=False)}
        slots = plan_session(topics, competence, count=4)
        assert all(s.reason for s in slots)

    def test_unmeasured_topics_get_an_honest_reason(self):
        slots = plan_session([_topic("new")], {"new": _competence(None, measurable=False)}, count=3)
        assert "not practised enough" in slots[0].reason

    def test_all_strong_still_produces_a_session(self):
        """Nothing needs work, but the learner asked to practise."""
        topics = [_topic("a"), _topic("b")]
        competence = {"a": _competence(95), "b": _competence(90)}
        slots = plan_session(topics, competence, count=5)
        assert len(slots) == 5

    def test_plan_reflects_decayed_evidence(self):
        """Stale success should not keep a topic out of the plan."""
        stale = [
            SimpleNamespace(
                prep_topic_id="t",
                is_correct=True,
                observed_at=NOW - timedelta(days=120),
                hint_count=0,
                difficulty="MEDIUM",
                response_ms=None,
            )
            for _ in range(6)
        ]
        competence = {"faded": prep_competence.estimate(stale, now=NOW)}
        slots = plan_session([_topic("faded")], competence, count=3)
        # Unmeasurable once decayed, so it calibrates rather than assuming mastery.
        assert all(s.difficulty == CALIBRATION_DIFFICULTY for s in slots)
