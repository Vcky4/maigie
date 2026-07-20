"""
Service for Learning Space management.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, func
from src.domains.identity.repository import identity_repo
from src.domains.intelligence.repository import intelligence_repo
from src.domains.learning_spaces.models import (
    ChatGroupCreate,
    ChatGroupUpdate,
    ImportRequest,
    InviteRequest,
    SessionCreate,
    SessionUpdate,
    SpaceCreate,
    SpaceUpdate,
    TransferOwnershipRequest,
)
from src.shared.infrastructure.email import send_space_invite_email
from src.shared.database import get_session_factory

from ..db_models import SpaceInvite, SpaceMember, SpaceSeatAddon
from ..repository import space_repo

# --- Tier constants ---

SPACE_CREATE_TIERS = (
    "STUDY_CIRCLE_MONTHLY",
    "STUDY_CIRCLE_YEARLY",
    "SQUAD_MONTHLY",
    "SQUAD_YEARLY",
)

MAX_SPACES_PER_USER = 5
MAX_MEMBERS_PER_SPACE = 5
MAX_GROUPS_PER_SPACE = 5
INVITE_EXPIRY_DAYS = 7


# --- Helpers ---


async def _verify_membership(db: Any, space_id: str, user_id: str):
    """Verify user is a member of the space. Returns the membership record."""
    member = await space_repo.find_member(space_id, user_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this space.",
        )
    return member


async def _verify_owner(db: Any, space_id: str, user_id: str):
    """Verify user is the OWNER of the space."""
    member = await _verify_membership(db, space_id, user_id)
    if member.role != "OWNER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the space owner can perform this action.",
        )
    return member


async def _verify_admin(db: Any, space_id: str, user_id: str):
    """Verify user is an ADMIN or OWNER of the space."""
    member = await _verify_membership(db, space_id, user_id)
    if member.role not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only space admins can perform this action.",
        )
    return member


async def _verify_tutor(db: Any, space_id: str, user_id: str):
    """Verify user is a TUTOR, ADMIN or OWNER of the space."""
    member = await _verify_membership(db, space_id, user_id)
    if member.role not in ("OWNER", "ADMIN", "TUTOR"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only space tutors or admins can perform this action.",
        )
    return member


async def _sync_chat_group_session_metadata(db: Any, session_id: str):
    """Ensure space-backed chat sessions stay isolated from personal chat state."""
    await intelligence_repo.update_chat_session(
        session_id,
        {
            "isSpaceRoom": True,
            "isActive": False,
        },
    )


# --- Space CRUD ---


async def create_space_impl(db: Any, user_id: str, user_tier: str, data: SpaceCreate):
    """
    Create a new Learning Space. Any authenticated user can create a Space
    (Requirement 4.1). Suspended users are rejected at the route layer.
    """
    # Check max spaces — count where user is OWNER
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(SpaceMember)
            .where(SpaceMember.user_id == user_id, SpaceMember.role == "OWNER")
        )
        membership_count = result.scalar() or 0

    if membership_count >= MAX_SPACES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can own up to {MAX_SPACES_PER_USER} spaces.",
        )

    # Build create data with optional visibility and category
    create_data: dict = {
        "name": data.name,
        "description": data.description,
        "createdById": user_id,
        "creditsLimit": getattr(data, "creditsLimit", None),
    }
    # Accept visibility (default PRIVATE per Requirement 4.3)
    if hasattr(data, "visibility") and data.visibility is not None:
        create_data["visibility"] = data.visibility
    # Accept category
    if hasattr(data, "category") and data.category is not None:
        create_data["category"] = data.category

    # Create space
    space = await space_repo.create_space(create_data)

    # Add creator as OWNER member. Seat_Tier is PLUS_SEAT only if the
    # Space has an active Space_Plan; otherwise FREE_SEAT (Req 4.4).
    owner_seat_tier = "PLUS_SEAT" if space.space_plan_active else "FREE_SEAT"
    await space_repo.add_member(
        {
            "spaceId": space.id,
            "userId": user_id,
            "role": "OWNER",
            "seatTier": owner_seat_tier,
        }
    )

    # Create default "General" chat group (Requirement 4.5)
    chat_session = await intelligence_repo.create_chat_session(
        {
            "userId": user_id,
            "title": f"{data.name} - General",
            "isActive": False,
            "isSpaceRoom": True,
        }
    )

    await space_repo.create_chat_group(
        {
            "spaceId": space.id,
            "name": "General",
            "chatSessionId": chat_session.id,
        }
    )

    return await get_space_detail_impl(db, space.id, user_id)


async def get_space_detail_impl(db: Any, space_id: str, user_id: str):
    """Get a space with full details (members, chat groups)."""
    await _verify_membership(db, space_id, user_id)

    space = await space_repo.find_space(space_id)

    if not space:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Space not found.",
        )

    space = await _ensure_space_chat_sessions(db, space, user_id)
    return space


async def list_user_spaces_impl(db: Any, user_id: str):
    """List all spaces the user belongs to."""
    memberships = await space_repo.list_user_spaces(user_id)
    return memberships


async def update_space_impl(db: Any, space_id: str, user_id: str, data: SpaceUpdate):
    """Update a space (owner only)."""
    await _verify_owner(db, space_id, user_id)

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        return await get_space_detail_impl(db, space_id, user_id)

    await space_repo.update_space(space_id, update_data)

    return await get_space_detail_impl(db, space_id, user_id)


async def delete_space_impl(db: Any, space_id: str, user_id: str) -> bool:
    """Delete a space (owner only)."""
    await _verify_owner(db, space_id, user_id)

    await space_repo.delete_space(space_id)
    return True


async def set_visibility(db: Any, space_id: str, actor_user_id: str, visibility: str) -> dict:
    """Change a Space's visibility (OWNER or ADMIN only).

    Preserves all members on PUBLIC -> PRIVATE transition (Requirement 4.7).
    Syncs to repository within 60 s by updating the visibility column
    directly (the repository query filters on this column).
    """
    await _verify_admin(db, space_id, actor_user_id)

    if visibility not in ("PUBLIC", "PRIVATE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visibility must be PUBLIC or PRIVATE.",
        )

    space = await space_repo.update_space(space_id, {"visibility": visibility})

    return {
        "spaceId": space.id,
        "visibility": str(space.visibility),
    }


# --- Ownership Transfer ---


async def transfer_ownership(
    db: Any,
    space_id: str,
    user_id: str,
    data: TransferOwnershipRequest,
):
    """Transfer space ownership to another member.

    Any member can become the new owner (tier gate removed by Space
    Reimagining). The transfer is immediate for the basic case.
    """
    await _verify_owner(db, space_id, user_id)

    # Verify new owner is a member
    new_owner_member = await space_repo.find_member(space_id, data.newOwnerUserId)
    if not new_owner_member:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new owner must be an existing member of the space.",
        )

    # Transfer: demote current owner to ADMIN, promote new owner
    await space_repo.update_member(space_id, user_id, {"role": "ADMIN"})
    await space_repo.update_member(space_id, data.newOwnerUserId, {"role": "OWNER"})

    # Update createdById on the space
    await space_repo.update_space(space_id, {"createdById": data.newOwnerUserId})

    # If Space has active plan, ensure new owner gets PLUS_SEAT
    space = await space_repo.find_space_basic(space_id)
    if space and space.space_plan_active:
        await space_repo.update_member(space_id, data.newOwnerUserId, {"seatTier": "PLUS_SEAT"})

    return await get_space_detail_impl(db, space_id, user_id)


# --- Invite System ---


async def invite_members(db: Any, space_id: str, user_id: str, data: InviteRequest):
    """Invite members to a space (owner only)."""
    await _verify_owner(db, space_id, user_id)

    # Check member count
    current_member_count = await space_repo.count_members(space_id)

    # Count pending invites
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(SpaceInvite)
            .where(SpaceInvite.space_id == space_id, SpaceInvite.status == "PENDING")
        )
        pending_invites = result.scalar() or 0

    space = await space_repo.find_space_basic(space_id)
    max_members = space.max_members if space else MAX_MEMBERS_PER_SPACE

    if current_member_count + pending_invites + len(data.emails) > max_members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This space can have at most {max_members} members (including pending invites).",
        )

    created_invites = []
    for email in data.emails:
        # Check if already invited (case-insensitive)
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(SpaceInvite).where(
                    SpaceInvite.space_id == space_id,
                    func.lower(SpaceInvite.invitee_email) == str(email).lower(),
                )
            )
            existing = result.scalar_one_or_none()

        if existing and existing.status == "PENDING":
            continue  # Skip already pending invites

        # Check if already a member (by email lookup - case-insensitive)
        invitee_user = await identity_repo.find_by_email(str(email))

        if invitee_user:
            # Check if already a member
            existing_member = await space_repo.find_member(space_id, invitee_user.id)
            if existing_member:
                continue  # Already a member, skip

        # Check if invitee already belongs to max spaces
        if invitee_user:
            async with factory() as session:
                result = await session.execute(
                    select(func.count())
                    .select_from(SpaceMember)
                    .where(SpaceMember.user_id == invitee_user.id)
                )
                invitee_space_count = result.scalar() or 0
            if invitee_space_count >= MAX_SPACES_PER_USER:
                continue  # Can't join more spaces

        expires_at = datetime.now(UTC) + timedelta(days=INVITE_EXPIRY_DAYS)

        # Determine role and seat tier from invite data (default MEMBER / FREE_SEAT)
        invite_role = getattr(data, "role", None) or "MEMBER"
        invite_seat_tier = getattr(data, "seat_tier", None) or "FREE_SEAT"
        # Validate role — only OWNER can assign ADMIN/TUTOR
        if invite_role not in ("MEMBER", "ADMIN", "TUTOR"):
            invite_role = "MEMBER"
        if invite_seat_tier not in ("FREE_SEAT", "PLUS_SEAT"):
            invite_seat_tier = "FREE_SEAT"

        if existing:
            # Update existing invite (e.g., re-invite after decline/expire)
            await space_repo.update_invite(
                existing.id,
                {
                    "status": "PENDING",
                    "expiresAt": expires_at,
                    "inviterId": user_id,
                    "inviteeId": invitee_user.id if invitee_user else None,
                    "role": invite_role,
                    "seatTier": invite_seat_tier,
                },
            )
            invite = await space_repo.find_invite(existing.id)
        else:
            invite = await space_repo.create_invite(
                {
                    "spaceId": space_id,
                    "inviterId": user_id,
                    "inviteeEmail": str(email),
                    "inviteeId": invitee_user.id if invitee_user else None,
                    "expiresAt": expires_at,
                    "role": invite_role,
                    "seatTier": invite_seat_tier,
                }
            )

        # Send invite email
        inviter = await identity_repo.find_by_id(user_id)
        inviter_name = (inviter.name or inviter.email) if inviter else "Maigie User"
        await send_space_invite_email(
            str(email), inviter_name, space.name if space else "a learning space"
        )

        created_invites.append(invite)

    return {
        "message": f"Successfully sent {len(created_invites)} invite(s).",
        "invites": created_invites,
    }


async def cancel_invite(db: Any, space_id: str, invite_id: str, user_id: str):
    """Cancel a pending invite."""
    await _verify_owner(db, space_id, user_id)

    invite = await space_repo.find_invite(invite_id)

    if not invite or invite.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found for this space.",
        )

    if invite.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending invites can be cancelled.",
        )

    # Delete invite via raw SQLAlchemy (no delete_invite in repo)
    from sqlalchemy import delete

    factory = get_session_factory()
    async with factory() as session:
        stmt = delete(SpaceInvite).where(SpaceInvite.id == invite_id)
        await session.execute(stmt)
        await session.commit()

    return {"message": "Invite cancelled successfully."}


async def list_pending_invites(db: Any, user_id: str):
    """List pending invites for the current user."""
    user = await identity_repo.find_by_id(user_id)
    if not user:
        return []

    invites = await space_repo.list_user_invites(user.email)
    return invites


async def accept_invite(db: Any, space_id: str, invite_id: str, user_id: str):
    """Accept a space invite."""
    invite = await space_repo.find_invite(invite_id)

    if not invite or invite.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found.",
        )

    # Verify the invite is for this user (case-insensitive email comparison)
    user = await identity_repo.find_by_id(user_id)
    if not user or invite.invitee_email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This invite is for {invite.invitee_email}, but you are logged in as {user.email if user else 'unknown'}."
                if invite.invitee_email.lower() != (user.email.lower() if user else "")
                else "Invite verification failed."
            ),
        )

    if invite.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This invite has already been {invite.status.lower()}.",
        )

    if invite.expires_at < datetime.now(UTC):
        await space_repo.update_invite(invite_id, {"status": "EXPIRED"})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invite has expired.",
        )

    # Check if already a member
    existing_membership = await space_repo.find_member(space_id, user_id)

    if not existing_membership:
        # Check max spaces for the accepting user
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(func.count()).select_from(SpaceMember).where(SpaceMember.user_id == user_id)
            )
            user_space_count = result.scalar() or 0
        if user_space_count >= MAX_SPACES_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You can belong to a maximum of {MAX_SPACES_PER_USER} spaces. Please leave another space first.",
            )

        # Add user as member with the role and seat tier specified in the invite
        invite_role = getattr(invite, "role", "MEMBER") or "MEMBER"
        invite_seat_tier = getattr(invite, "seat_tier", "FREE_SEAT") or "FREE_SEAT"
        await space_repo.add_member(
            {
                "spaceId": space_id,
                "userId": user_id,
                "role": invite_role,
                "seatTier": invite_seat_tier,
            }
        )

    # Update invite status
    await space_repo.update_invite(invite_id, {"status": "ACCEPTED", "inviteeId": user_id})

    return await get_space_detail_impl(db, space_id, user_id)


async def decline_invite(db: Any, space_id: str, invite_id: str, user_id: str):
    """Decline a space invite."""
    invite = await space_repo.find_invite(invite_id)

    if not invite or invite.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found.",
        )

    user = await identity_repo.find_by_id(user_id)
    if not user or invite.invitee_email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is not for you.",
        )

    await space_repo.update_invite(invite_id, {"status": "DECLINED"})

    return True


# --- Member Management ---


async def remove_member(db: Any, space_id: str, target_user_id: str, current_user_id: str):
    """Remove a member or leave a space."""
    if target_user_id == current_user_id:
        # Leaving the space
        member = await _verify_membership(db, space_id, current_user_id)
        if member.role == "OWNER":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Space owners must transfer ownership before leaving.",
            )
    else:
        # Removing someone (owner only)
        await _verify_owner(db, space_id, current_user_id)

    # Release any PLUS_SEAT held by the departing member before deletion
    from src.domains.learning_spaces.services.seat_impl import release_seat_on_member_remove

    await release_seat_on_member_remove(space_id, target_user_id, db_client=db)

    await space_repo.remove_member(space_id, target_user_id)

    return True


async def update_member_role(
    db: Any, space_id: str, target_user_id: str, new_role: str, current_user_id: str
):
    """Change a member's role. Only OWNER or ADMIN can do this. Cannot change OWNER."""
    # Verify the caller is OWNER or ADMIN
    await _verify_admin(db, space_id, current_user_id)

    # Cannot change your own role
    if target_user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role.",
        )

    # Get the target member
    target_member = await space_repo.find_member(space_id, target_user_id)
    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this space.",
        )

    # Cannot change the OWNER's role
    if target_member.role == "OWNER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the owner's role. Use ownership transfer instead.",
        )

    # Only OWNER can promote to ADMIN
    if new_role == "ADMIN":
        caller = await space_repo.find_member(space_id, current_user_id)
        if caller and caller.role != "OWNER":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the space owner can promote members to ADMIN.",
            )

    updated = await space_repo.update_member(space_id, target_user_id, {"role": new_role})

    return {
        "userId": target_user_id,
        "role": updated.role,
        "message": f"Role updated to {new_role}.",
    }


