from fastapi import APIRouter, status

from src.dependencies import CurrentUser, db
from src.models.circles import (
    CircleCreate,
    CircleUpdate,
    CircleResponse,
    CircleDetailResponse,
    CircleListResponse,
    CircleInviteCreate,
    CircleInviteResponse,
    CircleChatGroupCreate,
    CircleChatGroupUpdate,
    CircleChatGroupResponse,
    CircleSessionResponse,
    CircleSessionCreate,
    CircleSessionUpdate,
    CircleSessionSuggestionResponse,
    CircleImportRequest,
    CircleMemberResponse,
    TransferOwnershipRequest,
)
from src.services import circle_service

router = APIRouter(prefix="/circles", tags=["Circles"])


def _circle_detail_to_response(circle, user_id: str) -> CircleDetailResponse:
    """Helper to convert circle model to response model."""
    # Find user's role in this circle
    user_role = None
    for member in circle.members:
        if member.userId == user_id:
            user_role = member.role
            break

    return CircleDetailResponse(
        id=circle.id,
        name=circle.name,
        description=circle.description,
        avatarUrl=circle.avatarUrl,
        createdById=circle.createdById,
        maxMembers=circle.maxMembers,
        maxGroups=circle.maxGroups,
        role=user_role,
        members=[
            CircleMemberResponse(
                id=m.id,
                userId=m.userId,
                name=m.user.name if m.user else None,
                email=m.user.email if m.user else None,
                role=m.role,
                joinedAt=m.joinedAt,
            )
            for m in circle.members
        ],
        chatGroups=[
            CircleChatGroupResponse(
                id=g.id,
                name=g.name,
                circleId=g.circleId,
                chatSessionId=g.chatSessionId,
                createdAt=g.createdAt,
                updatedAt=g.updatedAt,
            )
            for g in circle.chatGroups
        ],
        invites=[
            CircleInviteResponse(
                id=i.id,
                circleId=i.circleId,
                circleName=circle.name,
                inviterId=i.inviterId,
                inviteeEmail=i.inviteeEmail,
                status=i.status,
                expiresAt=i.expiresAt,
                createdAt=i.createdAt,
            )
            for i in getattr(circle, "invites", [])
        ],
        credits=getattr(circle, "credits", 0),
        creditsLimit=getattr(circle, "creditsLimit", None),
        createdAt=circle.createdAt,
        updatedAt=circle.updatedAt,
    )


