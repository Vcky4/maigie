"""
Learning Session management — schedule, start, complete, cancel.
"""

import logging
from typing import Any

from src.domains.learning_spaces.db_models import SpaceSession
from src.shared.events import ClassroomEvents, emit
from src.shared.exceptions import NotFoundError, ValidationError
from src.shared.field_mapping import reject_unclearable

from ..repository import classroom_repo

logger = logging.getLogger(__name__)


async def create_session(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Schedule a Learning Session."""
    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, space_id, user_id, min_role="TUTOR")

    session = await classroom_repo.create_session(
        {
            "spaceId": space_id,
            "title": data["title"],
            "description": data.get("description"),
            "scheduledAt": data["scheduledAt"],
            "duration": data.get("duration", 60),
            "chatGroupId": data.get("classroomId"),
            "topicId": data.get("topicId"),
            "goalId": data.get("goalId"),
            "createdById": user_id,
            "status": "SCHEDULED",
        }
    )

    await emit(
        ClassroomEvents.SESSION_STARTED,
        {
            "space_id": space_id,
            "session_id": session.id,
            "user_id": user_id,
        },
    )
    return session


async def list_sessions(*, space_id: str, classroom_id: str | None = None) -> list:
    """List sessions in a space, optionally filtered by classroom."""
    return await classroom_repo.list_sessions(space_id, classroom_id=classroom_id)


async def list_upcoming(*, space_id: str, limit: int = 10) -> list:
    """List upcoming sessions."""
    return await classroom_repo.list_upcoming_sessions(space_id, limit=limit)


async def update_session(*, session_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update a session (reschedule, change status, etc.)."""
    session = await classroom_repo.find_session(session_id)
    if not session:
        raise NotFoundError("Session", session_id)

    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, session.space_id, user_id, min_role="TUTOR")

    # An explicit null clears the field; an omitted key leaves it alone. This used to be
    # `{k: v for k, v in data.items() if v is not None}`, which — given the route dumps the body with
    # `exclude_unset=True` — made clearing any field impossible while still returning success.
    #
    # Nullability is read from the mapped columns, so a null aimed at a NOT NULL column is refused with
    # a message the client can act on instead of a database constraint error.
    try:
        reject_unclearable(data, SpaceSession)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    update_data = data
    if update_data:
        await classroom_repo.update_session(session_id, update_data)
    return await classroom_repo.find_session(session_id)


async def delete_session(*, session_id: str, user_id: str) -> None:
    """Delete/cancel a session."""
    session = await classroom_repo.find_session(session_id)
    if not session:
        raise NotFoundError("Session", session_id)

    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, session.space_id, user_id, min_role="TUTOR")
    await classroom_repo.delete_session(session_id)


async def suggest_sessions(*, space_id: str, user_id: str) -> list[dict[str, Any]]:
    """Generate AI-suggested sessions for a space."""
    from src.domains.learning_spaces.services.space_impl import suggest_sessions as _suggest

    return await _suggest(None, space_id, user_id)
