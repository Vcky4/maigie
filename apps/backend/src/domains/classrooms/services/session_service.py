"""
Learning Session management — schedule, start, complete, cancel.
"""

import logging
from typing import Any

from src.shared.events import emit
from src.shared.exceptions import NotFoundError

from ..repository import classroom_repo

logger = logging.getLogger(__name__)


async def create_session(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Schedule a Learning Session."""
    from src.domains.learning_spaces.services.space_impl import _require_role

    await _require_role(None, space_id, user_id, min_role="TUTOR")

    session = await classroom_repo.create_session({
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
    })

    await emit("classroom.session_started", {
        "space_id": space_id,
        "session_id": session.id,
        "user_id": user_id,
    })
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

    update_data = {k: v for k, v in data.items() if v is not None}
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
