"""
Study Block (schedule) management â€” CRUD with Google Calendar sync.
"""

import logging
from typing import Any

from src.domains.progress.db_models import ScheduleBlock
from src.shared.exceptions import NotFoundError, ValidationError
from src.shared.field_mapping import reject_unclearable

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
    *,
    user_id: str,
    start_date=None,
    end_date=None,
    course_id: str | None = None,
    goal_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
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
    # An explicit null clears the field; an omitted key leaves it alone. This used to be
    # `{k: v for k, v in data.items() if v is not None}`, which — given the route dumps the body with
    # `exclude_unset=True` — made clearing any field impossible while still returning success.
    #
    # Nullability is read from the mapped columns, so a null aimed at a NOT NULL column is refused with
    # a message the client can act on instead of a database constraint error.
    try:
        reject_unclearable(data, ScheduleBlock)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    update_data = data
    if not update_data:
        return block

    updated = await progress_repo.update_block(block_id, update_data)

    # A block going from not-done to done is the meaningful outcome of the study-session reminder
    # that pointed at it, so attribute it. Only on the transition, so re-saving a done block does
    # not re-fire; the recorder is idempotent and never raises, but the guard keeps the common
    # edit path from doing needless work. Imported locally, matching how the rest of progress
    # reaches into notifications, to avoid a domain import cycle.
    became_complete = (
        getattr(block, "completed_at", None) is None
        and getattr(updated, "completed_at", None) is not None
    )
    if became_complete:
        from src.domains.notifications import service as notification_service

        await notification_service.record_action(
            user_id=user_id,
            source_entity_id=block_id,
            source_entity_type="schedule_block",
        )

    # **Re-sync, because only creation used to.** A block moved to a different hour kept its original
    # time in Google for ever, so the learner's calendar and their schedule disagreed and the calendar
    # was the one they had chosen to trust. Same tolerance as creation: a Google failure must not fail an
    # edit that is already saved locally.
    try:
        from src.integrations.google_calendar import sync_schedule_block

        await sync_schedule_block(user_id, block_id)
    except Exception as e:
        logger.debug(f"Calendar sync skipped: {e}")

    return updated


async def delete_block(*, block_id: str, user_id: str) -> None:
    block = await progress_repo.find_block(block_id, user_id)
    if not block:
        raise NotFoundError("StudyBlock", block_id)

    # Read the event id before the row goes, because afterwards there is nothing to read it from.
    # Without this the event stayed in the learner's calendar for a session Maigie no longer has, and
    # only editing Google directly would remove it.
    event_id = getattr(block, "google_calendar_event_id", None)

    await progress_repo.delete_block(block_id)

    if event_id:
        try:
            from src.integrations.google_calendar import delete_schedule_block_event

            await delete_schedule_block_event(user_id, event_id)
        except Exception as e:
            logger.debug(f"Calendar event removal skipped: {e}")
