"""
Learning Spaces domain — API routes.

Collaborative learning environments. Covers space CRUD, membership,
invitations, chat groups, sessions, seats, and import.

Mounted at: /api/v1/spaces
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from prisma.models import User

from src.shared.auth import CurrentUser

from . import models
from .services import membership_service, seat_service, space_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["learning-spaces"])


# ===========================================================================
# Space CRUD
# ===========================================================================


@router.post("", response_model=models.SpaceDetailResponse, status_code=201)
async def create_space(body: models.SpaceCreate, current_user: CurrentUser):
    """Create a new Learning Space. Caller becomes OWNER."""
    if getattr(current_user, "suspended", False):
        raise HTTPException(status_code=403, detail="Account suspended")
    circle = await space_service.create_space(
        user=current_user, data=body.model_dump(exclude_unset=True)
    )
    return _to_detail(circle, current_user.id)


@router.get("", response_model=models.SpaceListResponse)
async def list_spaces(current_user: CurrentUser, skip: int = 0, limit: int = 20):
    """List Learning Spaces the user belongs to."""
    memberships = await space_service.list_user_spaces(user_id=current_user.id)
    items = [_membership_to_response(m, current_user.id) for m in memberships]
    return models.SpaceListResponse(spaces=items[skip: skip + limit], total=len(items))


@router.get("/{space_id}", response_model=models.SpaceDetailResponse)
async def get_space(space_id: str, current_user: CurrentUser):
    """Get Learning Space details."""
    circle = await space_service.get_space_detail(space_id=space_id, user_id=current_user.id)
    return _to_detail(circle, current_user.id)


@router.put("/{space_id}", response_model=models.SpaceDetailResponse)
async def update_space(space_id: str, body: models.SpaceUpdate, current_user: CurrentUser):
    """Update Learning Space settings (OWNER/ADMIN)."""
    circle = await space_service.update_space(
        space_id=space_id, user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )
    return _to_detail(circle, current_user.id)


@router.delete("/{space_id}", status_code=204)
async def delete_space(space_id: str, current_user: CurrentUser):
    """Delete a Learning Space (OWNER only)."""
    await space_service.delete_space(space_id=space_id, user_id=current_user.id)


# ===========================================================================
# Membership
# ===========================================================================


@router.post("/{space_id}/invite")
async def invite_members(space_id: str, body: models.InviteRequest, current_user: CurrentUser):
    """Invite members to a Learning Space."""
    return await membership_service.invite_members(
        space_id=space_id,
        user_id=current_user.id,
        emails=body.emails,
        role=body.role,
        seat_tier=body.seat_tier,
    )


@router.post("/{space_id}/leave", status_code=204)
async def leave_space(space_id: str, current_user: CurrentUser):
    """Leave a Learning Space."""
    await membership_service.leave_space(space_id=space_id, user_id=current_user.id)


@router.post("/{space_id}/members/{target_user_id}/role")
async def update_member_role(
    space_id: str, target_user_id: str, body: dict, current_user: CurrentUser
):
    """Update a member's role (OWNER/ADMIN only)."""
    return await membership_service.update_member_role(
        space_id=space_id,
        user_id=current_user.id,
        target_user_id=target_user_id,
        new_role=body.get("role", "LEARNER"),
    )


@router.delete("/{space_id}/members/{target_user_id}", status_code=204)
async def remove_member(space_id: str, target_user_id: str, current_user: CurrentUser):
    """Remove a member (OWNER/ADMIN only)."""
    await membership_service.remove_member(
        space_id=space_id, user_id=current_user.id, target_user_id=target_user_id
    )


@router.post("/{space_id}/transfer-ownership")
async def transfer_ownership(
    space_id: str, body: models.TransferOwnershipRequest, current_user: CurrentUser
):
    """Transfer ownership to another member."""
    return await membership_service.transfer_ownership(
        space_id=space_id, user_id=current_user.id, new_owner_id=body.newOwnerUserId
    )


# ===========================================================================
# Seats (Plus Seat management)
# ===========================================================================


