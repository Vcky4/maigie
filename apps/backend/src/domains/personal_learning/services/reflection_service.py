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
from . import reflection_metrics, reflection_narrative

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


#: Metric fields rendered into the prompt, with the label and unit the model should reuse.
#: An explicit list rather than a dump of the whole object, so adding a metric does not
#: silently change what the model is told.
_PROMPT_FACTS: tuple[tuple[str, str, str], ...] = (
    ("focused_minutes", "Tracked focused minutes", "min"),
    ("active_days", "Days active in the period", ""),
    ("sessions_completed", "Sessions completed", ""),
    ("topics_studied", "Topics touched", ""),
    ("topics_mastered", "Topics completed (count)", ""),
    ("notes_created", "Notes written", ""),
    ("flashcards_reviewed", "Flashcards reviewed", ""),
    ("recall_percent", "Recall", "%"),
    ("quizzes_completed", "Quizzes completed", ""),
    ("accuracy_percent", "Quiz accuracy", "%"),
    ("mastery_gained_percent", "Course completion gained", "percentage points"),
    ("consistency_score", "Consistency score", "out of 100"),
    ("average_session_minutes", "Average session length", "min"),
    ("best_day", "Strongest day", ""),
    ("streak_current", "Current streak", "days"),
    ("streak_best", "Longest streak", "days"),
)


def _render_facts(metrics: models.ReflectionMetrics) -> str:
    """The measured facts, as lines the model may reuse and must not exceed.

    Null metrics are **omitted rather than rendered as unknown or zero**. A prompt that lists
    "Recall: not measured" invites the model to explain the gap, and a learner does not need a
    paragraph about a number we did not take. Omission also keeps the instruction below
    truthful: every line present is a fact.
    """
    lines: list[str] = []
    for attribute, label, unit in _PROMPT_FACTS:
        value = getattr(metrics, attribute)
        if value is None:
            continue
        suffix = f" {unit}" if unit and not unit.startswith("%") else unit
        lines.append(f"- {label}: {reflection_narrative.render_figure(value)}{suffix}")

    if metrics.new_topics_mastered:
        # A distinct label from the count above. Two lines both reading "Topics completed",
        # one a number and one a list, is an invitation to add them together.
        lines.append("- Names of topics completed: " + ", ".join(metrics.new_topics_mastered[:8]))
    if metrics.milestones_reached:
        lines.append("- Milestones reached: " + ", ".join(metrics.milestones_reached[:5]))

    return "\n".join(lines)


def _build_prompt(
    *,
    type_: models.ReflectionType,
    period_start: datetime,
    period_end: datetime,
    deep: bool,
    metrics: models.ReflectionMetrics,
) -> str:
    """A narration brief with the measurements supplied as facts.

    The model's job is wording. It receives the numbers and is told, in the strongest terms
    the format allows, not to produce any of its own — because the defect this replaced was
    exactly that: a prompt asking for counts, given no data to count.
    """
    facts = _render_facts(metrics)
    depth = (
        "Write with some depth: name a pattern in how the learner is working and what it "
        "suggests about where attention would pay off next.\n"
        if deep
        else "Keep it brief and plain.\n"
    )

    if facts:
        evidence = (
            "These are the learner's measured figures for the period. They are the only "
            "figures that exist:\n"
            f"{facts}\n\n"
            "Use them if they help. You may restate a figure exactly as given. You must not "
            "compute a new one, estimate, round differently, compare against a period you "
            "were not given, or mention any measurement absent from the list above.\n"
        )
    else:
        evidence = (
            "Nothing was measured for this learner in this period, so state no figures at "
            "all. Do not mention counts, minutes, percentages, streaks or scores, and do not "
            "invent examples of what they studied.\n"
        )

    return (
        f"Write an encouraging {type_.value} learning reflection for "
        f"{period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}.\n\n"
        f"{evidence}\n"
        f"{depth}\n"
        "Return a JSON object with exactly two keys:\n"
        '- "title": a short heading, at most eight words\n'
        '- "summary": two short paragraphs\n\n'
        "Return ONLY the JSON object."
    )