# --- Chat Groups ---


async def create_chat_group(db: Any, space_id: str, user_id: str, data: ChatGroupCreate):
    """Create a new chat group in a space (owner/admin only, gated by plan)."""
    await _verify_admin(db, space_id, user_id)

    # Plan-aware gate check (Task 5.4 / 8.3)
    from src.domains.learning_spaces.services.space_gates import SpaceFeature, SpaceGateError, SpaceGateState, gate

    space = await space_repo.find_space_basic(space_id)

    # Count chat groups
    groups = await space_repo.list_chat_groups(space_id)
    group_count = len(groups)

    # Check if any active add-on exists
    has_addon = False
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(SpaceSeatAddon)
                .where(
                    SpaceSeatAddon.space_id == space_id,
                    SpaceSeatAddon.status.in_(["ACTIVE", "TRIALING"]),
                )
            )
            addon_count = result.scalar() or 0
        has_addon = addon_count > 0
    except Exception:
        pass

    state = SpaceGateState(
        space_plan_active=space.space_plan_active if space else False,
        has_any_active_addon=has_addon,
        chat_group_count=group_count,
    )

    try:
        gate(SpaceFeature.CHAT_GROUP_CREATE, state)
    except SpaceGateError as e:
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        )

    # Create a backing ChatSession
    chat_session = await intelligence_repo.create_chat_session(
        {
            "userId": user_id,
            "title": f"{space.name} - {data.name}" if space else data.name,
            "isActive": False,
            "isSpaceRoom": True,
        }
    )

    group = await space_repo.create_chat_group(
        {
            "spaceId": space_id,
            "name": data.name,
            "chatSessionId": chat_session.id,
        }
    )

    return group


