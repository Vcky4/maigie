"""
Service for Circle (study group) management.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from prisma import Prisma

from src.models.circles import (
    CircleChatGroupCreate,
    CircleChatGroupUpdate,
    CircleCreate,
    CircleInviteCreate,
    CircleUpdate,
    TransferOwnershipRequest,
)


# --- Tier constants ---

CIRCLE_CREATE_TIERS = (
    "STUDY_CIRCLE_MONTHLY",
    "STUDY_CIRCLE_YEARLY",
    "SQUAD_MONTHLY",
    "SQUAD_YEARLY",
)

MAX_CIRCLES_PER_USER = 5
MAX_MEMBERS_PER_CIRCLE = 5
MAX_GROUPS_PER_CIRCLE = 5
INVITE_EXPIRY_DAYS = 7


# --- Helpers ---


async def _verify_membership(db: Prisma, circle_id: str, user_id: str):
    """Verify user is a member of the circle. Returns the membership record."""
    member = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}}
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this circle.",
        )
    return member


async def _verify_owner(db: Prisma, circle_id: str, user_id: str):
    """Verify user is the OWNER of the circle."""
    member = await _verify_membership(db, circle_id, user_id)
    if member.role != "OWNER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the circle owner can perform this action.",
        )
    return member


# --- Circle CRUD ---


async def create_circle(db: Prisma, user_id: str, user_tier: str, data: CircleCreate):
    """
    Create a new circle. Only Study Circle / Squad tier users can create circles.
    """
    # Check tier
    if str(user_tier) not in CIRCLE_CREATE_TIERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You need a Study Circle or Squad plan to create circles.",
        )

    # Check max circles
    membership_count = await db.circlemember.count(where={"userId": user_id, "role": "OWNER"})
    if membership_count >= MAX_CIRCLES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can own up to {MAX_CIRCLES_PER_USER} circles.",
        )

    # Create circle
    circle = await db.circle.create(
        data={
            "name": data.name,
            "description": data.description,
            "createdById": user_id,
        }
    )

    # Add creator as OWNER member
    await db.circlemember.create(
        data={
            "circleId": circle.id,
            "userId": user_id,
            "role": "OWNER",
        }
    )

    # Create default "General" chat group
    chat_session = await db.chatsession.create(
        data={
            "userId": user_id,
            "title": f"{data.name} - General",
            "isActive": True,
        }
    )

    await db.circlechatgroup.create(
        data={
            "circleId": circle.id,
            "name": "General",
            "chatSessionId": chat_session.id,
        }
    )

    return await get_circle_detail(db, circle.id, user_id)


async def get_circle_detail(db: Prisma, circle_id: str, user_id: str):
    """Get a circle with full details (members, chat groups)."""
    await _verify_membership(db, circle_id, user_id)

    circle = await db.circle.find_unique(
        where={"id": circle_id},
        include={
            "members": {
                "include": {
                    "user": True,
                },
                "order_by": {"joinedAt": "asc"},
            },
            "chatGroups": {
                "order_by": {"createdAt": "asc"},
            },
        },
    )

    if not circle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Circle not found.",
        )

    return circle


async def list_user_circles(db: Prisma, user_id: str):
    """List all circles the user belongs to."""
    memberships = await db.circlemember.find_many(
        where={"userId": user_id},
        include={
            "circle": {
                "include": {
                    "members": True,
                },
            },
        },
        order_by={"joinedAt": "desc"},
    )

    return memberships


async def update_circle(db: Prisma, circle_id: str, user_id: str, data: CircleUpdate):
    """Update a circle (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        return await get_circle_detail(db, circle_id, user_id)

    await db.circle.update(
        where={"id": circle_id},
        data=update_data,
    )

    return await get_circle_detail(db, circle_id, user_id)


async def delete_circle(db: Prisma, circle_id: str, user_id: str) -> bool:
    """Delete a circle (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    await db.circle.delete(where={"id": circle_id})
    return True


# --- Ownership Transfer ---


async def transfer_ownership(
    db: Prisma,
    circle_id: str,
    user_id: str,
    data: TransferOwnershipRequest,
):
    """Transfer circle ownership to another member."""
    await _verify_owner(db, circle_id, user_id)

    # Verify new owner is a member
    new_owner_member = await db.circlemember.find_unique(
        where={
            "circleId_userId": {
                "circleId": circle_id,
                "userId": data.newOwnerUserId,
            }
        }
    )
    if not new_owner_member:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new owner must be an existing member of the circle.",
        )

    # Verify new owner has a circle-eligible tier
    new_owner = await db.user.find_unique(where={"id": data.newOwnerUserId})
    if not new_owner or str(new_owner.tier) not in CIRCLE_CREATE_TIERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new owner must have a Study Circle or Squad plan.",
        )

    # Transfer: demote current owner, promote new owner
    await db.circlemember.update(
        where={"circleId_userId": {"circleId": circle_id, "userId": user_id}},
        data={"role": "MEMBER"},
    )
    await db.circlemember.update(
        where={
            "circleId_userId": {
                "circleId": circle_id,
                "userId": data.newOwnerUserId,
            }
        },
        data={"role": "OWNER"},
    )

    # Update createdById on the circle
    await db.circle.update(
        where={"id": circle_id},
        data={"createdById": data.newOwnerUserId},
    )

    return await get_circle_detail(db, circle_id, user_id)


# --- Invite System ---


async def invite_members(db: Prisma, circle_id: str, user_id: str, data: CircleInviteCreate):
    """Invite members to a circle (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    # Check member count
    current_member_count = await db.circlemember.count(where={"circleId": circle_id})
    pending_invites = await db.circleinvite.count(
        where={"circleId": circle_id, "status": "PENDING"}
    )

    circle = await db.circle.find_unique(where={"id": circle_id})
    max_members = circle.maxMembers if circle else MAX_MEMBERS_PER_CIRCLE

    if current_member_count + pending_invites + len(data.emails) > max_members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This circle can have at most {max_members} members (including pending invites).",
        )

    created_invites = []
    for email in data.emails:
        # Check if already invited
        existing = await db.circleinvite.find_unique(
            where={
                "circleId_inviteeEmail": {
                    "circleId": circle_id,
                    "inviteeEmail": str(email),
                }
            }
        )
        if existing and existing.status == "PENDING":
            continue  # Skip already pending invites

        # Check if already a member (by email lookup)
        invitee_user = await db.user.find_unique(where={"email": str(email)})

        if invitee_user:
            # Check if already a member
            existing_member = await db.circlemember.find_unique(
                where={
                    "circleId_userId": {
                        "circleId": circle_id,
                        "userId": invitee_user.id,
                    }
                }
            )
            if existing_member:
                continue  # Already a member, skip

        # Check if invitee already belongs to max circles
        if invitee_user:
            invitee_circle_count = await db.circlemember.count(where={"userId": invitee_user.id})
            if invitee_circle_count >= MAX_CIRCLES_PER_USER:
                continue  # Can't join more circles

        expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)

        if existing:
            # Update existing invite (e.g., re-invite after decline/expire)
            invite = await db.circleinvite.update(
                where={"id": existing.id},
                data={
                    "status": "PENDING",
                    "expiresAt": expires_at,
                    "inviterId": user_id,
                    "inviteeId": invitee_user.id if invitee_user else None,
                },
            )
        else:
            invite = await db.circleinvite.create(
                data={
                    "circleId": circle_id,
                    "inviterId": user_id,
                    "inviteeEmail": str(email),
                    "inviteeId": invitee_user.id if invitee_user else None,
                    "expiresAt": expires_at,
                }
            )

        created_invites.append(invite)

    return created_invites


