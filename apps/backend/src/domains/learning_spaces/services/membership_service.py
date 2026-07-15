"""
Learning Space membership â€” join, leave, invite, roles, transfer ownership.
"""

import logging
from typing import Any

from prisma.models import User

from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError, ValidationError

from ..repository import space_repo

logger = logging.getLogger(__name__)


async def invite_members(*, space_id: str, user_id: str, emails: list[str], role: str | None = None, seat_tier: str | None = None) -> list[Any]:
    """Invite users to a Learning Space by email."""
    from src.domains.learning_spaces.services.space_impl import invite_members as _invite
    from src.shared.database import db
    from src.domains.learning_spaces.models import CircleInviteCreate

    invite_data = CircleInviteCreate(emails=emails, role=role, seat_tier=seat_tier)
    return await _invite(db, space_id, user_id, invite_data)


async def accept_invite(*, invite_id: str, user_id: str) -> Any:
    """Accept a pending invitation."""
    from src.domains.learning_spaces.services.space_impl import accept_invite as _accept
    from src.shared.database import db

    result = await _accept(db, invite_id, user_id)
    if result:
        await emit("space.member_joined", {"user_id": user_id, "space_id": result.get("circleId", "")})
    return result


async def leave_space(*, space_id: str, user_id: str) -> None:
    """Leave a Learning Space."""
    from src.domains.learning_spaces.services.space_impl import leave_circle as _leave
    from src.shared.database import db

    await _leave(db, space_id, user_id)
    await emit("space.member_left", {"user_id": user_id, "space_id": space_id})


async def update_member_role(*, space_id: str, user_id: str, target_user_id: str, new_role: str) -> Any:
    """Update a member's role within the space."""
    from src.domains.learning_spaces.services.space_impl import update_member_role as _update
    from src.shared.database import db

    result = await _update(db, space_id, user_id, target_user_id, new_role)
    await emit("space.role_changed", {
        "space_id": space_id,
        "user_id": target_user_id,
        "new_role": new_role,
    })
    return result


async def remove_member(*, space_id: str, user_id: str, target_user_id: str) -> None:
    """Remove a member from the space (OWNER/ADMIN only)."""
    from src.domains.learning_spaces.services.space_impl import remove_member as _remove
    from src.shared.database import db

    await _remove(db, space_id, user_id, target_user_id)
    await emit("space.member_left", {"user_id": target_user_id, "space_id": space_id})


async def transfer_ownership(*, space_id: str, user_id: str, new_owner_id: str) -> Any:
    """Transfer space ownership to another member."""
    from src.domains.learning_spaces.services.space_impl import transfer_ownership as _transfer
    from src.shared.database import db

    return await _transfer(db, space_id, user_id, new_owner_id)


async def list_pending_invites(*, user_email: str) -> list[Any]:
    """List pending invitations for a user."""
    return await space_repo.list_user_invites(user_email)
