"""
API routes for file uploads (CDN storage).
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from src.dependencies import CurrentUser, DBDep
from src.services.storage_service import storage_service
from src.services.usage_tracking_service import increment_feature_usage
from src.utils.exceptions import SubscriptionLimitError

router = APIRouter(tags=["upload"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def upload_file(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    path: str = Query("uploads", description="Optional subdirectory path for file storage"),
    db: DBDep = None,
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
        # Check file upload limit for FREE tier users
        if db:
            await increment_feature_usage(current_user, "file_uploads", db_client=db)

        result = await storage_service.upload_file(file, path=path)
        return result
    except SubscriptionLimitError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}",
        )