async def list_pending_invites(db: Prisma, user_id: str):
    """List pending invites for the current user."""
    user = await db.user.find_unique(where={"id": user_id})
    if not user:
        return []

    invites = await db.circleinvite.find_many(
        where={
            "inviteeEmail": user.email,
            "status": "PENDING",
        },
        include={
            "circle": True,
        },
        order_by={"createdAt": "desc"},
    )

    return invites


async def accept_invite(db: Prisma, circle_id: str, invite_id: str, user_id: str):
    """Accept a circle invite."""
    invite = await db.circleinvite.find_unique(
        where={"id": invite_id},
        include={"circle": True},
    )

    if not invite or invite.circleId != circle_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found.",
        )

    # Verify the invite is for this user
    user = await db.user.find_unique(where={"id": user_id})
    if not user or invite.inviteeEmail != user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is not for you.",
        )

    if invite.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This invite has already been {invite.status.lower()}.",
        )

    if invite.expiresAt < datetime.now(timezone.utc):
        await db.circleinvite.update(
            where={"id": invite_id},
            data={"status": "EXPIRED"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invite has expired.",
        )

    # Check max circles for the accepting user
    user_circle_count = await db.circlemember.count(where={"userId": user_id})
    if user_circle_count >= MAX_CIRCLES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can belong to a maximum of {MAX_CIRCLES_PER_USER} circles.",
        )

    # Add user as member
    await db.circlemember.create(
        data={
            "circleId": circle_id,
            "userId": user_id,
            "role": "MEMBER",
        }
    )

    # Update invite status
    await db.circleinvite.update(
        where={"id": invite_id},
        data={"status": "ACCEPTED", "inviteeId": user_id},
    )

    return await get_circle_detail(db, circle_id, user_id)


