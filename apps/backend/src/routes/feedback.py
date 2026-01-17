"""
Feedback routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from prisma import Client as PrismaClient

from src.core.security import decode_access_token
from src.dependencies import CurrentUser, db
from src.utils.dependencies import get_db_client
from src.models.auth import TokenData
from src.models.feedback import (
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

# Optional security for anonymous feedback submission
security_optional = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Security(security_optional),
):
    """Get current user if authenticated, otherwise return None."""
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = decode_access_token(token)
        email: str = payload.get("sub")
        if email is None:
            return None

        token_data = TokenData(email=email)
        user = await db.user.find_unique(
            where={"email": token_data.email}, include={"preferences": True}
        )

        if user is None or not user.isActive:
            return None

        return user
    except (JWTError, Exception):
        return None


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def create_feedback(
    feedback_data: FeedbackCreate,
    request: Request,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user_optional)] = None,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Create a new feedback entry.
    Can be submitted by authenticated users or anonymously.
    """
    try:
        # Get user agent and page URL from request
        user_agent = request.headers.get("user-agent")
        page_url = feedback_data.pageUrl or str(request.url)

        # Create feedback
        feedback = await db.feedback.create(
            data={
                "userId": current_user.id if current_user else None,
                "type": feedback_data.type,
                "title": feedback_data.title,
                "description": feedback_data.description,
                "pageUrl": page_url,
                "userAgent": user_agent,
                "metadata": feedback_data.metadata,
                "status": "PENDING",
            }
        )

        logger.info(
            f"Feedback created: {feedback.id} by user {current_user.id if current_user else 'anonymous'}"
        )

        return FeedbackResponse.model_validate(feedback)
    except Exception as e:
        logger.error(f"Error creating feedback: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create feedback",
        )


@router.get("", response_model=FeedbackListResponse)
async def list_feedback(
    current_user: CurrentUser,
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status (PENDING, REVIEWED, RESOLVED, ARCHIVED)",
    ),
    type_filter: str | None = Query(
        None,
        alias="type",
        description="Filter by type (BUG_REPORT, FEATURE_REQUEST, etc.)",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    List feedback entries.
    Users can only see their own feedback.
    Admins can see all feedback.
    """
    try:
        # Build where clause
        where_clause = {}
        if current_user.role != "ADMIN":
            where_clause["userId"] = current_user.id
        if status_filter:
            where_clause["status"] = status_filter
        if type_filter:
            where_clause["type"] = type_filter

        # Get total count
        total = await db.feedback.count(where=where_clause)

        # Get paginated feedback
        skip = (page - 1) * pageSize
        feedback_list = await db.feedback.find_many(
            where=where_clause,
            skip=skip,
            take=pageSize,
            order={"createdAt": "desc"},
        )

        return FeedbackListResponse(
            feedback=[FeedbackResponse.model_validate(f) for f in feedback_list],
            total=total,
            page=page,
            pageSize=pageSize,
            hasMore=skip + len(feedback_list) < total,
        )
    except Exception as e:
        logger.error(f"Error listing feedback: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list feedback",
        )


@router.get("/{feedback_id}", response_model=FeedbackResponse)
async def get_feedback(
    feedback_id: str,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Get a specific feedback entry.
    Users can only access their own feedback.
    Admins can access any feedback.
    """
    try:
        feedback = await db.feedback.find_unique(where={"id": feedback_id})

        if not feedback:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback not found",
            )

        # Check permissions
        if current_user.role != "ADMIN" and feedback.userId != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to access this feedback",
            )

        return FeedbackResponse.model_validate(feedback)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting feedback: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get feedback",
        )


@router.patch("/{feedback_id}", response_model=FeedbackResponse)
async def update_feedback(
    feedback_id: str,
    feedback_update: FeedbackUpdate,
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Update feedback (admin only for status/adminNotes, users can update their own).
    """
    try:
        feedback = await db.feedback.find_unique(where={"id": feedback_id})

        if not feedback:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback not found",
            )

        # Check permissions - only admins can update status/adminNotes
        if feedback_update.status or feedback_update.adminNotes:
            if current_user.role != "ADMIN":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only admins can update feedback status and admin notes",
                )

        # Build update data
        update_data = {}
        if feedback_update.status:
            update_data["status"] = feedback_update.status
            if feedback_update.status == "RESOLVED":
                from datetime import datetime

                update_data["resolvedAt"] = datetime.utcnow()
        if feedback_update.adminNotes is not None:
            update_data["adminNotes"] = feedback_update.adminNotes

        # Update feedback
        updated_feedback = await db.feedback.update(
            where={"id": feedback_id},
            data=update_data,
        )

        logger.info(f"Feedback updated: {feedback_id} by user {current_user.id}")

        return FeedbackResponse.model_validate(updated_feedback)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating feedback: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update feedback",
        )
