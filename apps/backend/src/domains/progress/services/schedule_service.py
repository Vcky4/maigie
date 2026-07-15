"""
Study Block (schedule) management â€” CRUD with Google Calendar sync.
"""

import logging
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import progress_repo

logger = logging.getLogger(__name__)


async def create_block(*, user_id: str, data: dict[str, Any]) -> Any:
    block = await progress_repo.create_block({"userId": user_id, **data})
    # Sync to Google Calendar if connected
    try:
        from src.integrations.google_calendar import sync_schedule_block
        await sync_schedule_block(user_id, block.id)
    except Exception as e:
        logger.debug(f"Calendar sync skipped: {e}")
    return block


async def list_blocks(
    *, user_id: str, start_date=None, end_date=None,
    course_id: str | None = None, goal_id: str | None = None,
    page: int = 1, page_size: int = 50,
) -> tuple[list, int]:
    where: dict[str, Any] = {}
    if course_id:
        where["courseId"] = course_id
    if goal_id:
        where["goalId"] = goal_id
    if start_date:
        where["endAt"] = {"gte": start_date}
    if end_date:
        if "startAt" not in where:
            where["startAt"] = {}
        where["startAt"] = {"lte": end_date}
    skip = (page - 1) * page_size
    return await progress_repo.list_blocks(user_id, where=where, skip=skip, take=page_size)


async def update_block(*, block_id: str, user_id: str, data: dict[str, Any]) -> Any:
    block = await progress_repo.find_block(block_id, user_id)
    if not block:
        raise NotFoundError("StudyBlock", block_id)
    update_data = {k: v for k, v in data.items() if v is not None}
    if update_data:
        return await progress_repo.update_block(block_id, update_data)
    return block


async def delete_block(*, block_id: str, user_id: str) -> None:
    block = await progress_repo.find_block(block_id, user_id)
    if not block:
        raise NotFoundError("StudyBlock", block_id)
    await progress_repo.delete_block(block_id)
