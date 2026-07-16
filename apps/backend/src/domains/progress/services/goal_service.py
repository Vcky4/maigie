"""
Goal management â€” CRUD, progress recording, plan regeneration.
"""

import logging
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import progress_repo

logger = logging.getLogger(__name__)


async def create_goal(*, user_id: str, data: dict[str, Any]) -> Any:
    return await progress_repo.create_goal({"userId": user_id, **data})


async def list_goals(
    *, user_id: str, status: str | None = None, space_id: str | None = None,
    page: int = 1, page_size: int = 20, sort_by: str = "createdAt", sort_order: str = "desc",
) -> tuple[list, int]:
    where: dict[str, Any] = {}
    if status:
        where["status"] = status
    if space_id:
        where["spaceId"] = space_id
    else:
        where["spaceId"] = None
    skip = (page - 1) * page_size
    return await progress_repo.list_goals(user_id, where=where, skip=skip, take=page_size, order={sort_by: sort_order})


async def get_goal(*, goal_id: str, user_id: str) -> Any:
    goal = await progress_repo.find_goal(goal_id, user_id)
    if not goal:
        raise NotFoundError("Goal", goal_id)
    return goal


async def update_goal(*, goal_id: str, user_id: str, data: dict[str, Any]) -> Any:
    await get_goal(goal_id=goal_id, user_id=user_id)
    update_data = {k: v for k, v in data.items() if v is not None}
    if update_data:
        return await progress_repo.update_goal(goal_id, update_data)
    return await progress_repo.find_goal(goal_id, user_id)


async def record_progress(*, goal_id: str, user_id: str, progress: float) -> Any:
    goal = await get_goal(goal_id=goal_id, user_id=user_id)
    data: dict[str, Any] = {"progress": progress}
    if progress >= 100.0 and goal.status == "ACTIVE":
        data["status"] = "COMPLETED"
    return await progress_repo.update_goal(goal_id, data)


async def delete_goal(*, goal_id: str, user_id: str) -> None:
    await get_goal(goal_id=goal_id, user_id=user_id)
    await progress_repo.delete_goal(goal_id)


async def regenerate_plan(*, user_id: str, goal_id: str, duration_weeks: int = 4, request: str | None = None) -> dict[str, Any]:
    from src.domains.intelligence.planning.planning_impl import regenerate_goal_plan
    return await regenerate_goal_plan(user_id=user_id, goal_id=goal_id, duration_weeks=duration_weeks, request=request)
