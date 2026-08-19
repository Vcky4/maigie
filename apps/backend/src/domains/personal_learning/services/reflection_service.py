"""
Reflection service — period summaries of a learner's progress.

Presented with the Three Layer Model (Activities -> Progress -> Achievements), which is a
grouping the client applies to `ReflectionMetrics`; it is not how the data is stored.

**The model narrates. It never supplies a number.**

That rule is the whole point of this module, and it was written the other way round. The
previous prompt asked the model for `topics_studied`, `sessions_completed`, `notes_created`,
`total_minutes`, `concepts_mastered`, `retention_score`, `streak_days` and `milestones`,
while the only context it received was the behaviour profile — purpose, consistency score,
average session minutes, maturity days. No session, note, topic, flashcard, quiz or
achievement row was ever read. The model was inventing counts for data it had never seen,
and `create_reflection` persisted them next to a real `periodStart` as though they had been
measured. On failure it wrote hardcoded zeros, so a broken generation and a genuinely
inactive week produced identical rows.

This stage removes the fabrication without yet replacing it: `metrics` is written all-null,
which is honest about knowing nothing, and the aggregate queries that fill it arrive next.
An all-null metrics object is a worse *product* than invented numbers and a much better
*record*, and only one of those is recoverable.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.exceptions import NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)

#: How far back each reflection type looks.
_PERIOD_DAYS: dict[models.ReflectionType, int] = {
    models.ReflectionType.WEEKLY: 7,
    models.ReflectionType.MONTHLY: 30,
}


def _fallback_title(type_: models.ReflectionType) -> str:
    return (
        "Your week in review" if type_ is models.ReflectionType.WEEKLY else "Your month in review"
    )


def _fallback_summary(type_: models.ReflectionType) -> str:
    return (
        f"Your {type_.value} reflection is ready. "
        "The narrative could not be generated this time, so this is the short version."
    )


def _build_prompt(
    *, type_: models.ReflectionType, period_start: datetime, period_end: datetime, deep: bool
) -> str:
    """A narration brief. Note what it does not ask for: any count, score or total.

    It cannot ask for one honestly, because this stage has no measurements to hand it. Once
    `reflection_metrics` lands, the numbers go *into* this prompt as facts and the model's job
    stays exactly what it is here — wording.
    """
    depth = (
        "Write with some depth: name a pattern in how the learner is working, and what it "
        "suggests about where attention would pay off next.\n"
        if deep
        else "Keep it brief and plain.\n"
    )
    return (
        f"Write an encouraging {type_.value} learning reflection for the period "
        f"{period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}.\n\n"
        f"{depth}\n"
        "You have no statistics about this learner, so do not state or imply any. Do not "
        "mention counts, minutes, percentages, streaks or scores, and do not invent examples "
        "of what they studied. Write about the value of returning to the work and of "
        "reviewing it deliberately.\n\n"
        "Return a JSON object with exactly two keys:\n"
        '- "title": a short heading, at most eight words\n'
        '- "summary": two short paragraphs\n\n'
        "Return ONLY the JSON object."
    )


async def generate_reflection(*, user_id: str, type: str | models.ReflectionType) -> Any:
    """Generate and persist a reflection for the current period.

    Idempotent on ``(userId, type, periodStart)``: regenerating a period updates the row
    rather than adding a second one for the same week.

    FREE: a standard-depth narrative. PLUS: a deeper one. Neither tier gets metrics from the
    model, because no tier should be sold invented numbers.
    """
    from src.domains.personal_learning.services.llm_resilient import generate_content_json

    from . import feature_tier_service, trial_service

    type_ = models.ReflectionType(type.lower() if isinstance(type, str) else type.value)

    now = datetime.now(UTC)
    period_start = now - timedelta(days=_PERIOD_DAYS[type_])
    period_end = now

    quality_tier = await feature_tier_service.get_quality_tier(user_id)
    deep = quality_tier == "plus"
    if deep:
        await trial_service.record_plus_feature_used(user_id, "reflection")

    title = _fallback_title(type_)
    summary = _fallback_summary(type_)

    try:
        # `fallback={}` rather than `None`: passing None to this helper means "raise", and two
        # routes have already taken a 500 from that reading of it.
        data = await generate_content_json(
            _build_prompt(type_=type_, period_start=period_start, period_end=period_end, deep=deep),
            max_tokens=800,
            fallback={},
            user_id=user_id,
        )
        if isinstance(data, dict):
            title = (data.get("title") or title).strip()[:200]
            summary = (data.get("summary") or summary).strip()
    except Exception as e:
        # A reflection is still delivered without its narrative. What is *not* done here is
        # substituting zeros for the metrics, which is what made the old failure path
        # indistinguishable from an inactive week.
        logger.warning("Reflection narrative generation failed for user %s: %s", user_id, e)

    return await repo.upsert_reflection(
        {
            "userId": user_id,
            "type": type_.value,
            "periodStart": period_start,
            "periodEnd": period_end,
            "title": title,
            "summary": summary,
            "depth": (
                models.ReflectionDepth.DEEP.value if deep else models.ReflectionDepth.STANDARD.value
            ),
            # All-null until the aggregate queries land. Written as an empty object rather
            # than omitted so the column is never NULL and no reader has to coerce it.
            "metrics": models.ReflectionMetrics().model_dump(by_alias=True),
            # Empty rather than model-authored: an action carries a navigation target, and
            # the service picks that from measurements it does not yet have.
            "recommendations": [],
        }
    )


async def list_reflections(
    *,
    user_id: str,
    type_filter: str | None = None,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """A page of past reflections, sorted by period."""
    skip = (page - 1) * page_size
    return await repo.list_reflections(
        user_id,
        type_filter=type_filter,
        period_from=period_from,
        period_to=period_to,
        sort=sort,
        skip=skip,
        take=page_size,
    )


async def get_reflection(*, user_id: str, reflection_id: str) -> Any:
    """Get a single reflection."""
    reflection = await repo.get_reflection(reflection_id, user_id)
    if reflection is None:
        raise NotFoundError("Reflection", reflection_id)
    return reflection


async def update_reflection(*, user_id: str, reflection_id: str, data: dict[str, Any]) -> Any:
    """Rename a reflection or correct its summary."""
    reflection = await repo.update_reflection(reflection_id, user_id, data)
    if reflection is None:
        raise NotFoundError("Reflection", reflection_id)
    return reflection


async def delete_reflection(*, user_id: str, reflection_id: str) -> None:
    """Delete the learner's reflection."""
    if not await repo.delete_reflection(reflection_id, user_id):
        raise NotFoundError("Reflection", reflection_id)


async def mark_reflection_read(*, user_id: str, reflection_id: str) -> Any:
    """Record that the learner opened this reflection.

    An explicit call rather than a side effect of the GET. A read that mutates is not
    idempotent, defeats caching, and would count a dashboard prefetch as engagement — which
    matters because this timestamp is what the reflection streak counts.
    """
    reflection = await repo.mark_reflection_opened(
        reflection_id, user_id, opened_at=datetime.now(UTC)
    )
    if reflection is None:
        raise NotFoundError("Reflection", reflection_id)
    return reflection
