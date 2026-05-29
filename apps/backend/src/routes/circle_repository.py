"""
Circle Repository routes — public discovery, search, and join.

GET /circles/repository           — Search/list public Circles
GET /circles/repository/featured  — Featured Circles carousel
GET /circles/repository/{id}      — Public Circle detail
POST /circles/repository/{id}/join — Join a public Circle

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.dependencies import CurrentUser, db
from src.services import circle_repository_service

router = APIRouter(prefix="/api/v1/circles/repository", tags=["circle-repository"])


@router.get("")
async def list_public_circles(
    query: str | None = Query(None, description="Search query"),
    category: str | None = Query(None, description="Filter by category"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """Search and list public Circles in the Repository."""
    return await circle_repository_service.list_public(
        query=query, category=category, page=page, size=size, db_client=db
    )


@router.get("/featured")
async def list_featured_circles():
    """List featured Circles for the Repository carousel."""
    return await circle_repository_service.list_featured(db_client=db)


@router.get("/{circle_id}")
async def get_public_circle_detail(circle_id: str):
    """Get public detail for a Circle."""
    detail = await circle_repository_service.get_public_detail(circle_id, db_client=db)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Circle not found or not publicly visible.",
        )
    return detail


@router.post("/{circle_id}/join")
async def join_circle(
    circle_id: str,
    current_user: CurrentUser,
):
    """Join a public Circle.

    Behavior depends on the Circle's joinPolicy:
    - AUTO_JOIN: immediately adds the user as a MEMBER with FREE_SEAT
    - REQUEST_TO_JOIN: creates a pending CircleJoinRequest and notifies admins
    """
    # Fetch the Circle
    circle = await db.circle.find_unique(where={"id": circle_id})
    if circle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Circle not found.",
        )

    if str(circle.visibility) != "PUBLIC":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Circle not found or not publicly visible.",
        )

    if circle.hiddenByModeration:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "CIRCLE_HIDDEN_FOR_VIOLATION",
                "message": "This Circle is currently hidden due to a policy violation.",
            },
        )

    # Check if already a member
    existing = await db.circlemember.find_unique(
        where={"circleId_userId": {"circleId": circle_id, "userId": current_user.id}}
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this Circle.",
        )

    join_policy = getattr(circle, "joinPolicy", "AUTO_JOIN")

    if join_policy == "AUTO_JOIN":
        # Immediately add as MEMBER with FREE_SEAT
        member = await db.circlemember.create(
            data={
                "circleId": circle_id,
                "userId": current_user.id,
                "role": "MEMBER",
                "seatTier": "FREE_SEAT",
            }
        )
        return {
            "status": "joined",
            "circleId": circle_id,
            "role": "MEMBER",
            "seatTier": "FREE_SEAT",
        }

    elif join_policy == "REQUEST_TO_JOIN":
        # Check for existing pending request
        existing_request = await db.circlejoinrequest.find_first(
            where={
                "circleId": circle_id,
                "userId": current_user.id,
                "status": "PENDING",
            }
        )
        if existing_request:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have a pending join request for this Circle.",
            )

        # Create pending join request
        join_request = await db.circlejoinrequest.create(
            data={
                "circleId": circle_id,
                "userId": current_user.id,
                "status": "PENDING",
            }
        )
        return {
            "status": "pending",
            "circleId": circle_id,
            "requestId": join_request.id,
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown join policy: {join_policy}",
        )
