"""
API routes for Circle (study group) management.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from fastapi import APIRouter, HTTPException, Query, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.circles import (
    CircleChatGroupCreate,
    CircleChatGroupResponse,
    CircleChatGroupUpdate,
    CircleCreate,
    CircleDetailResponse,
    CircleInviteCreate,
    CircleInviteResponse,
    CircleListResponse,
    CircleMemberResponse,
    CircleResponse,
    CircleUpdate,
    TransferOwnershipRequest,
)
from src.services import circle_service

router = APIRouter(prefix="/api/v1/circles", tags=["circles"])


# ==========================================
# Circle CRUD
# ==========================================


@router.post("", response_model=CircleDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_circle(
    data: CircleCreate,
    current_user: CurrentUser,
):
    """Create a new circle (requires Study Circle or Squad plan)."""
    circle = await circle_service.create_circle(db, current_user.id, str(current_user.tier), data)
    return _circle_detail_to_response(circle, current_user.id)


@router.get("", response_model=CircleListResponse)
async def list_circles(
    current_user: CurrentUser,
):
    """List all circles the current user belongs to."""
    memberships = await circle_service.list_user_circles(db, current_user.id)

    circles = []
    for membership in memberships:
        circle = membership.circle
        circles.append(
            CircleResponse(
                id=circle.id,
                name=circle.name,
                description=circle.description,
                avatarUrl=circle.avatarUrl,
                createdById=circle.createdById,
                maxMembers=circle.maxMembers,
                maxGroups=circle.maxGroups,
                memberCount=len(circle.members) if circle.members else 0,
                role=membership.role,
                createdAt=circle.createdAt,
                updatedAt=circle.updatedAt,
            )
        )

    return CircleListResponse(circles=circles, total=len(circles))


@router.get("/{circle_id}", response_model=CircleDetailResponse)
async def get_circle(
    circle_id: str,
    current_user: CurrentUser,
):
    """Get circle details (members, chat groups)."""
    circle = await circle_service.get_circle_detail(db, circle_id, current_user.id)
    return _circle_detail_to_response(circle, current_user.id)


@router.put("/{circle_id}", response_model=CircleDetailResponse)
async def update_circle(
    circle_id: str,
    data: CircleUpdate,
    current_user: CurrentUser,
):
    """Update a circle (owner only)."""
    circle = await circle_service.update_circle(db, circle_id, current_user.id, data)
    return _circle_detail_to_response(circle, current_user.id)


@router.delete("/{circle_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_circle(
    circle_id: str,
    current_user: CurrentUser,
):
    """Delete a circle (owner only)."""
    await circle_service.delete_circle(db, circle_id, current_user.id)
    return None


# ==========================================
# Ownership Transfer
# ==========================================


@router.post("/{circle_id}/transfer", response_model=CircleDetailResponse)
async def transfer_ownership(
    circle_id: str,
    data: TransferOwnershipRequest,
    current_user: CurrentUser,
):
    """Transfer circle ownership to another member (also transfers billing)."""
    circle = await circle_service.transfer_ownership(db, circle_id, current_user.id, data)
    return _circle_detail_to_response(circle, current_user.id)


# ==========================================
# Invites
# ==========================================


@router.post("/{circle_id}/invite", status_code=status.HTTP_201_CREATED)
async def invite_members(
    circle_id: str,
    data: CircleInviteCreate,
    current_user: CurrentUser,
):
    """Invite members to a circle by email (owner only)."""
    invites = await circle_service.invite_members(db, circle_id, current_user.id, data)
    return {"invites": len(invites), "message": f"Sent {len(invites)} invite(s)."}


@router.get("/invites/pending", response_model=list[CircleInviteResponse])
async def list_pending_invites(
    current_user: CurrentUser,
):
    """List pending circle invites for the current user."""
    invites = await circle_service.list_pending_invites(db, current_user.id)

    return [
        CircleInviteResponse(
            id=invite.id,
            circleId=invite.circleId,
            circleName=invite.circle.name if invite.circle else None,
            inviterId=invite.inviterId,
            inviteeEmail=invite.inviteeEmail,
            status=invite.status,
            expiresAt=invite.expiresAt,
            createdAt=invite.createdAt,
        )
        for invite in invites
    ]


@router.post("/{circle_id}/invite/{invite_id}/accept", response_model=CircleDetailResponse)
async def accept_invite(
    circle_id: str,
    invite_id: str,
    current_user: CurrentUser,
):
    """Accept a circle invite."""
    circle = await circle_service.accept_invite(db, circle_id, invite_id, current_user.id)
    return _circle_detail_to_response(circle, current_user.id)


@router.post("/{circle_id}/invite/{invite_id}/decline")
async def decline_invite(
    circle_id: str,
    invite_id: str,
    current_user: CurrentUser,
):
    """Decline a circle invite."""
    await circle_service.decline_invite(db, circle_id, invite_id, current_user.id)
    return {"message": "Invite declined."}


# ==========================================
# Members
# ==========================================


@router.delete("/{circle_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    circle_id: str,
    user_id: str,
    current_user: CurrentUser,
):
    """Remove a member from a circle (owner), or leave a circle (self)."""
    await circle_service.remove_member(db, circle_id, user_id, current_user.id)
    return None


# ==========================================
# Chat Groups
# ==========================================


@router.post(
    "/{circle_id}/groups",
    response_model=CircleChatGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_group(
    circle_id: str,
    data: CircleChatGroupCreate,
    current_user: CurrentUser,
):
    """Create a new chat group in a circle (owner only, max 5)."""
    group = await circle_service.create_chat_group(db, circle_id, current_user.id, data)
    return CircleChatGroupResponse(
        id=group.id,
        name=group.name,
        circleId=group.circleId,
        chatSessionId=group.chatSessionId,
        createdAt=group.createdAt,
        updatedAt=group.updatedAt,
    )


@router.get("/{circle_id}/groups", response_model=list[CircleChatGroupResponse])
async def list_chat_groups(
    circle_id: str,
    current_user: CurrentUser,
):
    """List chat groups in a circle."""
    groups = await circle_service.list_chat_groups(db, circle_id, current_user.id)
    return [
        CircleChatGroupResponse(
            id=group.id,
            name=group.name,
            circleId=group.circleId,
            chatSessionId=group.chatSessionId,
            createdAt=group.createdAt,
            updatedAt=group.updatedAt,
        )
        for group in groups
    ]


@router.put("/{circle_id}/groups/{group_id}", response_model=CircleChatGroupResponse)
async def rename_chat_group(
    circle_id: str,
    group_id: str,
    data: CircleChatGroupUpdate,
    current_user: CurrentUser,
):
    """Rename a chat group (owner only)."""
    group = await circle_service.update_chat_group(db, circle_id, group_id, current_user.id, data)
    return CircleChatGroupResponse(
        id=group.id,
        name=group.name,
        circleId=group.circleId,
        chatSessionId=group.chatSessionId,
        createdAt=group.createdAt,
        updatedAt=group.updatedAt,
    )


@router.delete("/{circle_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_group(
    circle_id: str,
    group_id: str,
    current_user: CurrentUser,
):
    """Delete a chat group (owner only)."""
    await circle_service.delete_chat_group(db, circle_id, group_id, current_user.id)
    return None


# ==========================================
# Circle Resources (scoped by circle)
# ==========================================


@router.get("/{circle_id}/notes")
async def list_circle_notes(
    circle_id: str,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """List shared notes in a circle."""
    notes, total = await circle_service.list_circle_notes(
        db, circle_id, current_user.id, page, size
    )
    return {"notes": notes, "total": total, "page": page, "size": size}


@router.get("/{circle_id}/goals")
async def list_circle_goals(
    circle_id: str,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """List shared goals in a circle."""
    goals, total = await circle_service.list_circle_goals(
        db, circle_id, current_user.id, page, size
    )
    return {"goals": goals, "total": total, "page": page, "size": size}


@router.get("/{circle_id}/courses")
async def list_circle_courses(
    circle_id: str,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """List shared courses in a circle."""
    courses, total = await circle_service.list_circle_courses(
        db, circle_id, current_user.id, page, size
    )
    return {"courses": courses, "total": total, "page": page, "size": size}


# ==========================================
# Helpers
# ==========================================


def _circle_detail_to_response(circle, current_user_id: str) -> CircleDetailResponse:
    """Convert a Prisma circle object (with includes) to CircleDetailResponse."""
    members = []
    current_role = None
    for m in circle.members or []:
        member_resp = CircleMemberResponse(
            id=m.id,
            userId=m.userId,
            name=m.user.name if m.user else None,
            email=m.user.email if m.user else None,
            role=m.role,
            joinedAt=m.joinedAt,
        )
        members.append(member_resp)
        if m.userId == current_user_id:
            current_role = m.role

    chat_groups = [
        CircleChatGroupResponse(
            id=g.id,
            name=g.name,
            circleId=g.circleId,
            chatSessionId=g.chatSessionId,
            createdAt=g.createdAt,
            updatedAt=g.updatedAt,
        )
        for g in (circle.chatGroups or [])
    ]

    return CircleDetailResponse(
        id=circle.id,
        name=circle.name,
        description=circle.description,
        avatarUrl=circle.avatarUrl,
        createdById=circle.createdById,
        maxMembers=circle.maxMembers,
        maxGroups=circle.maxGroups,
        members=members,
        chatGroups=chat_groups,
        role=current_role,
        createdAt=circle.createdAt,
        updatedAt=circle.updatedAt,
    )
