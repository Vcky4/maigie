"""Adaptive scheduling for a preparation's study plan.

`generate_plan` computed `is_adaptive = quality_tier == "plus"` and **nothing
branched on it.** Its only effect was recording that a Plus feature had been used.
A Plus learner's plan was byte-for-byte a Free learner's: the same even walk through
topics in `orderIndex` order, the same flat 15-minute review on the first third of
items. Meanwhile the docstring promised "adaptive scheduling that adjusts based on
quiz performance and behaviour" and the commercial surface sold "Adaptive study
plans". This module is what makes that true.

# What adapts

Three things, all from evidence that already existed and was not being read:

1. **Order.** Topics are scheduled by *need* rather than by `orderIndex`, reusing
   `prep_adaptive.rank_topics` so a plan and an adaptive practice session agree
   about what needs work. Scheduling the weakest topic first is not cosmetic: it
   gets the most remaining days, so it can be revisited more times before the date.

2. **Time.** A topic the learner is at 30% on does not need the same minutes as one
   at 85%. Estimated minutes are scaled by the gap to the target, bounded, so the
   plan spends the learner's time where it changes the outcome.

3. **Revisits.** Spacing is per topic and proportional to weakness — two revisits
   for a focus topic, one for review, none for a strong one — instead of one flat
   review applied to whichever third of the list happened to be first.

# What does not adapt, and why

Nothing here reschedules in response to what happens next; `complete_item` already
redistributes when a learner falls behind, and that stays where it is. This module
decides the plan at composition time, which is the same boundary
`prep_adaptive.plan_session` draws and for the same reason: adaptation as the plan
is *lived* is a different change.

    That adjusts difficulty before frustration builds.
    -- content/intelligence/ch27-towards-autonomous-learning.mdx

The reading taken here is that a schedule adapts by allocating effort, not by
making individual sessions harder — difficulty is the practice session's job, and
`prep_adaptive` already owns it.

Pure. No database, no LLM, no clock beyond the start date it is handed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from . import prep_adaptive, prep_competence

#: Recorded on the plan so the claim is inspectable rather than asserted. A plan
#: that says it was scheduled adaptively can be checked against this module.
STRATEGY_ADAPTIVE = "ADAPTIVE"
STRATEGY_EVEN = "EVEN"

#: Mastery a plan aims a topic at when the preparation states no target. Used only
#: to size the gap, never written anywhere as a target the learner chose.
DEFAULT_TARGET_MASTERY = 80.0

#: How far minutes may be scaled for a weak topic. A topic at zero retention gets
#: 1.6x the time, not five times it: the bound exists because a plan that pours
#: every available minute into one topic stops being a plan for the exam.
MAX_TIME_MULTIPLIER = 1.6
MIN_TIME_MULTIPLIER = 0.8

#: Revisits by band. Spacing is what makes a revisit worth scheduling at all, so a
#: topic that needs none gets none rather than a token one.
REVISITS_BY_BAND = {"focus": 2, "review": 1, "strong": 0}
#: An unmeasured topic gets one revisit: enough to produce a second reading of
#: whether it stuck, without spending on a topic that may already be solid.
REVISITS_UNMEASURED = 1

#: Days after the study session for the first and second revisit. Expanding rather
#: than fixed, which is the whole point of spacing.
REVISIT_OFFSETS = (3, 9)

#: A revisit is a check, not a re-study.
REVISIT_MINUTES = 15


@dataclass(frozen=True)
class ScheduledItem:
    """One dated row of the plan."""

    title: str
    description: str | None
    scheduled_date: datetime
    estimated_minutes: int
    item_type: str
    topic_id: str | None
    prep_topic_id: str | None


def time_multiplier(
    competence: prep_competence.TopicCompetence | None,
    *,
    target_mastery: float = DEFAULT_TARGET_MASTERY,
) -> float:
    """How much of the base estimate this topic should get.

    An unmeasured topic gets exactly its estimate. We do not know whether it needs
    more, and inflating it would take time from a topic measured as weak — which is
    the one case we do have evidence about.
    """
    if competence is None or not competence.is_measurable or competence.retention is None:
        return 1.0

    gap = max(0.0, target_mastery - competence.retention) / max(target_mastery, 1.0)
    multiplier = MIN_TIME_MULTIPLIER + gap * (MAX_TIME_MULTIPLIER - MIN_TIME_MULTIPLIER)
    return max(MIN_TIME_MULTIPLIER, min(MAX_TIME_MULTIPLIER, multiplier))


def revisit_count(competence: prep_competence.TopicCompetence | None) -> int:
    """How many spaced revisits this topic earns."""
    if competence is None or not competence.is_measurable:
        return REVISITS_UNMEASURED
    return REVISITS_BY_BAND.get(competence.band, REVISITS_UNMEASURED)


def _reason(competence: prep_competence.TopicCompetence | None) -> str:
    """Why this topic is placed where it is, in words a learner can read.

    Written onto the item's description, because a plan that reorders someone's
    topics owes them an explanation for the order.
    """
    if competence is None or not competence.is_measurable:
        return "Scheduled early because you have not practised this enough to tell where you stand."
    band = competence.band
    retention = round(competence.retention or 0.0)
    if band == "focus":
        return f"More time here: you are at {retention}% on this, your weakest area."
    if band == "review":
        return f"Close to solid at {retention}%. Scheduled to finish it off."
    return f"Strong at {retention}%. A shorter session to keep it."


def schedule(
    topics: Sequence[Any],
    competence_by_topic: dict[str, prep_competence.TopicCompetence],
    *,
    days_available: int,
    start: datetime,
    max_daily_minutes: float,
    target_mastery: float | None = None,
) -> list[ScheduledItem]:
    """Build the dated plan.

    `topics` are the ORM topic rows, so ranking can reuse `prep_adaptive`. Returns
    study items and their revisits together, ordered by date.
    """
    if not topics or days_available <= 0:
        return []

    target = target_mastery if target_mastery is not None else DEFAULT_TARGET_MASTERY
    ranked = prep_adaptive.rank_topics(topics, competence_by_topic)

    items: list[ScheduledItem] = []
    day_index = 0
    minutes_used = 0.0
    # The last day the plan may touch. A revisit past this is dropped rather than
    # clamped onto the final day, where it would collide with the exam.
    last_day = days_available - 1

    for topic in ranked:
        competence = competence_by_topic.get(topic.id)
        base = getattr(topic, "estimated_minutes", None) or 30
        minutes = max(10, round(base * time_multiplier(competence, target_mastery=target)))

        if minutes_used + minutes > max_daily_minutes and minutes_used > 0:
            day_index += 1
            minutes_used = 0.0

        # Weakest first means weakest gets the earliest day and therefore the most
        # room for revisits. Wrapping rather than overflowing keeps every topic in
        # the plan when there are more topics than days.
        study_day = day_index % days_available
        study_date = start + timedelta(days=study_day)

        items.append(
            ScheduledItem(
                title=topic.title,
                description=_reason(competence),
                scheduled_date=study_date,
                estimated_minutes=minutes,
                item_type="STUDY",
                topic_id=None,
                prep_topic_id=topic.id,
            )
        )
        minutes_used += minutes

        for offset in REVISIT_OFFSETS[: revisit_count(competence)]:
            revisit_day = study_day + offset
            if revisit_day > last_day:
                # No room before the date. Dropped, not squeezed in: a revisit the
                # day before the exam is not spaced practice.
                continue
            items.append(
                ScheduledItem(
                    title=f"Review: {topic.title}",
                    description=f"Spaced check {offset} days on, to see what stuck.",
                    scheduled_date=start + timedelta(days=revisit_day),
                    estimated_minutes=REVISIT_MINUTES,
                    item_type="REVIEW",
                    topic_id=None,
                    prep_topic_id=topic.id,
                )
            )

        if minutes_used >= max_daily_minutes:
            day_index += 1
            minutes_used = 0.0

    items.sort(key=lambda item: item.scheduled_date)
    return items


async def load_and_schedule(
    *,
    user_id: str,
    topics: Sequence[Any],
    days_available: int,
    start: datetime,
    max_daily_minutes: float,
    target_mastery: float | None = None,
) -> list[ScheduledItem]:
    """Load competence for these topics and schedule from it."""
    competence_by_topic = await prep_competence.load_for_topics(
        user_id=user_id, topic_ids=[topic.id for topic in topics]
    )
    return schedule(
        topics,
        competence_by_topic,
        days_available=days_available,
        start=start,
        max_daily_minutes=max_daily_minutes,
        target_mastery=target_mastery,
    )