async def _compose_and_store(
    *,
    user_id: str,
    type_: models.ReflectionType,
    period_start: datetime,
    period_end: datetime,
) -> Any:
    """Measure a period, narrate it, and store the result.

    Takes the period rather than deriving it, which is what lets an existing reflection be
    regenerated against the window it already describes. Deriving it here would mean
    regeneration silently retargeted the current week and left the old row untouched.
    """
    from src.domains.personal_learning.services.llm_resilient import (
        generate_content_json,
    )

    from . import feature_tier_service, trial_service

    quality_tier = await feature_tier_service.get_quality_tier(user_id)
    deep = quality_tier == "plus"
    if deep:
        await trial_service.record_plus_feature_used(user_id, "reflection")

    # Measured first, and independently of the narrative. The ordering is the guarantee: the
    # metrics exist before the model is called, so nothing that happens to the model can
    # affect them.
    metrics = await reflection_metrics.compute_metrics(
        user_id=user_id, period_start=period_start, period_end=period_end
    )

    title = _fallback_title(type_)
    summary = _fallback_summary(type_)

    try:
        # `fallback={}` rather than `None`: passing None to this helper means "raise", and two
        # routes have already taken a 500 from that reading of it.
        data = await generate_content_json(
            _build_prompt(
                type_=type_,
                period_start=period_start,
                period_end=period_end,
                deep=deep,
                metrics=metrics,
            ),
            # This first call asks only for the title and summary, which every tier gets and which
            # the fallbacks above already cover. The narrative is a second, larger request made
            # below — split so that a failure of the long one still leaves a titled, summarised
            # reflection rather than losing both.
            # 2048, not the 800 this first used. `max_tokens` is a budget for the whole
            # generation, and the configured models spend most of it on reasoning tokens before
            # emitting anything — measured at ~0.8 characters of visible output per token of
            # budget. At 800 the reply truncated mid-sentence on four of thirteen reflections,
            # which `json.loads` then rejected, so the narrative silently fell back to the
            # placeholder. The previous implementation used 2000 for the same reason.
            max_tokens=2048,
            fallback={},
            user_id=user_id,
            operation="reflection_summary",
        )
        if isinstance(data, dict):
            title = (data.get("title") or title).strip()[:200]
            summary = (data.get("summary") or summary).strip()
    except Exception as e:
        # The reflection is still delivered, with its metrics intact and a plain summary. What
        # is *not* done here is substituting zeros for the metrics, which is what made the old
        # failure path indistinguishable from an inactive week.
        logger.warning("Reflection narrative generation failed for user %s: %s", user_id, e)

    narrative, recommendations = await _compose_narrative(
        user_id=user_id,
        type_=type_,
        period_start=period_start,
        period_end=period_end,
        deep=deep,
        metrics=metrics,
        summary=summary,
    )

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
            # `by_alias=True` because the repository mapper speaks wire names, and the column
            # stores exactly what the response publishes.
            "metrics": metrics.model_dump(by_alias=True),
            # `None` when the narrative could not be composed at all, which is a published, meaningful
            # state — not an empty object claiming prose exists and then saying nothing.
            "narrative": narrative.model_dump(by_alias=True) if narrative else None,
            "recommendations": [action.model_dump(by_alias=True) for action in recommendations],
        }
    )