async def list_chat_groups(db: Any, space_id: str, user_id: str):
    """List chat groups in a space."""
    await _verify_membership(db, space_id, user_id)

    groups = await space_repo.list_chat_groups(space_id)

    repaired_groups = []
    for group in groups:
        if group.chat_session_id:
            await _sync_chat_group_session_metadata(db, group.chat_session_id)
            repaired_groups.append(group)
            continue
        repaired_groups.append(await _ensure_chat_group_session(db, group, user_id))

    return repaired_groups


async def _ensure_space_chat_sessions(db: Any, space, user_id: str):
    """Backfill missing chat sessions for legacy space groups."""
    repaired_groups = []
    repaired_any = False

    for group in space.chat_groups or []:
        if group.chat_session_id:
            await _sync_chat_group_session_metadata(db, group.chat_session_id)
            repaired_groups.append(group)
            continue

        repaired_groups.append(await _ensure_chat_group_session(db, group, user_id, space))
        repaired_any = True

    if repaired_any:
        space.chat_groups = repaired_groups

    return space


async def _ensure_chat_group_session(db: Any, group, user_id: str, space=None):
    """Create a backing chat session for groups created before chatSessionId was enforced."""
    if group.chat_session_id:
        return group

    owning_space = space or await space_repo.find_space_basic(group.space_id)
    session_owner_id = getattr(owning_space, "created_by_id", None) or user_id
    session_title = f"{owning_space.name} - {group.name}" if owning_space else group.name

    chat_session = await intelligence_repo.create_chat_session(
        {
            "userId": session_owner_id,
            "title": session_title,
            "isActive": False,
            "isSpaceRoom": True,
        }
    )

    await space_repo.update_chat_group(group.id, {"chatSessionId": chat_session.id})
    # Refresh and return updated group
    return await space_repo.find_chat_group(group.id)