def _circle_list_to_response(circle_or_membership, user_id: str) -> CircleResponse:
    """Convert a circle or membership row into the list response shape."""
    circle = getattr(circle_or_membership, "circle", circle_or_membership)
    role = getattr(circle_or_membership, "role", None)
    members = getattr(circle, "members", []) or []

    if role is None:
        for member in members:
            if member.userId == user_id:
                role = member.role
                break

    return CircleResponse(
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


@router.post("/", response_model=CircleDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_circle(
    data: CircleCreate,
    current_user: CurrentUser,
):
    """Create a new study circle."""
    circle = await circle_service.create_circle(
        db,
        current_user.id,
        str(current_user.tier),
        data,
    )
    return _circle_detail_to_response(circle, current_user.id)


@router.get("/", response_model=CircleListResponse)
async def list_circles(
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 20,
):
    """List circles the user belongs to."""
    circles = await circle_service.list_user_circles(db, current_user.id)
    items = [_circle_list_to_response(circle, current_user.id) for circle in circles]
    return CircleListResponse(circles=items[skip : skip + limit], total=len(items))


@router.get("/{circle_id}", response_model=CircleDetailResponse)
async def get_circle(
    circle_id: str,
    current_user: CurrentUser,
):
    """Get details for a specific circle."""
    circle = await circle_service.get_circle_detail(db, circle_id, current_user.id)
    return _circle_detail_to_response(circle, current_user.id)


@router.put("/{circle_id}", response_model=CircleDetailResponse)
@router.patch("/{circle_id}", response_model=CircleDetailResponse)
async def update_circle(
    circle_id: str,
    data: CircleUpdate,
    current_user: CurrentUser,
):
    """Update circle settings (owner only)."""
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


@router.post("/{circle_id}/transfer", response_model=CircleDetailResponse)
async def transfer_ownership(
    circle_id: str,
    data: TransferOwnershipRequest,
    current_user: CurrentUser,
):
    """Transfer ownership of a circle to another member."""
    circle = await circle_service.transfer_ownership(db, circle_id, current_user.id, data)
    return _circle_detail_to_response(circle, current_user.id)


# ==========================================
# Invites
# ==========================================


@router.post("/{circle_id}/invite", status_code=status.HTTP_200_OK)
async def invite_members(
    circle_id: str,
    data: CircleInviteCreate,
    current_user: CurrentUser,
):
    """Invite members to a circle by email (owner only)."""
    return await circle_service.invite_members(db, circle_id, current_user.id, data)


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


@router.delete("/{circle_id}/invite/{invite_id}")
async def cancel_invite(
    circle_id: str,
    invite_id: str,
    current_user: CurrentUser,
):
    """Cancel a pending circle invite (owner only)."""
    return await circle_service.cancel_invite(db, circle_id, invite_id, current_user.id)


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
@router.patch("/{circle_id}/groups/{group_id}", response_model=CircleChatGroupResponse)
async def update_chat_group(
    circle_id: str,
    group_id: str,
    data: CircleChatGroupUpdate,
    current_user: CurrentUser,
):
    """Update a chat group (owner only)."""
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
# Sessions
# ==========================================


@router.post(
    "/{circle_id}/sessions",
    response_model=CircleSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    circle_id: str,
    data: CircleSessionCreate,
    current_user: CurrentUser,
):
    """Create a new study session in a circle."""
    session = await circle_service.create_group_session(db, circle_id, current_user.id, data)
    return session


@router.get("/{circle_id}/sessions", response_model=list[CircleSessionResponse])
async def list_sessions(
    circle_id: str,
    current_user: CurrentUser,
):
    """List study sessions in a circle."""
    sessions = await circle_service.list_group_sessions(db, circle_id, current_user.id)
    return sessions


@router.put("/{circle_id}/sessions/{session_id}", response_model=CircleSessionResponse)
@router.patch("/{circle_id}/sessions/{session_id}", response_model=CircleSessionResponse)
async def update_session(
    circle_id: str,
    session_id: str,
    data: CircleSessionUpdate,
    current_user: CurrentUser,
):
    """Update a study session."""
    session = await circle_service.update_group_session(
        db,
        circle_id,
        session_id,
        current_user.id,
        data,
    )
    return session


@router.delete("/{circle_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    circle_id: str,
    session_id: str,
    current_user: CurrentUser,
):
    """Delete a study session."""
    await circle_service.delete_group_session(db, circle_id, session_id, current_user.id)
    return None


@router.get("/{circle_id}/sessions/suggest", response_model=CircleSessionSuggestionResponse)
async def suggest_sessions(
    circle_id: str,
    current_user: CurrentUser,
):
    """Suggest study sessions for a circle based on recent activity."""
    suggestions = await circle_service.suggest_group_sessions(db, circle_id, current_user.id)
    return CircleSessionSuggestionResponse(suggestions=suggestions)


@router.get("/{circle_id}/notes")
async def list_circle_notes(
    circle_id: str,
    current_user: CurrentUser,
    page: int = 1,
    size: int = 10,
):
    """List notes shared in the circle."""
    notes, total = await circle_service.list_circle_notes(
        db, circle_id, current_user.id, page, size
    )
    pages = (total + size - 1) // size if size else 0
    return {
        "items": notes,
        "notes": notes,
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.get("/{circle_id}/goals")
async def list_circle_goals(
    circle_id: str,
    current_user: CurrentUser,
    page: int = 1,
    size: int = 20,
):
    """List goals shared in the circle."""
    goals, total = await circle_service.list_circle_goals(
        db, circle_id, current_user.id, page, size
    )
    pages = (total + size - 1) // size if size else 0
    return {
        "goals": goals,
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.get("/{circle_id}/courses")
async def list_circle_courses(
    circle_id: str,
    current_user: CurrentUser,
    page: int = 1,
    size: int = 20,
):
    """List courses shared in the circle."""
    courses, total = await circle_service.list_circle_courses(
        db,
        circle_id,
        current_user.id,
        page,
        size,
    )
    pages = (total + size - 1) // size if size else 0
    return {
        "courses": courses,
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.post("/{circle_id}/import")
async def import_to_circle(
    circle_id: str,
    data: CircleImportRequest,
    current_user: CurrentUser,
):
    """Import existing personal content into a circle."""
    imported = await circle_service.import_to_circle(db, circle_id, current_user.id, data)
    return {
        "success": True,
        "imported": imported,
    }
