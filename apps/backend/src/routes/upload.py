"""
API routes for file uploads (CDN storage).
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from src.dependencies import CurrentUser
from src.services.storage_service import storage_service

router = APIRouter(tags=["upload"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def upload_file(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    path: str = Query("uploads", description="Optional subdirectory path for file storage"),
):
    """
    Upload a file to CDN storage.

    Args:
        file: The file to upload
        path: Optional subdirectory path (default: "uploads")

    Returns:
        dict: { "filename": str, "url": str, "size": int }
    """
    try:
        result = await storage_service.upload_file(file, path=path)
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}",
        )