async def update_chat_group(
    db: Any,
    space_id: str,
    group_id: str,
    user_id: str,
    data: ChatGroupUpdate,
):
    """Update a chat group (owner/admin only)."""
    await _verify_admin(db, space_id, user_id)

    group = await space_repo.find_chat_group(group_id)
    if not group or group.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat group not found.",
        )

    update_data: dict = {}
    if data.name is not None:
        update_data["name"] = data.name
    if data.visibility is not None:
        update_data["visibility"] = data.visibility
    if data.description is not None:
        update_data["description"] = data.description

    if not update_data:
        return group

    await space_repo.update_chat_group(group_id, update_data)
    return await space_repo.find_chat_group(group_id)


async def delete_chat_group(db: Any, space_id: str, group_id: str, user_id: str):
    """Delete a chat group (owner only)."""
    await _verify_owner(db, space_id, user_id)

    group = await space_repo.find_chat_group(group_id)
    if not group or group.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat group not found.",
        )

    # Delete the backing chat session if it exists
    if group.chat_session_id:
        await intelligence_repo.delete_chat_session(group.chat_session_id)

    await space_repo.delete_chat_group(group_id)
    return True


# --- Space-scoped resources ---


async def list_space_notes(db: Any, space_id: str, user_id: str, page: int = 1, size: int = 20):
    """List notes shared in a space."""
    await _verify_membership(db, space_id, user_id)

    from sqlalchemy import text

    factory = get_session_factory()
    skip = (page - 1) * size
    async with factory() as session:
        total_result = await session.execute(
            text('SELECT COUNT(*) FROM "Note" WHERE "spaceId" = :cid'),
            {"cid": space_id},
        )
        total = total_result.scalar() or 0

        result = await session.execute(
            text(
                'SELECT * FROM "Note" WHERE "spaceId" = :cid '
                'ORDER BY "updatedAt" DESC OFFSET :skip LIMIT :take'
            ),
            {"cid": space_id, "skip": skip, "take": size},
        )
        notes = [dict(row._mapping) for row in result.fetchall()]

    return notes, total


