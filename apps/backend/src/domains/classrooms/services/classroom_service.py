"""
Classroom lifecycle — create, update, delete, list.
"""

import logging
from typing import Any

from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError

from ..repository import classroom_repo

logger = logging.getLogger(__name__)


async def create_classroom(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Create a Classroom within a Learning Space."""
    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, space_id, user_id, min_role="TUTOR")

    classroom = await classroom_repo.create_classroom({
        "circleId": space_id,
        "name": data["name"],
        "visibility": data.get("visibility", "PUBLIC"),
        "description": data.get("description"),
    })

    await emit("classroom.created", {"space_id": space_id, "classroom_id": classroom.id, "user_id": user_id})
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

    await _require_role(None, classroom.circle_id, user_id, min_role="TUTOR")

    update_data = {k: v for k, v in data.items() if v is not None}
    if update_data:
        await classroom_repo.update_classroom(classroom_id, update_data)
    return await classroom_repo.find_classroom(classroom_id)


async def delete_classroom(*, classroom_id: str, user_id: str) -> None:
    """Delete a classroom."""
    classroom = await classroom_repo.find_classroom(classroom_id)
    if not classroom:
        raise NotFoundError("Classroom", classroom_id)

    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, classroom.circle_id, user_id, min_role="ADMIN")
    await classroom_repo.delete_classroom(classroom_id)
