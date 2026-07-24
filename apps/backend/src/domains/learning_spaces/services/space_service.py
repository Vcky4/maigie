"""
Learning Space lifecycle — create, update, delete, list.
"""

import logging
from typing import Any

from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError

from ..models import SpaceCreate, SpaceUpdate
from ..repository import space_repo

logger = logging.getLogger(__name__)


async def create_space(*, user, data: dict[str, Any]) -> Any:
    """Create a new Learning Space. User becomes OWNER."""
    from src.domains.learning_spaces.services.space_impl import create_space_impl

    create_model = SpaceCreate(**data)
    space = await create_space_impl(None, user.id, str(user.tier), create_model)

    await emit("space.created", {"user_id": user.id, "space_id": space.id})
    return space


async def list_user_spaces(*, user_id: str) -> list[Any]:
    """List all Learning Spaces a user belongs to."""
    return await space_repo.list_user_spaces(user_id)


async def get_space_detail(*, space_id: str, user_id: str) -> Any:
    """Get detailed Learning Space info. Verifies membership."""
    from src.domains.learning_spaces.services.space_impl import get_space_detail_impl

    return await get_space_detail_impl(None, space_id, user_id)


async def update_space(*, space_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update space settings (OWNER/ADMIN only)."""
    from src.domains.learning_spaces.services.space_impl import update_space_impl

    update_model = SpaceUpdate(**data)
    return await update_space_impl(None, space_id, user_id, update_model)


async def delete_space(*, space_id: str, user_id: str) -> None:
    """Delete a Learning Space (OWNER only)."""
    from src.domains.learning_spaces.services.space_impl import delete_space_impl

    await delete_space_impl(None, space_id, user_id)