async def list_space_goals(db: Any, space_id: str, user_id: str, page: int = 1, size: int = 20):
    """List goals shared in a space."""
    await _verify_membership(db, space_id, user_id)

    from src.domains.progress.db_models import Goal

    factory = get_session_factory()
    skip = (page - 1) * size
    async with factory() as session:
        total_result = await session.execute(
            select(func.count()).select_from(Goal).where(Goal.space_id == space_id)
        )
        total = total_result.scalar() or 0

        stmt = (
            select(Goal)
            .where(Goal.space_id == space_id)
            .order_by(Goal.updated_at.desc())
            .offset(skip)
            .limit(size)
        )
        result = await session.execute(stmt)
        goals = list(result.scalars().all())

    return goals, total


async def list_space_courses(db: Any, space_id: str, user_id: str, page: int = 1, size: int = 20):
    """List courses shared in a space."""
    await _verify_membership(db, space_id, user_id)

    from src.domains.knowledge.db_models import Course

    factory = get_session_factory()
    skip = (page - 1) * size
    async with factory() as session:
        total_result = await session.execute(
            select(func.count()).select_from(Course).where(Course.space_id == space_id)
        )
        total = total_result.scalar() or 0

        stmt = (
            select(Course)
            .where(Course.space_id == space_id)
            .order_by(Course.updated_at.desc())
            .offset(skip)
            .limit(size)
        )
        result = await session.execute(stmt)
        courses = list(result.scalars().all())

    return courses, total


