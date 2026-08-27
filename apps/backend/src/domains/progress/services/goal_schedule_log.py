"""Recording that a goal's deadline moved.

One function, in its own module, because **two unrelated code paths move a deadline** — a learner
editing the goal, and the AI plan regenerator recomputing it from a requested duration — and they must
agree on what counts as a change. Written twice, they would drift: one would record no-op saves, or
skip a deadline being cleared, and the count the learner sees would depend on which path moved it.

Why the count matters at all: `goal_metrics.elapsed_percent` measures a goal's window as
`createdAt → targetDate`, so pushing the deadline out enlarges the denominator, shrinks elapsed percent,
shrinks the lag `is_at_risk` tests, and **the goal reports itself healthy for having been given more
time**. Nothing else in the data distinguishes a goal that was always due in December from one that was
due in August and has been rewritten twice.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.shared.time.stored_instants import ensure_utc_optional

from ..repository import progress_repo
from . import goal_metrics

logger = logging.getLogger(__name__)


async def record_date_change(*, goal: Any, new_date: datetime | None, reason: str) -> None:
    """Log a goal's deadline moving from whatever `goal` currently holds to `new_date`.

    Call **after** the update has been committed, with the goal row as it was *before*. A change
    recorded before the write would claim a deadline moved when the write could still fail.

    A no-op is not recorded. Saving a goal without touching its deadline goes through the same update
    path as moving it, so logging every write would turn "this deadline has moved three times" into "this
    goal has been saved three times" — a number that looks like a warning and is not one.

    **Never raises.** A failure to write an audit row must not fail the learner's edit; the edit is the
    thing they asked for and the log is bookkeeping about it. The exception is logged with the goal id so
    a gap in the history is traceable rather than invisible.

    Args:
        goal: The goal row **before** the update, read for its current `target_date`, its owner, and the
            `prepId` that decides date authority.
        new_date: The deadline after the change. `None` is a deadline being cleared, which is recorded —
            it is an edit to the schedule, and it makes every pace figure on the goal go null.
        reason: One of `GoalScheduleChange.REASONS`. The database refuses anything else, which is the
            point of the constraint.
    """
    previous = ensure_utc_optional(getattr(goal, "target_date", None))
    incoming = ensure_utc_optional(new_date)
    if previous == incoming:
        return

    try:
        await progress_repo.create_schedule_change(
            {
                "goalId": goal.id,
                "userId": goal.user_id,
                "previousDate": previous,
                "newDate": incoming,
                "reason": reason,
                # Snapshotted, not derived on read: `Goal.prepId` is `ON DELETE SET NULL`, so deleting a
                # preparation would retroactively reclassify every past change on its goal as the
                # learner's own. What the entry is for is what was true when the date moved.
                "dateAuthority": goal_metrics.date_authority(goal),
            }
        )
    except Exception:
        logger.exception(
            "Failed to record goal schedule change (goal=%s, reason=%s). "
            "The deadline was moved; the history of it is now incomplete.",
            getattr(goal, "id", None),
            reason,
        )
