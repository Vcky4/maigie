"""Public API routes for Learning Spaces."""

from fastapi import APIRouter, status

from src.shared.auth import CurrentUser

from . import models
from .repository import space_repo
from .services import membership_service, space_service

router = APIRouter(tags=["learning-spaces"])


def _space_response(space, *, role: str | None = None) -> models.SpaceResponse:
    return models.SpaceResponse(
        id=space.id,
        name=space.name,
        description=space.description,
        avatarUrl=space.avatar_url,
        createdById=space.created_by_id,
        maxMembers=space.max_members,
        maxGroups=space.max_groups,
        memberCount=len(space.members),
        role=role,
        credits=space.credits,
        creditsLimit=space.credits_limit,
        createdAt=space.created_at,
        updatedAt=space.updated_at,
    )


@router.get("", response_model=list[models.SpaceResponse])
async def list_spaces(current_user: CurrentUser):
    """List spaces the authenticated user belongs to."""
    memberships = await space_service.list_user_spaces(user_id=current_user.id)
    return [_space_response(membership.space, role=membership.role) for membership in memberships]


@router.post("", response_model=models.SpaceResponse, status_code=status.HTTP_201_CREATED)
async def create_space(body: models.SpaceCreate, current_user: CurrentUser):
    """Create a space and make the authenticated user its owner."""
    space = await space_service.create_space(
        user=current_user,
        data=body.model_dump(exclude_none=True),
    )
    return _space_response(space, role="OWNER")


@router.get("/invites/pending", response_model=list[models.PendingInviteResponse])
async def list_pending_invites(current_user: CurrentUser):
    """List active email invitations addressed to the authenticated user."""
    invites = await membership_service.list_pending_invites(user_email=current_user.email)
    responses: list[models.PendingInviteResponse] = []
    for invite in invites:
        space = await space_repo.find_space_basic(invite.space_id)
        if not space:
            continue
        responses.append(
            models.PendingInviteResponse(
                id=invite.id,
                spaceId=invite.space_id,
                spaceName=space.name,
                inviterId=invite.inviter_id,
                inviteeEmail=invite.invitee_email,
                role=invite.role,
                expiresAt=invite.expires_at,
            )
        )
    return responses


@router.post("/invites/{invite_id}/accept", response_model=models.SpaceResponse)
async def accept_invite(invite_id: str, current_user: CurrentUser):
    """Accept an invitation and return the joined space."""
    space = await membership_service.accept_invite(
        invite_id=invite_id,
        user_id=current_user.id,
    )
    member = next((item for item in space.members if item.user_id == current_user.id), None)
    return _space_response(space, role=member.role if member else None)


@router.get("/{space_id}", response_model=models.SpaceResponse)
async def get_space(space_id: str, current_user: CurrentUser):
    """Get a space the authenticated user belongs to."""
    space = await space_service.get_space_detail(
        space_id=space_id,
        user_id=current_user.id,
    )
    member = next((item for item in space.members if item.user_id == current_user.id), None)
    return _space_response(space, role=member.role if member else None)