async def award_contribution_points(db: Any, space_id: str, user_id: str, points: int):
    """Award contribution points to a space member."""
    from ..db_models import SpaceMemberStat
    from sqlalchemy import update as sa_update

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SpaceMemberStat).where(
                SpaceMemberStat.space_id == space_id,
                SpaceMemberStat.user_id == user_id,
            )
        )
        stat = result.scalar_one_or_none()

        if not stat:
            new_stat = SpaceMemberStat(
                space_id=space_id, user_id=user_id, contribution_points=points
            )
            session.add(new_stat)
        else:
            stmt = (
                sa_update(SpaceMemberStat)
                .where(SpaceMemberStat.id == stat.id)
                .values(contribution_points=stat.contribution_points + points)
            )
            await session.execute(stmt)
        await session.commit()


async def import_to_space(db: Any, space_id: str, user_id: str, data: ImportRequest):
    """Import items (notes, courses, resources, goals) into a space."""
    await _verify_membership(db, space_id, user_id)

    from sqlalchemy import update as sa_update, text
    from src.domains.knowledge.db_models import Course, Resource
    from src.domains.progress.db_models import Goal

    imported_stats = {"notes": 0, "courses": 0, "resources": 0, "goals": 0}
    factory = get_session_factory()

    # Import Notes (no SQLAlchemy model — use raw SQL)
    for note_id in data.noteIds:
        async with factory() as session:
            result = await session.execute(
                text('SELECT id, "userId", "spaceId" FROM "Note" WHERE id = :nid'),
                {"nid": note_id},
            )
            note = result.fetchone()
            if note and note.userId == user_id and not note.spaceId:
                await session.execute(
                    text('UPDATE "Note" SET "spaceId" = :cid WHERE id = :nid'),
                    {"cid": space_id, "nid": note_id},
                )
                await session.commit()
                imported_stats["notes"] += 1

    # Import Courses
    for course_id in data.courseIds:
        async with factory() as session:
            result = await session.execute(select(Course).where(Course.id == course_id))
            course = result.scalar_one_or_none()
            if course and course.user_id == user_id and not course.space_id:
                stmt = sa_update(Course).where(Course.id == course_id).values(space_id=space_id)
                await session.execute(stmt)
                await session.commit()
                imported_stats["courses"] += 1

    # Import Resources
    for resource_id in data.resourceIds:
        async with factory() as session:
            result = await session.execute(select(Resource).where(Resource.id == resource_id))
            resource = result.scalar_one_or_none()
            if resource and resource.user_id == user_id and not resource.space_id:
                stmt = (
                    sa_update(Resource).where(Resource.id == resource_id).values(space_id=space_id)
                )
                await session.execute(stmt)
                await session.commit()
                imported_stats["resources"] += 1

    # Import Goals
    if hasattr(data, "goalIds") and data.goalIds:
        for goal_id in data.goalIds:
            async with factory() as session:
                result = await session.execute(select(Goal).where(Goal.id == goal_id))
                goal = result.scalar_one_or_none()
                if goal and goal.user_id == user_id and not goal.space_id:
                    stmt = sa_update(Goal).where(Goal.id == goal_id).values(space_id=space_id)
                    await session.execute(stmt)
                    await session.commit()
                    imported_stats["goals"] += 1

    return imported_stats


