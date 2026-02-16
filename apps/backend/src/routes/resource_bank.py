"""
Resource Bank routes.

Copyright (C) 2026 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.resource_bank import (
    ResourceBankItemResponse,
    ResourceBankItemSummary,
    ResourceBankListResponse,
    ResourceBankMyUploadResponse,
    ResourceBankReportRequest,
    ResourceUploadRewardClaimResponse,
    ResourceUploadRewardResponse,
)
from src.services import resource_bank_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/resource-bank", tags=["resource-bank"])


@router.post("/upload")
async def upload_resource(
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    title: Annotated[str, Form(min_length=1, max_length=300)],
    universityName: Annotated[str, Form(min_length=1, max_length=300)],
    type: Annotated[str, Form()] = "OTHER",
    description: Annotated[str | None, Form(max_length=2000)] = None,
    courseName: Annotated[str | None, Form(max_length=300)] = None,
    courseCode: Annotated[str | None, Form(max_length=50)] = None,
    files: list[UploadFile] = File(...),
):
    """
    Upload a resource to the resource bank.

    Accepts multipart form data with metadata fields and one or more files.
    The resource is created with PENDING_REVIEW status and sent for AI moderation.
    """
    try:
        if not files or len(files) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one file is required",
            )

        result = await resource_bank_service.upload_resource(
            user=current_user,
            title=title,
            university_name=universityName,
            files=files,
            description=description,
            resource_type=type,
            course_name=courseName,
            course_code=courseCode,
        )

        # Trigger AI moderation in background
        background_tasks.add_task(resource_bank_service.moderate_resource, result["id"])

        return result

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error uploading resource",
            extra={"user_id": current_user.id, "title": title, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload resource",
        )


@router.get("", response_model=ResourceBankListResponse)
async def browse_resources(
    current_user: CurrentUser,
    universityName: str | None = Query(None, description="Filter by university name"),
    courseCode: str | None = Query(None, description="Filter by course code"),
    type: str | None = Query(None, description="Filter by resource type"),
    search: str | None = Query(None, max_length=255, description="Search text"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sortBy: str = Query("createdAt", pattern="^(createdAt|downloadCount|viewCount)$"),
    sortOrder: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    Browse approved resources in the resource bank.

    If no universityName is provided and the user has a universityName on their profile,
    it defaults to filtering by the user's university.
    """
    try:
        # Default to user's university if not specified
        effective_university = universityName
        if effective_university is None and hasattr(current_user, "universityName"):
            effective_university = getattr(current_user, "universityName", None)

        result = await resource_bank_service.browse_resource_bank(
            university_name=effective_university,
            course_code=courseCode,
            resource_type=type,
            search=search,
            page=page,
            page_size=pageSize,
            sort_by=sortBy,
            sort_order=sortOrder,
        )

        return ResourceBankListResponse(
            items=[ResourceBankItemSummary(**item) for item in result["items"]],
            total=result["total"],
            page=result["page"],
            pageSize=result["pageSize"],
            hasMore=result["hasMore"],
        )

    except Exception as e:
        logger.error(
            "Error browsing resource bank",
            extra={"user_id": current_user.id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to browse resource bank",
        )


@router.get("/my-uploads")
async def get_my_uploads(current_user: CurrentUser):
    """Get all resources uploaded by the current user."""
    try:
        uploads = await resource_bank_service.get_my_uploads(current_user)
        return {"uploads": uploads}
    except Exception as e:
        logger.error(
            "Error fetching user uploads",
            extra={"user_id": current_user.id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch uploads",
        )


@router.get("/rewards")
async def get_upload_rewards(current_user: CurrentUser):
    """Get all claimable upload rewards for the current user."""
    try:
        rewards = await resource_bank_service.get_claimable_upload_rewards(current_user)
        return {"rewards": rewards}
    except Exception as e:
        logger.error(
            "Error fetching upload rewards",
            extra={"user_id": current_user.id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch rewards",
        )


@router.post("/rewards/{reward_id}/claim", response_model=ResourceUploadRewardClaimResponse)
async def claim_upload_reward(reward_id: str, current_user: CurrentUser):
    """Claim an upload reward to increase daily credit limit."""
    try:
        result = await resource_bank_service.claim_upload_reward(current_user, reward_id)
        return ResourceUploadRewardClaimResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            "Error claiming upload reward",
            extra={"user_id": current_user.id, "reward_id": reward_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim reward",
        )


@router.get("/{item_id}")
async def get_resource_detail(item_id: str, current_user: CurrentUser):
    """Get full details of a resource bank item including files."""
    try:
        detail = await resource_bank_service.get_resource_bank_detail(item_id)
        if not detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found",
            )
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error fetching resource detail",
            extra={"user_id": current_user.id, "item_id": item_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch resource detail",
        )


@router.get("/{item_id}/download/{file_id}")
async def download_file(item_id: str, file_id: str, current_user: CurrentUser):
    """Get file download URL and increment download count."""
    try:
        result = await resource_bank_service.download_file(item_id, file_id)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error downloading file",
            extra={
                "user_id": current_user.id,
                "item_id": item_id,
                "file_id": file_id,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download file",
        )


@router.post("/{item_id}/report")
async def report_resource(
    item_id: str,
    request: ResourceBankReportRequest,
    current_user: CurrentUser,
):
    """Report/flag a resource bank item."""
    try:
        # Verify resource exists and is approved
        item = await db.resourcebankitem.find_unique(where={"id": item_id})
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found",
            )

        result = await resource_bank_service.report_resource(
            reporter_id=current_user.id,
            item_id=item_id,
            reason=request.reason,
            description=request.description,
        )
        return result

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error reporting resource",
            extra={"user_id": current_user.id, "item_id": item_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to report resource",
        )
