"""
Learning Space lifecycle — create, update, delete, list.
"""

import logging
from typing import Any

from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError

from ..repository import space_repo

logger = logging.getLogger(__name__)


async def create_space(*, user, data: dict[str, Any]) -> Any:
    """Create a new Learning Space. User becomes OWNER."""
    from src.domains.learning_spaces.services.space_impl import create_circle
    from src.domains.learning_spaces.models import CircleCreate

    create_model = CircleCreate(**data)
    circle = await create_circle(None, user.id, str(user.tier), create_model)

    await emit("space.created", {"user_id": user.id, "space_id": circle.id})
    return circle


async def list_user_spaces(*, user_id: str) -> list[Any]:
    """List all Learning Spaces a user belongs to."""
    return await space_repo.list_user_spaces(user_id)


async def get_space_detail(*, space_id: str, user_id: str) -> Any:
    """Get detailed Learning Space info. Verifies membership."""
    from src.domains.learning_spaces.services.space_impl import get_circle_detail

    return await get_circle_detail(None, space_id, user_id)


async def update_space(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update space settings (OWNER/ADMIN only)."""
    from src.domains.learning_spaces.services.space_impl import update_circle
    from src.domains.learning_spaces.models import CircleUpdate

    update_model = CircleUpdate(**data)
    return await update_circle(None, space_id, user_id, update_model)


async def delete_space(*, space_id: str, user_id: str) -> None:
    """Delete a Learning Space (OWNER only)."""
    from src.domains.learning_spaces.services.space_impl import delete_circle

    await delete_circle(None, space_id, user_id)
