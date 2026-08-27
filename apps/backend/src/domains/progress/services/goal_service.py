"""
Goal management â€” CRUD, progress recording, plan regeneration.
"""

import logging
from typing import Any

from src.domains.progress.db_models import Goal, GoalMilestone
from src.shared.exceptions import NotFoundError, ValidationError
from src.shared.field_mapping import reject_unclearable

from ..repository import progress_repo
from . import goal_schedule_log

logger = logging.getLogger(__name__)


def _reject_asserted_current_value(data: dict[str, Any], *, metric_kind: str | None) -> None:
    """Refuse a `currentValue` on a goal whose value is measured.

    Only a `manual` goal has a current value the learner supplies. For every other `metricKind` the
    figure is derived on read from the source that actually holds it, so accepting one here would
    store a second version of a number that already exists — and it would start disagreeing with the
    source the moment the source moved.

    **Refused rather than silently dropped**, which is the point. A learner who types a figure and
    watches it vanish on the next read has been overruled with no explanation; a 422 naming the field
    and the reason is something the client can act on. This is the same rule the backend applies to
    every other accept-and-discard path.
    """
    if data.get("currentValue") is None:
        return
    if (metric_kind or "manual") == "manual":
        return
    raise ValidationError(
        "currentValue can only be set on a goal with metricKind 'manual'. "
        f"This goal measures '{metric_kind}', so its current value is derived from that source."
    )


async def create_goal(*, user_id: str, data: dict[str, Any]) -> Any:
    _reject_asserted_current_value(data, metric_kind=data.get("metricKind"))
    return await progress_repo.create_goal({"userId": user_id, **data})


async def list_goals(
    *,
    user_id: str,
    status: str | None = None,
    space_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "createdAt",
    sort_order: str = "desc",
) -> tuple[list, int]:
    where: dict[str, Any] = {}
    if status:
        where["status"] = status
    if space_id:
        where["spaceId"] = space_id
    else:
        where["spaceId"] = None
    skip = (page - 1) * page_size
    return await progress_repo.list_goals(
        user_id, where=where, skip=skip, take=page_size, order={sort_by: sort_order}
    )


async def get_goal(*, goal_id: str, user_id: str) -> Any:
    goal = await progress_repo.find_goal(goal_id, user_id)
    if not goal:
        raise NotFoundError("Goal", goal_id)
    return goal


async def update_goal(*, goal_id: str, user_id: str, data: dict[str, Any]) -> Any:
    existing = await get_goal(goal_id=goal_id, user_id=user_id)
    # The kind being moved to, or the one already on the row when the update leaves it alone. Reading
    # only the request body would let a `currentValue` through on a measured goal whenever the caller
    # did not resend `metricKind`.
    _reject_asserted_current_value(data, metric_kind=data.get("metricKind", existing.metric_kind))
    # An explicit null clears the field; an omitted key leaves it alone. This used to be
    # `{k: v for k, v in data.items() if v is not None}`, which — given the route dumps the body with
    # `exclude_unset=True` — made clearing any field impossible while still returning success.
    #
    # Nullability is read from the mapped columns, so a null aimed at a NOT NULL column is refused with
    # a message the client can act on instead of a database constraint error.
    try:
        reject_unclearable(data, Goal)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    update_data = data
    if not update_data:
        return await progress_repo.find_goal(goal_id, user_id)

    updated = await progress_repo.update_goal(goal_id, update_data)
    # Recorded after the write, from the row as it was before it, and only when the learner actually
    # sent a deadline. `exclude_unset=True` on the route means the key is present only when they did, so
    # this cannot mistake "saved the title" for "moved the deadline". `goal_schedule_log` drops the
    # no-op case where the deadline sent matches the one already stored.
    if "targetDate" in update_data:
        await goal_schedule_log.record_date_change(
            goal=existing, new_date=update_data["targetDate"], reason="learner_edited"
        )
    return updated


async def record_progress(*, goal_id: str, user_id: str, progress: float) -> Any:
    goal = await get_goal(goal_id=goal_id, user_id=user_id)
    data: dict[str, Any] = {"progress": progress}
    if progress >= 100.0 and goal.status == "ACTIVE":
        data["status"] = "COMPLETED"
    return await progress_repo.update_goal(goal_id, data)


async def delete_goal(*, goal_id: str, user_id: str) -> None:
    await get_goal(goal_id=goal_id, user_id=user_id)
    await progress_repo.delete_goal(goal_id)


async def regenerate_plan(
    *, user_id: str, goal_id: str, duration_weeks: int = 4, request: str | None = None
) -> dict[str, Any]:
    from src.domains.intelligence.planning.planning_impl import regenerate_goal_plan

    return await regenerate_goal_plan(
        user_id=user_id, goal_id=goal_id, duration_weeks=duration_weeks, request=request
    )


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------
#
# A `GoalMilestone` has no `userId`. Ownership is established **only** by loading its goal through
# `get_goal`, which is scoped to the learner — so every function here starts with that call, and a
# milestone id from someone else's goal is indistinguishable from one that does not exist.


async def list_milestones(*, goal_id: str, user_id: str) -> list[Any]:
    """A goal's milestones, in the learner's chosen order."""
    await get_goal(goal_id=goal_id, user_id=user_id)
    return await progress_repo.list_milestones(goal_id)


async def create_milestone(*, goal_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Add a milestone to a goal."""
    await get_goal(goal_id=goal_id, user_id=user_id)
    return await progress_repo.create_milestone({"goalId": goal_id, **data})


async def get_milestone(*, goal_id: str, user_id: str, milestone_id: str) -> Any:
    await get_goal(goal_id=goal_id, user_id=user_id)
    milestone = await progress_repo.find_milestone(milestone_id, goal_id)
    if not milestone:
        raise NotFoundError("GoalMilestone", milestone_id)
    return milestone


async def update_milestone(
    *, goal_id: str, user_id: str, milestone_id: str, data: dict[str, Any]
) -> Any:
    """Edit a milestone, including marking it achieved or un-achieving it."""
    await get_milestone(goal_id=goal_id, user_id=user_id, milestone_id=milestone_id)
    try:
        reject_unclearable(data, GoalMilestone)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if not data:
        return await progress_repo.find_milestone(milestone_id, goal_id)
    return await progress_repo.update_milestone(milestone_id, data)


async def delete_milestone(*, goal_id: str, user_id: str, milestone_id: str) -> None:
    await get_milestone(goal_id=goal_id, user_id=user_id, milestone_id=milestone_id)
    await progress_repo.delete_milestone(milestone_id)