async def _compose_narrative(
    *,
    user_id: str,
    type_: models.ReflectionType,
    period_start: datetime,
    period_end: datetime,
    deep: bool,
    metrics: models.ReflectionMetrics,
    summary: str,
) -> tuple[models.ReflectionNarrative | None, list[models.ReflectionAction]]:
    """Build the narrative and the recommendations, measured parts first.

    Returns `(None, [])` only if even the measured skeleton could not be read, which means the daily
    snapshot and the subject query both failed. A narrative that exists is therefore always at least
    as truthful as the numbers behind it.

    **The measured skeleton is assembled before the model is called, and the model is handed it.** It
    receives each signal and subject with the figure already attached and is asked for a sentence
    about it (Decision A). It is never asked what the figure is.

    **The service picks every action target** (Decision O). A model free to emit an `entityId` would
    eventually cite an entity the learner does not own.
    """
    from src.domains.intelligence.reasoning.llm import THINKING_DYNAMIC
    from src.domains.personal_learning.services.llm_resilient import (
        generate_content_json,
    )

    from . import growth_service, reflection_narrative

    try:
        subjects_response = await growth_service.get_subjects(user_id=user_id, range_="30d")
        subjects_source = subjects_response.items
        snapshots = await repo.list_daily_snapshots(
            user_id, since=period_start.date(), until=period_end.date()
        )
    except Exception as exc:
        logger.warning("Reflection narrative skeleton unavailable for user %s: %s", user_id, exc)
        return None, []

    signals = reflection_narrative.build_signals(metrics)
    subjects = reflection_narrative.build_subjects(subjects_source)
    rhythm = reflection_narrative.build_rhythm(snapshots)
    highlights = reflection_metrics.build_highlights(metrics)
    chosen = reflection_narrative.choose_actions(
        metrics=metrics,
        subjects=subjects_source,
        limit=reflection_narrative.recommendation_limit(deep=deep),
    )

    written: dict[str, Any] = {}
    # The skeleton every tier gets is entirely measured (Decision T2); this call buys the prose on top
    # of it.
    #
    # **The comment here used to claim free spends no call, and that is not what this condition
    # does.** `_FREE_RECOMMENDATIONS = 1`, so a free learner with any actionable metric has a
    # non-empty `chosen` and the call happens — their one recommendation needs wording. The call is
    # only skipped when there is nothing to say *and* nothing to recommend, which is a narrower case
    # than "free". Corrected rather than changed: Decision M wants the free weekly reflection composed
    # with no model call at all, and that needs a deterministic composer this module does not have —
    # `_fallback_summary` is an error message ("the narrative could not be generated this time"), not
    # a summary, so it cannot serve as the free version without telling every free learner their
    # reflection failed. Recorded as the open half of Decision M in Phase 3c.
    if deep or chosen:
        try:
            reply = await generate_content_json(
                reflection_narrative.build_prompt(
                    type_=type_,
                    period_start=period_start,
                    period_end=period_end,
                    facts=_render_facts(metrics),
                    signals=signals,
                    subjects=subjects,
                    actions=chosen,
                ),
                # Larger than the summary call's budget because this reply carries a paragraph per
                # signal and per subject. The same reasoning applies as there: most of the budget is
                # spent on reasoning tokens before any output appears, and a truncated reply is a
                # `JSONDecodeError` that silently costs the whole narrative.
                #
                # 8192, not the 4096 this first used. A deep reply for a learner with five measured
                # signals and two subjects was observed truncating at 4096 — `_repair_json` closed
                # the JSON, which salvaged the earlier fields and left `closing` cut mid-sentence.
                # `assemble` now refuses a half-sentence, but a budget that lets the reply finish is
                # the better half of the fix; refusing it only stops the damage showing.
                max_tokens=8192,
                # Explicitly dynamic rather than left to the provider default, so this reads as a
                # decision beside the narrative panels that are now `THINKING_BOUNDED`. This is the
                # one narrative whose length genuinely scales with the learner — a paragraph per
                # signal and per subject — so it is the one that should be allowed to think.
                thinking=THINKING_DYNAMIC,
                fallback={},
                user_id=user_id,
                # Deliberately below the split, despite being the largest budget in the file and the
                # only `THINKING_DYNAMIC` call. The deep narrative is already a Plus-gated *feature* —
                # a free learner does not reach this branch at all, since `deep or chosen` is the gate
                # — so a tier split inside it would decide between Plus and Plus. Labelling it as
                # expensive would put a member in `QUALITY_SPLIT_OPERATIONS` that can only ever
                # resolve one way, which reads as coverage and is really a no-op.
                operation="reflection_narrative",
            )
            if isinstance(reply, dict):
                written = reply
        except Exception as exc:
            # The measured skeleton survives. This is the difference between a reflection with real
            # numbers and no prose, and no reflection at all.
            logger.warning("Reflection narrative wording failed for user %s: %s", user_id, exc)

    return (
        reflection_narrative.assemble(
            deep=deep,
            summary=summary,
            written=written,
            signals=signals,
            subjects=subjects,
            rhythm=rhythm,
            highlights=highlights,
        ),
        reflection_narrative.assemble_actions(chosen=chosen, written=written),
    )