@router.get("/{space_id}/seats")
async def list_seats(space_id: str, current_user: CurrentUser):
    """List all Plus Seat assignments (OWNER/ADMIN only)."""
    return await seat_service.list_seats(space_id=space_id, user_id=current_user.id)


@router.post("/{space_id}/seats/assign")
async def assign_seat(space_id: str, body: models.SeatAssignRequest, current_user: CurrentUser):
    """Assign a Plus Seat to a member."""
    return await seat_service.assign_seat(
        space_id=space_id, target_user_id=body.target_user_id, user_id=current_user.id
    )


@router.post("/{space_id}/seats/unassign")
async def unassign_seat(space_id: str, body: models.SeatAssignRequest, current_user: CurrentUser):
    """Unassign a Plus Seat."""
    return await seat_service.unassign_seat(
        space_id=space_id, target_user_id=body.target_user_id, user_id=current_user.id
    )


@router.post("/{space_id}/seats/reassign")
async def reassign_seat(space_id: str, body: models.SeatReassignRequest, current_user: CurrentUser):
    """Reassign a Plus Seat between members."""
    return await seat_service.reassign_seat(
        space_id=space_id,
        from_user_id=body.from_user_id,
        to_user_id=body.to_user_id,
        user_id=current_user.id,
    )


# ===========================================================================
# Helpers
# ===========================================================================


def _to_detail(circle, user_id: str) -> models.SpaceDetailResponse:
    """Convert Prisma circle model to SpaceDetailResponse."""
    members = getattr(circle, "members", []) or []
    user_role = None
    member_responses = []
    for m in members:
        if m.userId == user_id:
            user_role = m.role
        member_responses.append(models.SpaceMemberResponse(
            id=m.id,
            userId=m.userId,
            name=m.user.name if getattr(m, "user", None) else None,
            email=m.user.email if getattr(m, "user", None) else None,
            role=m.role,
            joinedAt=m.joinedAt,
        ))

    chat_groups = [
        models.ChatGroupResponse(
            id=g.id,
            name=g.name,
            circleId=g.circleId,
            chatSessionId=getattr(g, "chatSessionId", None),
            visibility=getattr(g, "visibility", "PUBLIC"),
            description=getattr(g, "description", None),
            createdAt=g.createdAt,
            updatedAt=g.updatedAt,
        )
        for g in (getattr(circle, "chatGroups", []) or [])
    ]

    courses = [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "progress": c.progress,
            "createdAt": c.createdAt.isoformat(),
        }
        for c in (getattr(circle, "courses", []) or [])
    ]

    return models.SpaceDetailResponse(
        id=circle.id,
        name=circle.name,
        description=circle.description,
        avatarUrl=circle.avatarUrl,
        createdById=circle.createdById,
        maxMembers=circle.maxMembers,
        maxGroups=circle.maxGroups,
        members=member_responses,
        chatGroups=chat_groups,
        courses=courses,
        role=user_role,
        credits=getattr(circle, "credits", 0),
        creditsLimit=getattr(circle, "creditsLimit", None),
        createdAt=circle.createdAt,
        updatedAt=circle.updatedAt,
    )


def _membership_to_response(membership, user_id: str) -> models.SpaceResponse:
    """Convert a membership row to SpaceResponse."""
    circle = getattr(membership, "circle", membership)
    role = getattr(membership, "role", None)
    members = getattr(circle, "members", []) or []

    if role is None:
        for m in members:
            if m.userId == user_id:
                role = m.role
                break

    return models.SpaceResponse(
        id=circle.id,
        name=circle.name,
        description=circle.description,
        avatarUrl=circle.avatarUrl,
        createdById=circle.createdById,
        maxMembers=circle.maxMembers,
        maxGroups=circle.maxGroups,
        memberCount=len(members),
        role=role,
        credits=getattr(circle, "credits", 0),
        creditsLimit=getattr(circle, "creditsLimit", None),
        createdAt=circle.createdAt,
        updatedAt=circle.updatedAt,
    )