async def decline_invite(db: Prisma, circle_id: str, invite_id: str, user_id: str):
    """Decline a circle invite."""
    invite = await db.circleinvite.find_unique(where={"id": invite_id})

    if not invite or invite.circleId != circle_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found.",
        )

    user = await db.user.find_unique(where={"id": user_id})
    if not user or invite.inviteeEmail != user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is not for you.",
        )

    await db.circleinvite.update(
        where={"id": invite_id},
        data={"status": "DECLINED"},
    )

    return True


# --- Member Management ---


async def remove_member(db: Prisma, circle_id: str, target_user_id: str, current_user_id: str):
    """Remove a member or leave a circle."""
    if target_user_id == current_user_id:
        # Leaving the circle
        member = await _verify_membership(db, circle_id, current_user_id)
        if member.role == "OWNER":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Circle owners must transfer ownership before leaving.",
            )
    else:
        # Removing someone (owner only)
        await _verify_owner(db, circle_id, current_user_id)

    await db.circlemember.delete(
        where={
            "circleId_userId": {
                "circleId": circle_id,
                "userId": target_user_id,
            }
        }
    )

    return True


# --- Chat Groups ---


async def create_chat_group(db: Prisma, circle_id: str, user_id: str, data: CircleChatGroupCreate):
    """Create a new chat group in a circle (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    # Check max groups
    group_count = await db.circlechatgroup.count(where={"circleId": circle_id})
    circle = await db.circle.find_unique(where={"id": circle_id})
    max_groups = circle.maxGroups if circle else MAX_GROUPS_PER_CIRCLE

    if group_count >= max_groups:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This circle can have at most {max_groups} chat groups.",
        )

    # Create a backing ChatSession
    chat_session = await db.chatsession.create(
        data={
            "userId": user_id,
            "title": f"{circle.name} - {data.name}" if circle else data.name,
            "isActive": True,
        }
    )

    group = await db.circlechatgroup.create(
        data={
            "circleId": circle_id,
            "name": data.name,
            "chatSessionId": chat_session.id,
        }
    )

    return group


async def list_chat_groups(db: Prisma, circle_id: str, user_id: str):
    """List chat groups in a circle."""
    await _verify_membership(db, circle_id, user_id)

    groups = await db.circlechatgroup.find_many(
        where={"circleId": circle_id},
        order_by={"createdAt": "asc"},
    )

    return groups


async def update_chat_group(
    db: Prisma,
    circle_id: str,
    group_id: str,
    user_id: str,
    data: CircleChatGroupUpdate,
):
    """Rename a chat group (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    group = await db.circlechatgroup.find_unique(where={"id": group_id})
    if not group or group.circleId != circle_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat group not found.",
        )

    updated = await db.circlechatgroup.update(
        where={"id": group_id},
        data={"name": data.name},
    )

    return updated


async def delete_chat_group(db: Prisma, circle_id: str, group_id: str, user_id: str):
    """Delete a chat group (owner only)."""
    await _verify_owner(db, circle_id, user_id)

    group = await db.circlechatgroup.find_unique(where={"id": group_id})
    if not group or group.circleId != circle_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat group not found.",
        )

    # Delete the backing chat session if it exists
    if group.chatSessionId:
        await db.chatsession.delete(where={"id": group.chatSessionId})

    await db.circlechatgroup.delete(where={"id": group_id})
    return True


# --- Circle-scoped resources ---


async def list_circle_notes(
    db: Prisma, circle_id: str, user_id: str, page: int = 1, size: int = 20
):
    """List notes shared in a circle."""
    await _verify_membership(db, circle_id, user_id)

    skip = (page - 1) * size
    where = {"circleId": circle_id}

    total = await db.note.count(where=where)
    notes = await db.note.find_many(
        where=where,
        skip=skip,
        take=size,
        order_by={"updatedAt": "desc"},
        include={"tags": True, "attachments": True},
    )

    return notes, total


async def list_circle_goals(
    db: Prisma, circle_id: str, user_id: str, page: int = 1, size: int = 20
):
    """List goals shared in a circle."""
    await _verify_membership(db, circle_id, user_id)

    skip = (page - 1) * size
    where = {"circleId": circle_id}

    total = await db.goal.count(where=where)
    goals = await db.goal.find_many(
        where=where,
        skip=skip,
        take=size,
        order_by={"updatedAt": "desc"},
    )

    return goals, total


async def list_circle_courses(
    db: Prisma, circle_id: str, user_id: str, page: int = 1, size: int = 20
):
    """List courses shared in a circle."""
    await _verify_membership(db, circle_id, user_id)

    skip = (page - 1) * size
    where = {"circleId": circle_id}

    total = await db.course.count(where=where)
    courses = await db.course.find_many(
        where=where,
        skip=skip,
        take=size,
        order_by={"updatedAt": "desc"},
        include={"modules": True},
    )

    return courses, total
