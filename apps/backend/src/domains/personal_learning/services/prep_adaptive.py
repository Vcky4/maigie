"""Composing an adaptive practice session.

Phase D. Until now `ADAPTIVE` was a name: `start_quiz` branched on FULL_PRACTICE,
WEAK_AREAS and TOPIC_FOCUS, and everything else — including the two modes billed as
Plus features — fell through to `all_topics[:5]`, the free quick-review path.

# What adaptation means here

    That adjusts difficulty before frustration builds.
    -- content/intelligence/ch27-towards-autonomous-learning.mdx

and, on the harder question of how far to push:

    How long should I let them struggle?
    When does productive struggle become unproductive frustration?
    These are questions of judgment. They have no formula.
    -- content/intelligence/ch24-reasoning.mdx

The book supplies that tension and declines to resolve it. This module's resolution
is to aim at neither easy nor hard but at the learner's **frontier**: questions they
are likely — not certain — to get right. That satisfies both instructions without
choosing between them, because the frontier is not a fixed line. As competence
rises it rises too, so difficulty increases without anyone deciding to make things
harder.

# What this deliberately does not do yet

Reactive, within-session adaptation — noticing frustration building on question
four and softening question five — needs questions to be delivered one at a time.
Today a session's questions are all created up front. So adaptation here happens at
**composition** time: the session is built to ramp gently and to include
consolidation work, rather than reacting as it is played. Reactive delivery is a
separate change and is recorded as such rather than quietly skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import prep_competence

# The frontier. High enough to sustain momentum, low enough to be real practice.
# Chosen, not derived — the book explicitly refuses to give a number.
TARGET_SUCCESS_LOW = 0.70
TARGET_SUCCESS_HIGH = 0.80

# Roughly this share of an adaptive session revisits material the learner is already
# strong on. Not filler: a session composed only of weaknesses is relentless, and
# "adjusts difficulty before frustration builds" means building in relief before it
# is needed rather than waiting for it.
CONSOLIDATION_SHARE = 0.2

# With no evidence at all, start in the middle. Guessing a learner is weak is as
# wrong as guessing they are strong, and calibrating is the fastest way out of not
# knowing.
CALIBRATION_DIFFICULTY = "MEDIUM"


@dataclass(frozen=True)
class TopicSlot:
    """One question's worth of intent: which topic, and how hard."""

    topic_id: str
    difficulty: str
    #: Why this slot exists. Carried so a session can explain itself, per the
    #: requirement that recommendations be able to show their reasoning.
    reason: str


def target_difficulty(competence: prep_competence.TopicCompetence | None) -> str:
    """The difficulty that puts this topic near the learner's frontier.

    Not "easier when struggling". A learner at 40% on a topic is not served by easy
    questions they will also get right without learning anything — they are served
    by the level just above where they are reliable. That is why `focus` maps to
    MEDIUM rather than EASY, and EASY is reserved for the case where even medium
    questions are not landing.
    """
    if competence is None or not competence.is_measurable or competence.retention is None:
        return CALIBRATION_DIFFICULTY

    retention = competence.retention

    # Not reliable even on the basics: step down to rebuild footing.
    if retention < 40:
        return "EASY"
    # Below the focus threshold: medium is the frontier.
    if retention < 70:
        return "MEDIUM"
    # In the review band, or strong: stretch.
    if retention < 90:
        return "HARD"
    return "HARD"


def _need_rank(competence: prep_competence.TopicCompetence | None) -> tuple[int, float]:
    """Sort key: what most needs practice comes first.

    Unmeasured topics rank above weak ones. That is deliberate — a topic we know
    nothing about could be the learner's worst, and we cannot tell them where they
    stand while it is unmeasured. Gathering evidence *is* the useful next step.
    """
    if competence is None or not competence.is_measurable:
        return (0, 0.0)

    retention = competence.retention if competence.retention is not None else 0.0

    band = competence.band
    if band == "focus":
        return (1, retention)
    if band == "review":
        return (2, retention)
    return (3, retention)


def rank_topics(
    topics: Sequence[Any],
    competence_by_topic: dict[str, prep_competence.TopicCompetence],
) -> list[Any]:
    """Order topics by how much they would benefit from practice now."""
    return sorted(
        topics,
        key=lambda topic: (
            *_need_rank(competence_by_topic.get(topic.id)),
            topic.order_index or 0,
        ),
    )


def plan_session(
    topics: Sequence[Any],
    competence_by_topic: dict[str, prep_competence.TopicCompetence],
    *,
    count: int,
) -> list[TopicSlot]:
    """Compose an adaptive session: which topics, at which difficulty, in what order.

    Pure, so the composition rules are testable without a database or an LLM.
    """
    if not topics or count <= 0:
        return []

    ranked = rank_topics(topics, competence_by_topic)

    strong = [
        t for t in ranked if (competence_by_topic.get(t.id) or _unmeasured()).band == "strong"
    ]
    focus = [t for t in ranked if t not in strong]

    # Reserve a slice for consolidation, but only if there is strong material to
    # consolidate and enough questions for it to be worth doing.
    consolidation_slots = 0
    if strong and count >= 5:
        consolidation_slots = max(1, int(count * CONSOLIDATION_SHARE))
        consolidation_slots = min(consolidation_slots, len(strong), count - 1)

    practice_slots = count - consolidation_slots
    pool = focus or ranked

    slots: list[TopicSlot] = []
    for index in range(practice_slots):
        topic = pool[index % len(pool)]
        competence = competence_by_topic.get(topic.id)
        slots.append(
            TopicSlot(
                topic_id=topic.id,
                difficulty=target_difficulty(competence),
                reason=_reason_for(competence),
            )
        )

    for index in range(consolidation_slots):
        topic = strong[index % len(strong)]
        slots.append(
            TopicSlot(
                topic_id=topic.id,
                difficulty="HARD",
                reason="keeping a strong topic sharp",
            )
        )

    # Gentle ramp: easier work first so a session opens with momentum rather than
    # with its hardest question. This is the composition-time reading of "adjusts
    # difficulty before frustration builds".
    order = {"EASY": 0, "MEDIUM": 1, "HARD": 2}
    slots.sort(key=lambda slot: order.get(slot.difficulty, 1))
    return slots


def _unmeasured() -> prep_competence.TopicCompetence:
    return prep_competence.estimate([])


def _reason_for(competence: prep_competence.TopicCompetence | None) -> str:
    """Plain words, because a session should be able to say why it chose this."""
    if competence is None or not competence.is_measurable:
        return "not practised enough yet to tell where you stand"
    band = competence.band
    if band == "focus":
        return "this is where you are least reliable"
    if band == "review":
        return "close to solid, worth pushing"
    return "keeping a strong topic sharp"


async def load_plan(
    *, user_id: str, topics: Sequence[Any], count: int
) -> tuple[list[TopicSlot], dict[str, prep_competence.TopicCompetence]]:
    """Load competence for the topics and compose a session from it."""
    competence_by_topic = await prep_competence.load_for_topics(
        user_id=user_id, topic_ids=[topic.id for topic in topics]
    )
    return plan_session(topics, competence_by_topic, count=count), competence_by_topic
