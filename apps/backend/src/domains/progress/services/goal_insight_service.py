"""One goal's written interpretation and recommended next action.

Follows the rule the reflection composer established and the growth composer repeated: **every figure
is measured here and every action target is chosen here; the model is asked only for wording**
(Decisions A and O). A model free to name an entity would eventually cite one the learner does not own,
which is an authorisation bug dressed as a recommendation.

Plus (Decision Z), delivered as a `200` carrying a `LockedNoticeResponse` — never a `403`. Every number
on the goal detail page is free and only the interpretation is paid, so a Free page is shorter rather
than holed.

Stored by `narrative_cache` against a fingerprint of the skeleton below, so opening a goal twice does
not spend two generations. The prose is therefore always beside the figures it was written from: a moved
figure is a cache miss, and an unmoved one has no new sentence owing to it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import models
from . import goal_metrics

logger = logging.getLogger(__name__)

#: Pace at or above which a goal is called ahead of schedule. `pacePercent` is progress as a share of
#: where the schedule says it should be, so 100 is exactly on pace.
_AHEAD_PACE = 110.0
#: Below this, the goal is behind. The band between is "on track", wide enough that a goal does not
#: change badge because a single day passed.
_ON_TRACK_PACE = 90.0

#: Weeks of plan the insight is written against. Four, matching the momentum panel's default, so the
#: sentence describes the same weeks the chart beside it draws.
_MOMENTUM_WEEKS = 4


def derive_signal(*, status_label: str, pace_percent: float | None) -> models.GoalInsightSignal:
    """How the goal is travelling, from figures `GoalResponse` already publishes.

    Derived from `statusLabel` and `pacePercent` rather than recomputed, so the badge beside the insight
    and the badge on the goal card are the same claim.

    `not_paced` for a goal with no target date: it has no schedule to be ahead or behind of, and
    `pacePercent` is `null` for exactly that reason. Calling it "on track" would be an unmeasured claim
    about a goal that cannot be off track.
    """
    if status_label == "COMPLETED":
        return "achieved"
    if pace_percent is None:
        return "not_paced"
    if pace_percent >= _AHEAD_PACE:
        return "ahead"
    if pace_percent >= _ON_TRACK_PACE:
        return "on_track"
    return "behind"


def choose_next_action(
    *,
    goal: Any,
    status_label: str,
    planned: int,
    completed: int,
    completion_tracked: bool,
) -> tuple[str, str, models.GoalNextActionTarget]:
    """`(reason, label, target)` for the recommended next move on this goal.

    A ladder over what was measured, ordered by what actually blocks the learner:

    1. **Achieved** — the next move is a new goal, not more work on a finished one.
    2. **Nothing planned** — the gap is the plan itself. Recommending study to someone with no
       scheduled sessions skips the thing that is missing.
    3. **The plan is running behind, and completion is genuinely being tracked** — open the schedule.
       The `completion_tracked` guard matters: `ScheduleBlock.completedAt` shipped in migration `046`
       and reads zero for every learner until they start marking blocks, so `completed < planned`
       without it would tell every learner in the database that they are behind on a plan nobody has
       ever recorded against (Decision Y).
    4. **Linked to a preparation** — focused practice on it, in weak-areas mode.
    5. **Linked to a course** — open the course.
    6. **Nothing to point at** — advice with no button. `ReflectionActionKind.NONE` is legitimate and
       the card renders without one; a button to the page the learner is already on would be worse.

    `reason` is the grounds. It goes into the prompt so the sentence argues for the button beneath it,
    and it is the fallback for `detail` when the model does not finish a sentence — plainer than the
    prose would have been, but a true statement about the goal.
    """
    if status_label == "COMPLETED":
        return (
            "this goal is complete",
            "Set your next goal",
            models.GoalNextActionTarget(kind="goal"),
        )

    if planned == 0:
        return (
            "no sessions are scheduled for this goal",
            "Plan your sessions",
            models.GoalNextActionTarget(kind="schedule"),
        )

    if completion_tracked and completed < planned:
        return (
            f"{completed} of {planned} scheduled sessions were marked done "
            f"in the last {_MOMENTUM_WEEKS} weeks",
            "Open your schedule",
            models.GoalNextActionTarget(kind="schedule"),
        )

    if getattr(goal, "prep_id", None):
        return (
            "this goal is measured by a preparation's readiness",
            "Start focused practice",
            models.GoalNextActionTarget(
                kind="preparation_practice", entityId=goal.prep_id, mode="weak"
            ),
        )

    if getattr(goal, "course_id", None):
        return (
            "this goal is measured by progress through a course",
            "Open the course",
            models.GoalNextActionTarget(kind="course", entityId=goal.course_id),
        )

    return (
        "this goal records its progress manually",
        "",
        models.GoalNextActionTarget(kind="none"),
    )


def build_skeleton(
    *,
    goal_response: models.GoalResponse,
    planned: int,
    completed: int,
    completion_tracked: bool,
    recorded_days: int,
    evidence_count: int,
) -> dict[str, Any]:
    """Every figure the insight is written from, and nothing else.

    Reads the assembled `GoalResponse` rather than the ORM row, so the prose is written from the same
    derived figures the page renders — `pacePercent`, `projectedOutcome` and `statusLabel` are all
    computed on the way out and recomputing them here would make their agreement a coincidence.

    `recordedDays` is the length of the goal's stored history. It is in the brief because it bounds what
    can honestly be said: a goal with two recorded days has no trajectory to describe, and the prompt
    says so rather than leaving the model to infer it from silence (Decision Y).
    """
    return {
        "title": goal_response.title,
        "status": goal_response.statusLabel,
        "progress": round(goal_response.progress, 1),
        "currentValue": goal_response.currentValue,
        "currentValueMeasured": goal_response.currentValueMeasured,
        "targetValue": goal_response.targetValue,
        "unit": goal_response.unit,
        "metricKind": goal_response.metricKind,
        "targetDate": goal_response.targetDate,
        "pacePercent": goal_response.pacePercent,
        "projectedOutcome": goal_response.projectedOutcome,
        "milestonesAchieved": goal_response.milestonesAchieved,
        "milestonesTotal": goal_response.milestonesTotal,
        "plannedSessions": planned,
        "completedSessions": completed,
        "completionTracked": completion_tracked,
        "recordedDays": recorded_days,
        "evidenceCount": evidence_count,
    }


def build_prompt(*, skeleton: dict[str, Any], signal: str, reason: str) -> str:
    """Ask for a heading, a sentence and a next step, with every figure already attached.

    The unmeasured figures are stated as unmeasured rather than omitted. A brief that simply lacks a
    pace reads as a brief that forgot one, and the model fills the silence; a brief that says "pace: not
    measured, this goal has no target date" does not.
    """
    from src.domains.personal_learning.services.reflection_narrative import render_figure

    facts = [
        f"goal: {skeleton['title']}",
        f"progress: {render_figure(skeleton['progress'])}%",
        f"standing: {skeleton['status']}",
    ]

    if skeleton["targetValue"] is not None:
        unit = f" {skeleton['unit']}" if skeleton.get("unit") else ""
        current = (
            render_figure(skeleton["currentValue"])
            if skeleton["currentValue"] is not None
            else "not measured"
        )
        facts.append(
            f"measured value: {current} of {render_figure(skeleton['targetValue'])}{unit}"
            + ("" if skeleton["currentValueMeasured"] else " (entered by the learner, not measured)")
        )

    if skeleton["targetDate"]:
        # The date only. `GoalResponse.targetDate` is a full `isoformat()` for the client, and a brief
        # containing `2026-03-31T23:59:59` produced live prose reading "your target date of
        # 2026-03-31T23:59:59" — a timestamp in a sentence a learner reads. `%Y-%m-%d` matches the
        # reflection prompt's period formatting, so the two surfaces date things the same way.
        facts.append(f"target date: {str(skeleton['targetDate'])[:10]}")
        if skeleton["pacePercent"] is not None:
            facts.append(
                f"pace: {render_figure(skeleton['pacePercent'])}% of where the schedule says "
                "this should be, where 100 is exactly on pace"
            )
        else:
            facts.append("pace: not measured yet, too early in the window to be meaningful")
        if skeleton["projectedOutcome"] is not None:
            facts.append(
                "straight-line projection by the target date: "
                f"{render_figure(skeleton['projectedOutcome'])}%"
            )
    else:
        facts.append("target date: none set, so this goal has no schedule to be ahead or behind of")

    if skeleton["milestonesTotal"]:
        facts.append(
            f"milestones: {skeleton['milestonesAchieved']} of "
            f"{skeleton['milestonesTotal']} achieved"
        )

    facts.append(
        f"scheduled sessions in the last {_MOMENTUM_WEEKS} weeks: {skeleton['plannedSessions']}"
    )
    if skeleton["completionTracked"]:
        facts.append(f"of those, marked done: {skeleton['completedSessions']}")
    else:
        facts.append(
            "session completion has never been recorded for this goal, so nothing can be said "
            "about whether the plan was followed"
        )

    facts.append(f"dated pieces of work found behind this goal: {skeleton['evidenceCount']}")
    if skeleton["recordedDays"] < 2:
        facts.append(
            "recorded history: fewer than two days, so there is no trajectory yet and no "
            "statement may be made about a trend"
        )
    else:
        facts.append(f"recorded history: {skeleton['recordedDays']} days")

    return (
        "A learner's progress towards one goal.\n\n"
        "MEASURED FACTS. These are the only figures that exist. You may restate any of them exactly "
        "as given. You must not compute a new figure, estimate, round differently, compare against a "
        "period you were not given, or mention any measurement absent from this brief:\n"
        + "\n".join(f"- {fact}" for fact in facts)
        + f"\n\nThe goal is travelling: {signal}.\n"
        f"The recommended next step has already been chosen, on these grounds: {reason}. "
        "Write the step so it argues for that.\n\n"
        "Address the learner as \"you\". Return a JSON object with exactly these keys:\n"
        '- "title": a heading of at most eight words naming what is true about this goal\n'
        '- "detail": one sentence of at most thirty-five words explaining why\n'
        '- "action": {"title", "detail"} — a heading of at most eight words for the recommended '
        "step, and one sentence of at most thirty words saying what to do\n\n"
        "Do not congratulate progress the figures do not show. Return ONLY the JSON object."
    )


def assemble(
    *,
    written: dict[str, Any],
    signal: models.GoalInsightSignal,
    label: str,
    target: models.GoalNextActionTarget,
    reason: str,
) -> tuple[models.GoalInsight | None, models.GoalNextAction | None]:
    """`(insight, nextAction)` from the wording, either of which may be absent.

    The heading is a phrase and is trimmed to a length; the sentence is a sentence and is dropped when
    it does not end on a terminator, because `generate_content_json` repairs truncated JSON without
    being able to tell that the last string it rescued stops mid-word. That is the same split
    `reflection_narrative.assemble` makes between `theme` and `closing`.

    **No heading, no panel.** The insight card is nothing but its interpretation — the figures beside it
    are already on the page — so an untitled one would be a signal badge over blank space.

    The action, in contrast, ships whenever it has a heading and falls back to the service's grounds for
    its sentence: the target is the part that must be right.
    """
    title = str(written.get("title") or "").strip()[:100]
    detail = _finished(written.get("detail"))
    insight = models.GoalInsight(title=title, detail=detail, signal=signal) if title else None

    action_prose = written.get("action")
    action_prose = action_prose if isinstance(action_prose, dict) else {}
    action_title = str(action_prose.get("title") or "").strip()[:100]
    next_action = (
        models.GoalNextAction(
            title=action_title,
            detail=_finished(action_prose.get("detail")) or reason,
            label=label,
            target=target,
        )
        if action_title
        else None
    )
    return insight, next_action


def _finished(value: Any) -> str | None:
    """Prose the model actually finished, or `None`. Shared guard, imported so there is one of it."""
    from src.domains.personal_learning.services.reflection_narrative import _finished_sentence

    return _finished_sentence(value)


async def get_goal_insight(
    *, user_id: str, goal: Any, goal_response: models.GoalResponse, now: datetime | None = None
) -> models.GoalInsightResponse:
    """The insight and next-action panels for one goal.

    Takes the goal and its assembled response rather than re-reading either: the route has already
    established ownership and already paid for the derivation, and reading it twice would let the prose
    describe a different `pacePercent` from the one printed beside it.

    Measured first, then worded — the ordering is the guarantee. Everything the panel rests on exists
    before a model is called, so nothing the model does can affect it.
    """
    from src.domains.personal_learning.services import narrative_cache

    locked = await narrative_cache.plus_gate(
        user_id, reason="Goal insight and recommendations require Maigie Plus"
    )
    if locked is not None:
        return models.GoalInsightResponse(
            goalId=goal.id,
            locked=models.LockedNoticeResponse(
                reason=locked.reason,
                capability=locked.capability,
                upgradeUrl=locked.upgrade_url,
                trialAvailable=locked.trial_available,
                upgradeValue=locked.upgrade_value,
            ),
        )

    planned, completed, tracked, recorded_days, evidence_count = await _measure(
        user_id=user_id, goal=goal, now=now
    )

    signal = derive_signal(
        status_label=goal_response.statusLabel, pace_percent=goal_response.pacePercent
    )
    reason, label, target = choose_next_action(
        goal=goal,
        status_label=goal_response.statusLabel,
        planned=planned,
        completed=completed,
        completion_tracked=tracked,
    )
    skeleton = build_skeleton(
        goal_response=goal_response,
        planned=planned,
        completed=completed,
        completion_tracked=tracked,
        recorded_days=recorded_days,
        evidence_count=evidence_count,
    )
    # The signal and the chosen step are part of the fingerprint, not only of the prompt. The prose
    # argues for both, so a goal that slipped from "on track" to "behind" needs a new sentence even
    # though every figure it is written from is already in the skeleton.
    inputs = {"goal": skeleton, "signal": signal, "step": {"reason": reason, "kind": target.kind}}

    async def _compose() -> dict[str, Any] | None:
        return await narrative_cache.compose_json(
            user_id=user_id,
            prompt=build_prompt(skeleton=skeleton, signal=signal, reason=reason),
            what="goal insight",
        )

    written = await narrative_cache.resolve(
        user_id=user_id,
        kind="goal_insight",
        entity_id=goal.id,
        inputs=inputs,
        compose=_compose,
    )
    insight, next_action = assemble(
        written=written, signal=signal, label=label, target=target, reason=reason
    )
    return models.GoalInsightResponse(
        goalId=goal.id, insight=insight, nextAction=next_action
    )


async def _measure(
    *, user_id: str, goal: Any, now: datetime | None = None
) -> tuple[int, int, bool, int, int]:
    """`(planned, completed, completionTracked, recordedDays, evidenceCount)`.

    Each read is guarded separately and degrades to its empty value. A goal's insight must not be lost
    because its evidence query failed — and the prompt states each figure's absence explicitly, so a
    degraded read produces a narrower sentence rather than an invented one.
    """
    from src.domains.personal_learning.services import reflect_aggregates

    from . import goal_snapshot_service

    planned = completed = 0
    tracked = False
    try:
        weeks = await goal_metrics.get_goal_momentum(
            user_id=user_id, goal_id=goal.id, weeks=_MOMENTUM_WEEKS, now=now or datetime.now(UTC)
        )
        planned = sum(week.planned for week in weeks)
        completed = sum(week.completed for week in weeks)
        tracked = await goal_metrics.completion_ever_recorded(user_id=user_id, goal_id=goal.id)
    except Exception as exc:
        logger.warning("Goal momentum unavailable for insight on %s: %s", goal.id, exc)

    recorded_days = 0
    try:
        # Ninety days, matching the longest range the rest of Reflect offers. The figure is only used
        # to bound what may be claimed about a trajectory, so a wider window would not change the
        # sentence and a narrower one could understate a goal's history.
        until = (now or datetime.now(UTC)).date()
        history = await goal_snapshot_service.list_history(
            user_id=user_id, goal_id=goal.id, since=until - timedelta(days=90), until=until
        )
        recorded_days = len(history)
    except Exception as exc:
        logger.warning("Goal history unavailable for insight on %s: %s", goal.id, exc)

    evidence_count = 0
    try:
        items = await reflect_aggregates.list_goal_evidence(user_id=user_id, goal=goal, limit=12)
        evidence_count = len(items)
    except Exception as exc:
        logger.warning("Goal evidence unavailable for insight on %s: %s", goal.id, exc)

    return planned, completed, tracked, recorded_days, evidence_count
