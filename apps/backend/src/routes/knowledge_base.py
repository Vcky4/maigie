"""
Knowledge Base API Routes.

Allows users to view and manage their uploaded files (knowledge base).
Mounted at /api/v1/knowledge-base.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.core.database import db
from src.dependencies import CurrentUser
from src.services.knowledge_base_service import (
    delete_user_upload,
    get_upload_stats,
    get_user_uploads,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=dict)
async def list_uploads(current_user: CurrentUser):
    """List all knowledge base uploads for the current user with stats."""
    uploads = await get_user_uploads(current_user.id)
    stats = await get_upload_stats(current_user.id)
    return {"uploads": uploads, **stats}


@router.get("/stats", response_model=dict)
async def upload_stats(current_user: CurrentUser):
    """Quick stats endpoint for the settings badge."""
    return await get_upload_stats(current_user.id)


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_upload(upload_id: str, current_user: CurrentUser):
    """Delete a specific knowledge base upload."""
    deleted = await delete_user_upload(current_user.id, upload_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload not found",
        )
    logger.info("User %s deleted knowledge base upload %s", current_user.id, upload_id)
