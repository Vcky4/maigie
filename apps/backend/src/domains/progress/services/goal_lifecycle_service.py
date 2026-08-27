"""The nightly pass that keeps a goal from quietly going overdue.

**Nothing ever looked.** `is_at_risk`, `is_due_soon` and `is_overdue` were all written, all pure, and all
read only when a learner opened a page. So a goal drifted past its deadline and the only thing that
noticed was a label on a screen nobody was looking at. The learner who most needed telling was the one
least likely to be there to see it.

This walks the goals whose deadline is near or past and takes **exactly one action per goal**, from a
ladder, with a cooldown. The restraint is the design. An escalation that can fire twice on the same goal
in the same week is not an escalation, it is a notification loop, and the learner's remedy for that is to
turn all of them off.

## What it will not do

**It never creates a goal.** `progress_repo.delete_goal` is a hard `DELETE`, so a sweep that created
goals for intent that has none would resurrect a goal the learner deliberately threw away, every night,
with no way for them to make it stop. `goal_derivation_service`'s docstring records that decision and this
module is bound by it: it only ever adjusts goals that already exist.

**It never moves an external deadline.** An exam is on the 15th. For a goal whose date came from a
preparation the only truthful actions are to compress the effort or to say something, and once the date
has passed, to ask how it went — which is `prep_outcome_service`'s job, already built, and this defers to
it rather than asking a second time in different words.

**It does not compress plans.** The plan this implements lists "at risk, deadline far → compress the plan"
as a rung, and that shipped separately as `study_plan_service.redistribute_drifted_plans`, triggered by
actual item-level drift rather than by a goal's derived progress lagging. Triggering it from here as well
would bypass that sweep's own cooldown and reintroduce the nightly churn it exists to prevent, so a goal
at risk with a distant deadline gets no action here. The more direct signal already has an owner.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from src.shared.time.stored_instants import ensure_utc_optional

from ..repository import progress_repo
from . import goal_metrics, goal_schedule_log

logger = logging.getLogger(__name__)

#: How long a goal is left alone after the ladder acts on it, in days.
#:
#: Seven, matching `retention_service.INTERVENTION_COOLDOWN_DAYS`. A goal extended last night is not
#: extended again tonight, and a learner warned on Monday is not warned again on Tuesday. The floor is set
#: by what the actions are: both of them are a message to a person, and the interval that makes a message
#: useful is measured in days rather than hours.
GOAL_ACTION_COOLDOWN_DAYS = 7

#: How many times the system may extend one goal's deadline before it asks instead.
#:
#: Three. An extension here is sized from the learner's own measured rate, so it is a date they were on
#: pace to meet at the moment it was set. Missing three consecutive achievable dates is evidence about the
#: goal, not about the arithmetic — either it is not being worked on or it is not wanted, and both of those
#: are questions rather than another fortnight. Without a cap this becomes a goal that cannot fail: every
#: deadline it misses buys it a new one, forever.
#:
#: Counts only the system's own extensions. A learner moving their own deadline is them stating a new
#: intention, and spending their budget on it would mean refusing to help someone for having re-planned.
MAX_SYSTEM_EXTENSIONS = 3

#: How many days of recorded progress an extension is sized from.
#:
#: Fourteen: long enough that one good or bad day does not set the rate, short enough that the rate
#: describes what the learner is doing now rather than what they were doing last month.
RATE_WINDOW_DAYS = 14


async def review_goals(*, now: datetime | None = None, limit: int = 500) -> dict[str, int]:
    """Take at most one action on each goal whose deadline is near or past. Returns action counts.

    One pass, one action per goal, and the ladder is ordered so the first matching rung wins. Ordering
    matters more than it looks: a goal past its deadline is also at risk and also not due soon, so a
    ladder that tested conditions in the wrong order would warn about a deadline it should be asking
    about.

    Every action is recorded in `GoalLifecycleAction` **before** anything is sent, and regardless of
    whether what was sent survived. A notification can be deferred by the learner's daily allowance, held
    until their quiet hours end, or expire before it is delivered, so counting messages that reached
    someone would re-escalate the same goal every night — the trap the preparation ask and the weekly
    check-in each had to close.

    One goal's failure does not end the run.
    """
    moment = ensure_utc_optional(now) or datetime.now(UTC)
    goals = await progress_repo.list_goals_for_lifecycle_review(
        now=moment,
        # The two rungs that act both need the deadline near or past, so this is the honest bound on
        # what is worth measuring. `DUE_SOON_DAYS` rather than a new number, because a goal is "due
        # soon" on every other surface at exactly this distance.
        horizon=moment + timedelta(days=goal_metrics.DUE_SOON_DAYS),
        not_acted_since=moment - timedelta(days=GOAL_ACTION_COOLDOWN_DAYS),
        limit=limit,
    )
    if not goals:
        return {}

    measurements = await goal_metrics.derive_current_values(goals, now=moment)
    history = await goal_metrics.derive_schedule_history([goal.id for goal in goals])

    counts: dict[str, int] = {}
    for goal in goals:
        try:
            action = await _act_on(
                goal,
                progress=goal_metrics.derived_progress(goal, measurements.get(goal.id)),
                history=history.get(goal.id),
                now=moment,
            )
            if action:
                counts[action] = counts.get(action, 0) + 1
        except Exception:
            logger.exception(
                "Goal lifecycle review failed for one goal",
                extra={"goal_id": goal.id, "user_id": goal.user_id},
            )

    return counts


async def _act_on(
    goal: Any,
    *,
    progress: float,
    history: goal_metrics.GoalScheduleHistory | None,
    now: datetime,
) -> str | None:
    """Choose and take the one action this goal has earned. Returns the action, or `None` for nothing."""
    authority = goal_metrics.date_authority(goal)
    overdue = goal_metrics.is_overdue(
        status=goal.status, progress=progress, target_date=goal.target_date, now=now
    )

    if overdue:
        if authority == "external":
            # An exam that has been sat is not a goal that needs escalating, it is a goal waiting on an
            # answer — and `mark_preparations_awaiting_review` has already asked for it. Asking again
            # here, in different words, from a different surface, about the same exam, would read as the
            # system not knowing what it had already said.
            return None
        return await _extend_or_ask(goal, progress=progress, history=history, now=now, trigger="deadline_passed")

    at_risk = goal_metrics.is_at_risk(
        progress=progress, created_at=goal.created_at, target_date=goal.target_date, now=now
    )
    due_soon = goal_metrics.is_due_soon(
        status=goal.status, progress=progress, target_date=goal.target_date, now=now
    )
    if not (at_risk and due_soon):
        # Either the goal is fine, or it is behind with time still on the clock. The second case is
        # answered by compressing the plan, which `redistribute_drifted_plans` owns.
        return None

    if authority == "external":
        return await _warn(goal, progress=progress, now=now)
    return await _extend_or_ask(
        goal, progress=progress, history=history, now=now, trigger="at_risk_due_soon"
    )


async def _extend_or_ask(
    goal: Any,
    *,
    progress: float,
    history: goal_metrics.GoalScheduleHistory | None,
    now: datetime,
    trigger: str,
) -> str:
    """Move the deadline by a measured amount, or ask whether the goal is still wanted.

    Two ways to end up asking rather than extending, and they are different failures. The budget being
    spent means the learner has now missed three dates they were on pace for. The rate being unmeasurable
    means there is no evidence of progress to extrapolate from — a goal at 0% for a fortnight has no
    observed rate, and dividing the remaining work by nothing does not produce a deadline. In both cases
    inventing a date would be the system making up a commitment on the learner's behalf.
    """
    spent = (history.system_extended_count if history else 0) >= MAX_SYSTEM_EXTENSIONS
    days = None if spent else await _measured_extension_days(
        goal, progress=progress, history=history, now=now
    )
    if days is None:
        return await _ask_to_confirm(goal, progress=progress, history=history, now=now, trigger=trigger)

    new_date = now + timedelta(days=days)
    await progress_repo.update_goal(goal.id, {"targetDate": new_date})
    # The audit trail, which is what `extendedCount` publishes. A deadline the system moved without
    # recording it is one that `elapsed_percent` then treats as a larger window the goal always had.
    await goal_schedule_log.record_date_change(
        goal=goal, new_date=new_date, reason="system_extended"
    )
    await _record(goal, action="extended", trigger=trigger)
    await _notify(
        goal,
        type="goal_deadline_extended",
        title=f"More time: {goal.title}",
        body=(
            f"You're {progress:.0f}% of the way there. I've moved the deadline to "
            f"{new_date:%-d %B} to match the pace you've been keeping."
        ),
    )
    return "extended"


async def _ask_to_confirm(
    goal: Any,
    *,
    progress: float,
    history: goal_metrics.GoalScheduleHistory | None,
    now: datetime,
    trigger: str,
) -> str:
    moved = history.extended_count if history else 0
    await _record(goal, action="asked_to_confirm", trigger=trigger)
    await _notify(
        goal,
        type="goal_needs_decision",
        title=f"Still going for this? {goal.title}",
        body=(
            f"You're at {progress:.0f}% and this deadline has moved {moved} time"
            f"{'s' if moved != 1 else ''}. Do you want to keep going, or set it aside?"
        ),
    )
    return "asked_to_confirm"


async def _warn(goal: Any, *, progress: float, now: datetime) -> str:
    """Say the real numbers, and change nothing.

    The only action available on a deadline the learner does not own. The numbers are the message: "your
    exam is in three days and you are 30% ready" is something a learner can act on, where "keep it up"
    is not.
    """
    target = ensure_utc_optional(goal.target_date)
    days_left = max(0, (target - now).days) if target else 0
    await _record(goal, action="warned", trigger="at_risk_due_soon")
    await _notify(
        goal,
        # The one message on this path whose value expires with the deadline it describes. Everything else
        # here loses nothing by waiting for tomorrow, so it takes its turn behind the learner's daily
        # allowance; a warning that an immovable date is days away does not get that luxury.
        time_critical=True,
        type="goal_at_risk",
        title=f"{days_left} day{'s' if days_left != 1 else ''} left: {goal.title}",
        body=(
            f"You're at {progress:.0f}% with {days_left} day"
            f"{'s' if days_left != 1 else ''} to go. This date can't move, so it's worth deciding "
            f"what to focus on."
        ),
    )
    return "warned"


async def _record(goal: Any, *, action: str, trigger: str) -> None:
    """Write the decision. Not wrapped: if this fails the action must fail with it.

    The opposite call from `goal_schedule_log`, which swallows its own failures because a missing audit
    row must not reject a learner's edit. Here the row *is* the cooldown, so an action taken without one
    is an action that repeats tomorrow, and the next night, indefinitely. Better to lose one night's
    escalation than to start a loop.
    """
    await progress_repo.create_lifecycle_action(
        {"goalId": goal.id, "userId": goal.user_id, "action": action, "trigger": trigger}
    )


async def _notify(
    goal: Any, *, type: str, title: str, body: str, time_critical: bool = False
) -> None:
    """Send the message, and do not let its failure undo the action.

    Imported inside the function: `personal_learning` already imports this package's `goal_metrics`, so a
    module-level import of its notification service would close a cycle.

    Priority 3 rather than the default 5. These are messages about a commitment the learner made and a
    date that is days away or already passed, which is more urgent than a recommendation and less urgent
    than a security notice.

    Delivery is still not guaranteed: quiet hours hold a message until the learner's morning, and one held
    too long expires rather than arriving stale. It is no longer *destroyed* by the daily allowance, which
    it was when this was written. `goal_at_risk` sets `PRIORITY_TIME_CRITICAL` instead of 3 for that reason
    — a warning about a date that cannot move is the one message here whose value expires with the deadline
    it describes, so it outranks the allowance. The action is recorded either way.
    """
    from src.domains.personal_learning.services import notification_service

    try:
        await notification_service.create_notification(
            user_id=goal.user_id,
            type=type,
            title=title,
            body=body,
            priority=(
                notification_service.PRIORITY_TIME_CRITICAL if time_critical else 3
            ),
            action_data={"goalId": goal.id, "route": "goal"},
        )
    except Exception:
        logger.exception(
            "Could not notify about a goal lifecycle action", extra={"goal_id": goal.id}
        )


async def _measured_extension_days(
    goal: Any,
    *,
    progress: float,
    history: goal_metrics.GoalScheduleHistory | None,
    now: datetime,
) -> int | None:
    """How many days to add, from this goal's own recorded progress. `None` when it cannot be measured.

    **Measured, not guessed.** A fixed "add two weeks" would be the system inventing a commitment; this
    divides the work remaining by the rate the learner has actually been managing, from
    `GoalProgressSnapshot` — the table that exists precisely because `Goal.progress` is overwritten in
    place and leaves no trail.

    `None` in every case where the arithmetic would be fiction: fewer than two recorded days, a window
    that collapses to a single day, no progress gained, or nothing left to do. The caller asks the
    learner instead. A goal sitting at 0% for a fortnight has no rate, and 0 is not a number you can
    divide by to get a deadline.

    Capped at the length of the goal's **original** window, so one extension can at most double the time
    the learner first thought this would take. Original rather than current, or three extensions would
    compound into a date years out — each one doubling a window the last one had already doubled. Which
    is also why the cap reads `original_target_date` from the schedule history rather than the column,
    since the column is the already-extended one.
    """
    from . import goal_snapshot_service

    remaining = 100.0 - progress
    if remaining <= 0:
        return None

    until = now.date()
    rows = await goal_snapshot_service.list_history(
        user_id=goal.user_id,
        goal_id=goal.id,
        since=until - timedelta(days=RATE_WINDOW_DAYS),
        until=until,
    )
    if len(rows) < 2:
        return None

    first, last = rows[0], rows[-1]
    span_days = (last.captured_on - first.captured_on).days
    if span_days <= 0:
        return None
    gained = (last.progress or 0.0) - (first.progress or 0.0)
    if gained <= 0:
        return None

    needed = ceil(remaining / (gained / span_days))

    created = ensure_utc_optional(goal.created_at)
    original = (
        (history.original_target_date if history else None)
        or ensure_utc_optional(goal.target_date)
    )
    original = ensure_utc_optional(original)
    cap = (original - created).days if created and original else 0
    return max(1, min(needed, max(1, cap)))
