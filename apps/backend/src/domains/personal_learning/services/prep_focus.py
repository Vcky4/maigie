"""What to practise next in one preparation, and why.

The Prepare surface has asked this question in three places — a "next action" on
every dashboard card, the workspace's current-focus panel, and the practice
launcher's recommended topic — and until now answered it with a fixture string.

It cannot be answered on the client. The dashboard's `focusTopics` list is
bounded across *all* active preparations, so a given preparation may not appear in
it at all, and a client picking the lowest mastery it happens to have received
would recommend confidently from an incomplete list.

Design notes:

- **A code plus a sentence.** `reason_code` is what logic and tests depend on;
  `reason` is a short sentence for surfaces that want prose. A client is free to
  render its own copy from the code and drop the sentence entirely.
- **The recommendation is evidence, not encouragement.** Wording states what was
  measured ("no questions answered yet") rather than motivating. A recommendation
  that overstates its own confidence is worse than none.
- **A preparation with no topics still gets a recommendation** — to extract topics.
  That is the actual next action, and it keeps the field non-null in the one case
  where a client would otherwise have to invent an empty state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from . import prep_readiness

FocusReason = Literal[
    "NO_TOPICS",
    "NEVER_PRACTISED",
    "LOWEST_MASTERY",
    "MAINTENANCE",
]

# Mastery below which a topic is a weak area, matching what `WEAK_AREAS` selects.
_FOCUS_THRESHOLD = 70.0


@dataclass(frozen=True)
class FocusRecommendation:
    topic_id: str | None
    topic_title: str | None
    mastery_percent: float | None
    band: prep_readiness.MasteryBand | None
    reason_code: FocusReason
    reason: str
    recommended_mode: str
    recommended_question_count: int
    estimated_minutes: int


def _sizing(mode: str, topics: list[Any]) -> tuple[int, int]:
    """The question count and duration this recommendation will actually produce.

    Deferred to `quiz_engine`, which owns the rule, so the recommendation cannot
    promise a different number from what the session then asks. Duration follows
    the count: it previously came from the topic's own `estimatedMinutes`, which is
    the time to *study* that topic end to end, so a five-question set was
    advertised as 45 minutes.

    The topic count passed in is the number the *mode* will target, not the number
    the preparation has — `WEAK_AREAS` sizes from the weak topics, a drill from one.
    """
    from . import quiz_engine

    if mode == "TOPIC_FOCUS":
        target = 1
    elif mode == "WEAK_AREAS":
        target = sum(
            1
            for topic in topics
            if (getattr(topic, "mastery_score", 0.0) or 0.0) < _FOCUS_THRESHOLD
        )
    else:
        target = len(topics)

    count = quiz_engine.default_question_count(mode, target)
    return count, quiz_engine.estimated_minutes(count)


def recommend(
    topics: list[Any],
    *,
    answered_by_topic: dict[str, int] | None = None,
) -> FocusRecommendation:
    """Choose the next topic to practise from a preparation's topics.

    Pure, so the selection rules are testable without a database.

    `answered_by_topic` maps topic id to how many of its questions the learner has
    answered. Supplying it lets an entirely unpractised topic be recognised as
    such, which is a different recommendation from a topic that has been practised
    and scored badly — the first needs a baseline, the second needs work.

    **`None` and `{}` mean different things.** `None` is "no count data was
    loaded", and selection falls back to mastery alone. `{}` is "counts were
    loaded and nothing has been answered", which is itself information: a
    preparation with no banked questions has practised nothing, and treating that
    as missing data made a fresh preparation report its first topic as
    "your lowest-scoring topic at 0%" — a score the learner never earned.
    """
    if not topics:
        count, minutes = _sizing("QUICK_REVIEW", [])
        return FocusRecommendation(
            topic_id=None,
            topic_title=None,
            mastery_percent=None,
            band=None,
            reason_code="NO_TOPICS",
            reason="Extract topics from your material to unlock practice.",
            recommended_mode="QUICK_REVIEW",
            recommended_question_count=count,
            estimated_minutes=minutes,
        )

    # `{}` is a loaded-and-empty mapping, which is information; `None` means the
    # counts were never loaded. Collapsing the two is what made a preparation with
    # no questions at all report a score its learner never earned.
    counts_known = answered_by_topic is not None
    answered = answered_by_topic or {}
    ordered = sorted(
        topics,
        key=lambda topic: (
            getattr(topic, "mastery_score", 0.0) or 0.0,
            getattr(topic, "order_index", 0),
        ),
    )

    # An unpractised topic outranks a weak-but-measured one. Mastery 0 on a topic
    # nobody has been asked about is an absence of evidence, not a bad result, and
    # the useful next step is to get a first reading.
    unpractised = [topic for topic in ordered if answered.get(topic.id, 0) == 0]
    weakest = ordered[0]
    mastery = float(getattr(weakest, "mastery_score", 0.0) or 0.0)

    if unpractised and counts_known:
        topic = unpractised[0]
        drill_count, drill_minutes = _sizing("TOPIC_FOCUS", topics)
        return FocusRecommendation(
            topic_id=topic.id,
            topic_title=topic.title,
            mastery_percent=round(float(getattr(topic, "mastery_score", 0.0) or 0.0), 1),
            band=prep_readiness.mastery_band(getattr(topic, "mastery_score", 0.0)),
            reason_code="NEVER_PRACTISED",
            reason=f"You have not answered any questions on {topic.title} yet.",
            recommended_mode="TOPIC_FOCUS",
            recommended_question_count=drill_count,
            estimated_minutes=drill_minutes,
        )

    band = prep_readiness.mastery_band(mastery)

    if band == "strong":
        # Every topic is at or above the strong boundary. There is no weak area to
        # point at, so the honest recommendation is to keep it that way rather than
        # to manufacture a weakness out of the lowest of several good scores.
        review_count, review_minutes = _sizing("QUICK_REVIEW", topics)
        return FocusRecommendation(
            topic_id=weakest.id,
            topic_title=weakest.title,
            mastery_percent=round(mastery, 1),
            band=band,
            reason_code="MAINTENANCE",
            reason="Every topic is above target. A short mixed set keeps it there.",
            recommended_mode="QUICK_REVIEW",
            recommended_question_count=review_count,
            estimated_minutes=review_minutes,
        )

    # WEAK_AREAS rather than TOPIC_FOCUS: below the focus boundary the neighbouring
    # topics are usually weak too, and a set drawn across them spends the session
    # where it is worth most.
    mode = "WEAK_AREAS" if band == "focus" else "TOPIC_FOCUS"
    count, minutes = _sizing(mode, topics)
    return FocusRecommendation(
        topic_id=weakest.id,
        topic_title=weakest.title,
        mastery_percent=round(mastery, 1),
        band=band,
        reason_code="LOWEST_MASTERY",
        reason=(f"{weakest.title} is your lowest-scoring topic at {round(mastery)}%."),
        recommended_mode=mode,
        recommended_question_count=count,
        estimated_minutes=minutes,
    )