async def _ensure_cadence_allowed(*, user_id: str, type_: models.ReflectionType) -> None:
    """Monthly reflections are Plus (Decision T). Weekly is never gated.

    **A `403` here, unlike the locked trend range's `200`.** The difference is that this is a
    mutation the learner explicitly asked for: refusing it with a typed upgrade payload is
    actionable, and there is no chart to leave looking broken. A locked *read* has to answer `200`
    because the design renders the control and a Free learner must be able to press it.
    """
    if type_ is not models.ReflectionType.MONTHLY:
        return

    from fastapi import HTTPException

    from . import feature_tier_service

    # `get_quality_tier`, the same accessor `_compose_and_store` uses, rather than
    # `get_effective_tier`. One way of asking "which tier is this learner on" across the module.
    if await feature_tier_service.get_quality_tier(user_id) == "plus":
        return

    matrix = feature_tier_service.FEATURE_TIER_MATRIX.get("reflection", {})
    detail = models.UpgradeRequiredDetail(
        upgrade_required=True,
        reason="Monthly reflections require Maigie Plus",
        capability="reflection",
        upgrade_url="/subscription",
        trial_available=await feature_tier_service.trial_available(user_id),
        upgrade_value=matrix.get("upgrade_value", ""),
    )
    raise HTTPException(status_code=403, detail=detail.model_dump(by_alias=True))


async def generate_reflection(*, user_id: str, type: str | models.ReflectionType) -> Any:
    """Generate and persist a reflection for the period ending now.

    Idempotent on ``(userId, type, periodStart)``: regenerating a period updates the row rather
    than adding a second one for the same week.

    FREE: a standard-depth narrative. PLUS: a deeper one. Neither tier gets metrics from the
    model, because no tier should be sold invented numbers.
    """
    type_ = models.ReflectionType(type.lower() if isinstance(type, str) else type.value)
    await _ensure_cadence_allowed(user_id=user_id, type_=type_)
    now = datetime.now(UTC)
    return await _compose_and_store(
        user_id=user_id,
        type_=type_,
        period_start=now - timedelta(days=_PERIOD_DAYS[type_]),
        period_end=now,
    )


async def regenerate_reflection(*, user_id: str, reflection_id: str) -> Any:
    """Re-measure and re-narrate an existing reflection over **its own** period.

    Exists because the reflections written before metrics were measured cannot be repaired by
    calling `generate_reflection`: that targets the week ending now, so it would leave the old
    row untouched and add a new one beside it.

    The upsert key is `(userId, type, periodStart)` and all three are taken from the existing
    row, so this updates that row in place rather than creating a sibling.
    """
    existing = await repo.get_reflection(reflection_id, user_id)
    if existing is None:
        raise NotFoundError("Reflection", reflection_id)

    return await _compose_and_store(
        user_id=user_id,
        type_=models.ReflectionType(existing.type),
        period_start=existing.period_start,
        period_end=existing.period_end,
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


# ---------------------------------------------------------------------------
# Reflection notes
# ---------------------------------------------------------------------------
#
# Learner-authored, and kept apart from generated reflections on purpose (Decision F). Every
# function here is ownership-scoped in its query rather than fetching and then checking, so a
# note belonging to someone else is indistinguishable from one that does not exist — which is
# what stops an id being probed.


async def create_reflection_note(*, user_id: str, body: str, prompt_used: str | None = None) -> Any:
    """Store a note the learner wrote."""
    return await repo.create_reflection_note(user_id=user_id, body=body, prompt_used=prompt_used)


async def list_reflection_notes(
    *, user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list[Any], int]:
    """A page of the learner's notes, newest first, with the total for the envelope."""
    return await repo.list_reflection_notes(user_id, skip=(page - 1) * page_size, take=page_size)


async def get_reflection_note(*, user_id: str, note_id: str) -> Any:
    """One of the learner's notes."""
    note = await repo.find_reflection_note(note_id, user_id)
    if note is None:
        raise NotFoundError("ReflectionNote", note_id)
    return note


async def update_reflection_note(*, user_id: str, note_id: str, body: str) -> Any:
    """Edit the text of a note."""
    note = await repo.update_reflection_note(note_id, user_id, body=body)
    if note is None:
        raise NotFoundError("ReflectionNote", note_id)
    return note


async def delete_reflection_note(*, user_id: str, note_id: str) -> None:
    """Delete a note."""
    if not await repo.delete_reflection_note(note_id, user_id):
        raise NotFoundError("ReflectionNote", note_id)