async def export_from_space(
    db: Any, space_id: str, user_id: str, resource_type: str, resource_id: str
):
    """Export (copy) a Space resource into the user's Personal_Workspace.

    Gated by ``Space.allowMemberExport`` — OWNER is always allowed.
    The original resource remains in the Space.

    Args:
        resource_type: One of "note", "course", "goal", "resource"
        resource_id: ID of the resource to export

    Returns:
        The newly created personal copy.
    """
    member = await _verify_membership(db, space_id, user_id)

    # Check export permission
    space = await space_repo.find_space_basic(space_id)
    if space is None:
        raise HTTPException(status_code=404, detail="Space not found.")

    is_owner = str(member.role) == "OWNER"
    if not is_owner and not space.allow_member_export:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member export is not allowed for this Space.",
        )

    from sqlalchemy import text
    from src.domains.knowledge.db_models import Course
    from src.domains.progress.db_models import Goal

    factory = get_session_factory()

    if resource_type == "note":
        import uuid as _uuid

        async with factory() as session:
            result = await session.execute(
                text('SELECT * FROM "Note" WHERE id = :nid'),
                {"nid": resource_id},
            )
            original = result.fetchone()
        if not original or original.spaceId != space_id:
            raise HTTPException(status_code=404, detail="Note not found in this Space.")
        new_id = _uuid.uuid4().hex[:25]
        async with factory() as session:
            await session.execute(
                text(
                    'INSERT INTO "Note" (id, title, content, "userId", "spaceId", summary) '
                    "VALUES (:id, :title, :content, :uid, NULL, :summary)"
                ),
                {
                    "id": new_id,
                    "title": original.title,
                    "content": original.content,
                    "uid": user_id,
                    "summary": original.summary,
                },
            )
            await session.commit()
        return {"type": "note", "id": new_id, "title": original.title}

    elif resource_type == "course":
        async with factory() as session:
            result = await session.execute(select(Course).where(Course.id == resource_id))
            original = result.scalar_one_or_none()
        if not original or original.space_id != space_id:
            raise HTTPException(status_code=404, detail="Course not found in this Space.")
        async with factory() as session:
            copy = Course(
                title=original.title,
                description=original.description,
                user_id=user_id,
                space_id=None,
                difficulty=original.difficulty,
                is_ai_generated=original.is_ai_generated,
            )
            session.add(copy)
            await session.commit()
            await session.refresh(copy)
        return {"type": "course", "id": copy.id, "title": copy.title}

    elif resource_type == "goal":
        async with factory() as session:
            result = await session.execute(select(Goal).where(Goal.id == resource_id))
            original = result.scalar_one_or_none()
        if not original or original.space_id != space_id:
            raise HTTPException(status_code=404, detail="Goal not found in this Space.")
        async with factory() as session:
            copy = Goal(
                title=original.title,
                description=original.description,
                user_id=user_id,
                space_id=None,
                target_date=original.target_date,
            )
            session.add(copy)
            await session.commit()
            await session.refresh(copy)
        return {"type": "goal", "id": copy.id, "title": copy.title}

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported resource type: {resource_type}",
        )


# --- Group Sessions ---


async def create_group_session(db: Any, space_id: str, user_id: str, data: SessionCreate):
    """Create a new scheduled group session (gated by plan)."""
    await _verify_admin(db, space_id, user_id)

    # Plan-aware gate check (Task 8.3)
    from src.domains.learning_spaces.services.space_gates import SpaceFeature, SpaceGateError, SpaceGateState, gate

    space = await space_repo.find_space_basic(space_id)

    # Count sessions
    sessions = await space_repo.list_sessions(space_id)
    session_count = len(sessions)

    has_addon = False
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(SpaceSeatAddon)
                .where(
                    SpaceSeatAddon.space_id == space_id,
                    SpaceSeatAddon.status.in_(["ACTIVE", "TRIALING"]),
                )
            )
            addon_count = result.scalar() or 0
        has_addon = addon_count > 0
    except Exception:
        pass

    state = SpaceGateState(
        space_plan_active=space.space_plan_active if space else False,
        has_any_active_addon=has_addon,
        group_session_count=session_count,
    )

    try:
        gate(SpaceFeature.GROUP_SESSION_START, state)
    except SpaceGateError as e:
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        )

    chat_group = await space_repo.find_chat_group(data.chatGroupId)
    if not chat_group or chat_group.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please select a valid chat destination for this space.",
        )

    session_obj = await space_repo.create_session(
        {
            "spaceId": space_id,
            "title": data.title,
            "description": data.description,
            "scheduledAt": data.scheduledAt,
            "duration": data.duration,
            "chatGroupId": chat_group.id,
            "topicId": data.topicId,
            "goalId": data.goalId,
            "createdById": user_id,
        }
    )
    return session_obj


