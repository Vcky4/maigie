"""
Classroom lifecycle — create, update, delete, list.
"""

import logging
from typing import Any

from src.domains.learning_spaces.db_models import SpaceChatGroup
from src.shared.events import ClassroomEvents, emit
from src.shared.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.shared.field_mapping import reject_unclearable

from ..repository import classroom_repo

logger = logging.getLogger(__name__)


async def create_classroom(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Create a Classroom within a Learning Space."""
    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, space_id, user_id, min_role="TUTOR")

    classroom = await classroom_repo.create_classroom(
        {
            "spaceId": space_id,
            "name": data["name"],
            "visibility": data.get("visibility", "PUBLIC"),
            "description": data.get("description"),
        }
    )

    # `memberIds` used to be accepted and dropped. The field was declared on `ClassroomCreate` and
    # documented as the initial member list for a private classroom, but was named nowhere else in the
    # domain — so creating a private classroom with members returned `201` and added none of them.
    #
    # Members are added after the classroom exists rather than in the same statement, because the rows
    # need its id. A failure here leaves an empty classroom rather than no classroom, which is the
    # recoverable half: the educator can add members, whereas a rolled-back create loses the name,
    # the description and the visibility they chose.
    if data.get("memberIds"):
        await classroom_repo.add_members(classroom.id, data["memberIds"])

    await emit(
        ClassroomEvents.CLASSROOM_CREATED,
        {"space_id": space_id, "classroom_id": classroom.id, "user_id": user_id},
    )
    return classroom


async def list_classrooms(*, space_id: str) -> list:
    """List all classrooms in a Learning Space."""
    return await classroom_repo.list_classrooms(space_id)


async def get_classroom(*, classroom_id: str) -> Any:
    """Get a classroom by ID."""
    classroom = await classroom_repo.find_classroom(classroom_id)
    if not classroom:
        raise NotFoundError("Classroom", classroom_id)
    return classroom


async def update_classroom(*, classroom_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update classroom settings."""
    classroom = await classroom_repo.find_classroom(classroom_id)
    if not classroom:
        raise NotFoundError("Classroom", classroom_id)

    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, classroom.space_id, user_id, min_role="TUTOR")

    # An explicit null clears the field; an omitted key leaves it alone. This used to be
    # `{k: v for k, v in data.items() if v is not None}`, which — given the route dumps the body with
    # `exclude_unset=True` — made clearing any field impossible while still returning success.
    #
    # Nullability is read from the mapped columns, so a null aimed at a NOT NULL column is refused with
    # a message the client can act on instead of a database constraint error.
    try:
        reject_unclearable(data, SpaceChatGroup)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    update_data = data
    if update_data:
        await classroom_repo.update_classroom(classroom_id, update_data)
    return await classroom_repo.find_classroom(classroom_id)


async def delete_classroom(*, classroom_id: str, user_id: str) -> None:
    """Delete a classroom."""
    classroom = await classroom_repo.find_classroom(classroom_id)
    if not classroom:
        raise NotFoundError("Classroom", classroom_id)

    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, classroom.space_id, user_id, min_role="ADMIN")
    await classroom_repo.delete_classroom(classroom_id)
