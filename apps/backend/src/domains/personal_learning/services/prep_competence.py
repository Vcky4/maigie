"""What practice reveals about a learner, per topic.

Phase B of the learning-intelligence design. This replaces a lifetime average with
an estimate that decays, weights, and knows how much it does not know.

# Why the previous model had to go

Mastery was `correct / total` over every answer ever recorded. Two consequences,
both of which the book rules out explicitly:

    A bad day is not a pattern.
    A single mistake is not a weakness.
    ...
    It should weight recent experience appropriately.
    It should never trap someone in their past.
    -- content/intelligence/ch23-memory.mdx

A lifetime average traps people. A learner wrong ten times last month and right ten
times today read 50%, indistinguishable from someone guessing. And a topic with one
answered question read 0% or 100%, making a single mistake the entire model.

# Why there are four numbers and not one

    It must resist the temptation to optimise toward a single metric.
    It must embrace nuance.
    -- content/intelligence/ch24-reasoning.mdx

`retention` answers "do they still know it". `fluency` answers "how effortfully".
`independence` answers "can they do it unaided". `reliability` answers "is this
consistent or was it luck". They are different questions and a single percentage
cannot answer them.

`evidence` is not a fifth skill. It governs whether we are entitled to say anything
at all.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ..repository import personal_learning_repo as repo

# How quickly evidence loses influence. At 14 days an observation counts half as
# much as a fresh one. Chosen, not derived: the book says "weight recent experience
# appropriately" and declines to say how.
RECENCY_HALF_LIFE_DAYS = 14.0

# How far back to read at all. Beyond four half-lives an observation carries under
# 3% weight, so including it costs a row and changes nothing.
OBSERVATION_WINDOW_DAYS = 120

# Harder questions are stronger evidence. A correct answer on a HARD question says
# more than the same answer on an EASY one.
_DIFFICULTY_WEIGHT = {"EASY": 0.8, "MEDIUM": 1.0, "HARD": 1.3}
_DEFAULT_DIFFICULTY_WEIGHT = 1.0

# Credit for a correct answer, by how many hints were taken. A hinted-correct answer
# is real evidence — of *assisted* competence — so it earns less than an unaided one
# and is never treated as wrong.
_HINT_CREDIT = {0: 1.0, 1: 0.6, 2: 0.4}
_MANY_HINTS_CREDIT = 0.3

# Below this we decline to report a number. "A single mistake is not a weakness",
# so one observation is not an assessment.
MIN_OBSERVATIONS = 3
# And three observations from two months ago are not an assessment either, which is
# why the threshold is on decayed weight rather than on raw count alone.
MIN_EFFECTIVE_WEIGHT = 1.0

# The mastery ladder is unchanged and still lives in prep_readiness. Imported at use
# rather than at module load to keep these two modules independent.
CompetenceBand = Literal["focus", "review", "strong"]


def _difficulty_weight(difficulty: str | None) -> float:
    return _DIFFICULTY_WEIGHT.get((difficulty or "").upper(), _DEFAULT_DIFFICULTY_WEIGHT)


def _hint_credit(hint_count: int) -> float:
    return _HINT_CREDIT.get(max(0, hint_count), _MANY_HINTS_CREDIT)


def _recency_weight(observed_at: datetime, now: datetime) -> float:
    """Exponential decay. Never zero, so old evidence fades rather than vanishing."""
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - observed_at).total_seconds() / 86400)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _clamp_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


@dataclass(frozen=True)
class TopicCompetence:
    """An estimate, with its own uncertainty attached.

    Every percentage is `None` when there is not enough evidence to justify it.
    That is deliberate: reporting `0` for an unpractised topic tells the learner
    they know nothing, which is a claim we have not earned.
    """

    topic_id: str | None
    observations: int
    #: Sum of recency weights — the effective sample size after decay.
    effective_weight: float

    retention: float | None
    fluency: float | None
    independence: float | None
    reliability: float | None

    @property
    def is_measurable(self) -> bool:
        """Whether we have enough evidence to assert anything."""
        return (
            self.observations >= MIN_OBSERVATIONS and self.effective_weight >= MIN_EFFECTIVE_WEIGHT
        )

    @property
    def band(self) -> CompetenceBand | None:
        """Position on the shared mastery ladder, or `None` if not measurable."""
        if not self.is_measurable or self.retention is None:
            return None
        from . import prep_readiness

        return prep_readiness.mastery_band(self.retention)

    @property
    def needs_attention(self) -> bool:
        """Worth practising next.

        An unmeasured topic needs attention as much as a weak one: not knowing is a
        reason to look, not a reason to skip. This deliberately does not distinguish
        them, because both answer "what should I do next" with yes.
        """
        return not self.is_measurable or self.band == "focus"


def estimate(
    observations: Sequence[Any],
    *,
    topic_id: str | None = None,
    now: datetime | None = None,
    baseline_ms: float | None = None,
) -> TopicCompetence:
    """Estimate competence from observations. Pure; no database access.

    `baseline_ms` is the learner's own typical response time, used to judge fluency
    relative to themselves. Reading speed varies between people and a slow reader is
    not a weak learner, so an absolute threshold would measure the wrong thing.
    """
    reference = now or datetime.now(UTC)

    if not observations:
        return TopicCompetence(
            topic_id=topic_id,
            observations=0,
            effective_weight=0.0,
            retention=None,
            fluency=None,
            independence=None,
            reliability=None,
        )

    weights: list[float] = []
    weighted_credit = 0.0
    weighted_total = 0.0
    unaided_weight = 0.0
    recency_total = 0.0
    credits: list[float] = []
    latencies: list[tuple[float, int]] = []

    for observation in observations:
        recency = _recency_weight(observation.observed_at, reference)
        difficulty = _difficulty_weight(getattr(observation, "difficulty", None))
        hint_count = getattr(observation, "hint_count", 0) or 0

        credit = _hint_credit(hint_count) if observation.is_correct else 0.0
        weight = recency * difficulty

        weights.append(weight)
        weighted_credit += weight * credit
        weighted_total += weight
        recency_total += recency
        credits.append(credit)

        if hint_count == 0:
            unaided_weight += recency

        response_ms = getattr(observation, "response_ms", None)
        if response_ms:
            latencies.append((recency, int(response_ms)))

    result = TopicCompetence(
        topic_id=topic_id,
        observations=len(observations),
        effective_weight=round(recency_total, 3),
        retention=None,
        fluency=None,
        independence=None,
        reliability=None,
    )

    if not result.is_measurable:
        # Evidence exists but is too thin or too stale to support a number. The
        # counts are still reported so a surface can say "based on 2 questions".
        return result

    retention = _clamp_percent((weighted_credit / weighted_total) * 100) if weighted_total else None
    independence = _clamp_percent((unaided_weight / recency_total) * 100) if recency_total else None

    # Consistency, not skill: tight spread means the estimate is trustworthy,
    # scattered results mean the learner is inconsistent on this topic and the
    # headline number is hiding that.
    reliability: float | None = None
    if len(credits) >= 2:
        spread = statistics.pstdev(credits)
        # Credit is bounded 0..1, so the worst case spread is 0.5.
        reliability = _clamp_percent((1 - min(spread / 0.5, 1.0)) * 100)

    fluency = _fluency(latencies, baseline_ms=baseline_ms)

    return TopicCompetence(
        topic_id=topic_id,
        observations=result.observations,
        effective_weight=result.effective_weight,
        retention=retention,
        fluency=fluency,
        independence=independence,
        reliability=reliability,
    )


def _fluency(latencies: Sequence[tuple[float, int]], *, baseline_ms: float | None) -> float | None:
    """How effortfully the learner answers this topic, relative to themselves.

    `None` when there is no timing data or no personal baseline — not `0`, which
    would read as "maximally laboured" for a learner whose client simply never
    reported timings.

    Deliberately never surfaced as a judgement. "You were slow" is not something
    this product should say; the value of the signal is that it separates "knows it"
    from "worked it out", which need different next steps.
    """
    if not latencies or not baseline_ms or baseline_ms <= 0:
        return None

    total_weight = sum(weight for weight, _ in latencies)
    if total_weight <= 0:
        return None

    mean_ms = sum(weight * ms for weight, ms in latencies) / total_weight
    ratio = mean_ms / baseline_ms

    # At or under the learner's baseline is fluent; at three times baseline the
    # topic is clearly effortful. Linear between, clamped.
    if ratio <= 1.0:
        return 100.0
    if ratio >= 3.0:
        return 0.0
    return _clamp_percent((1 - (ratio - 1.0) / 2.0) * 100)


def response_baseline(observations: Sequence[Any]) -> float | None:
    """The learner's own typical response time, in milliseconds.

    The median rather than the mean, because one interrupted question — a learner
    who walked away mid-session — would otherwise redefine "normal" for them.
    """
    timings = [
        int(observation.response_ms)
        for observation in observations
        if getattr(observation, "response_ms", None)
    ]
    if len(timings) < MIN_OBSERVATIONS:
        return None
    return float(statistics.median(timings))


async def load_for_topics(
    *, user_id: str, topic_ids: Sequence[str], now: datetime | None = None
) -> dict[str, TopicCompetence]:
    """Estimate competence for several topics in one read.

    One query for the whole set, then grouping in memory, so adding topics does not
    add round trips. The learner's response baseline is computed across *all* the
    returned observations rather than per topic, because it describes the person.
    """
    if not topic_ids:
        return {}

    reference = now or datetime.now(UTC)
    since = reference - timedelta(days=OBSERVATION_WINDOW_DAYS)
    observations = await repo.list_topic_observations(
        user_id=user_id, topic_ids=list(topic_ids), since=since
    )

    baseline = response_baseline(observations)

    grouped: dict[str, list[Any]] = {topic_id: [] for topic_id in topic_ids}
    for observation in observations:
        if observation.prep_topic_id in grouped:
            grouped[observation.prep_topic_id].append(observation)

    return {
        topic_id: estimate(rows, topic_id=topic_id, now=reference, baseline_ms=baseline)
        for topic_id, rows in grouped.items()
    }


async def load_for_topic(
    *, user_id: str, topic_id: str, now: datetime | None = None
) -> TopicCompetence:
    """Estimate competence for a single topic."""
    result = await load_for_topics(user_id=user_id, topic_ids=[topic_id], now=now)
    return result.get(topic_id) or estimate([], topic_id=topic_id, now=now)