async def list_group_sessions(db: Any, space_id: str, user_id: str):
    """List all group sessions for a space."""
    await _verify_membership(db, space_id, user_id)

    sessions = await space_repo.list_sessions(space_id)
    return sessions


async def update_group_session(
    db: Any, space_id: str, session_id: str, user_id: str, data: SessionUpdate
):
    """Update a group session."""
    await _verify_admin(db, space_id, user_id)

    session_obj = await space_repo.find_session(session_id)
    if not session_obj or session_obj.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    update_data = data.model_dump(exclude_unset=True)
    if "chatGroupId" in update_data and update_data["chatGroupId"]:
        chat_group = await space_repo.find_chat_group(update_data["chatGroupId"])
        if not chat_group or chat_group.space_id != space_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please select a valid chat destination for this space.",
            )

    if update_data:
        await space_repo.update_session(session_id, update_data)
        session_obj = await space_repo.find_session(session_id)

    return session_obj


async def delete_group_session(db: Any, space_id: str, session_id: str, user_id: str) -> None:
    """Delete a group session."""
    await _verify_admin(db, space_id, user_id)

    session_obj = await space_repo.find_session(session_id)
    if not session_obj or session_obj.space_id != space_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )

    await space_repo.delete_session(session_id)


async def suggest_group_sessions(db: Any, space_id: str, user_id: str) -> list[dict]:
    """Generate AI suggestions for group sessions based on space's recent activity."""
    await _verify_membership(db, space_id, user_id)

    # Gather some context: recent courses and goals in the space
    from src.domains.knowledge.db_models import Course
    from src.domains.progress.db_models import Goal

    factory = get_session_factory()
    async with factory() as session:
        courses_result = await session.execute(
            select(Course)
            .where(Course.space_id == space_id)
            .order_by(Course.updated_at.desc())
            .limit(3)
        )
        recent_courses = list(courses_result.scalars().all())

        goals_result = await session.execute(
            select(Goal).where(Goal.space_id == space_id).order_by(Goal.updated_at.desc()).limit(3)
        )
        recent_goals = list(goals_result.scalars().all())

    context_lines = []
    if recent_courses:
        context_lines.append("Recent Courses:")
        for c in recent_courses:
            context_lines.append(f"- {c.title}")

    if recent_goals:
        context_lines.append("Recent Goals:")
        for g in recent_goals:
            context_lines.append(f"- {g.title}")

    context_text = "\n".join(context_lines)
    if not context_text:
        context_text = "The space is new and doesn't have much activity yet."

    prompt = f"""You are an AI study assistant managing a learning space.
Based on the space's recent activity, suggest 3 relevant group study sessions or discussion topics.

Space Activity:
{context_text}

Provide your response strictly as valid JSON matching this schema:
[
  {{
    "title": "Short catchy title",
    "description": "Brief description of what the group will do",
    "duration": 60, // integer, minutes (30, 45, or 60)
    "reason": "Why this is a good idea right now"
  }}
]
"""
    try:
        import json
        import re

        from google import genai
        from google.genai import types

        from src.domains.intelligence.reasoning.llm.registry import LlmTask, default_model_for, gemini_api_key

        client = genai.Client(api_key=gemini_api_key() or None)
        response = await client.aio.models.generate_content(
            model=default_model_for(LlmTask.STRUCTURED_COMPLETION),
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=800,
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )
        text = (response.text or "").strip()

        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\[[\s\S]*\]", text)
            if match:
                return json.loads(match.group(0))
            return []
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Failed to generate session suggestions: {e}")
        # Fallback to generic suggestions
        return [
            {
                "title": "Weekly Review & Planning",
                "description": "Let's meet to review what we learned this week and set goals for the next.",
                "duration": 45,
                "reason": "Good for weekly alignment",
            },
            {
                "title": "Q&A Study Jam",
                "description": "Bring your hardest questions and let's solve them together.",
                "duration": 60,
                "reason": "Helps clear blockers",
            },
        ]
