"""
Educator Seat management within Learning Spaces.

Plus Seats give educators enhanced capabilities (advanced AI, more context).
"""

import logging
from typing import Any

from src.shared.exceptions import ForbiddenError

from ..repository import space_repo

logger = logging.getLogger(__name__)


async def list_seats(*, space_id: str, user_id: str) -> Any:
    """List all Plus Seat assignments (OWNER/ADMIN only)."""
    from src.domains.learning_spaces.services.seat_impl import list_seats as _list
    from src.shared.database import db

    return await _list(space_id, user_id, db_client=db)


async def assign_seat(*, space_id: str, target_user_id: str, user_id: str) -> Any:
    """Assign a Plus Seat to a member."""
    from src.domains.learning_spaces.services.seat_impl import assign_seat as _assign
    from src.shared.database import db

    return await _assign(space_id, target_user_id, user_id, db_client=db)


async def unassign_seat(*, space_id: str, target_user_id: str, user_id: str) -> Any:
    """Unassign a Plus Seat from a member (revert to free)."""
    from src.domains.learning_spaces.services.seat_impl import unassign_seat as _unassign
    from src.shared.database import db

    return await _unassign(space_id, target_user_id, user_id, db_client=db)


async def reassign_seat(*, space_id: str, from_user_id: str, to_user_id: str, user_id: str) -> Any:
    """Reassign a Plus Seat from one member to another."""
    from src.domains.learning_spaces.services.seat_impl import reassign_seat as _reassign
    from src.shared.database import db

    return await _reassign(space_id, from_user_id, to_user_id, user_id, db_client=db)
