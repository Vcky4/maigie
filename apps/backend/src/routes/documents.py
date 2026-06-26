"""
Document sharing routes.
Handles public share links for generated documents.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/share/{share_id}")
async def get_shared_document_preview(share_id: str):
    """
    Public route: Render the HTML preview for a shared document.
    This is the shareable link that renders the presentation/document in-browser.
    No authentication required — access controlled by isPublic flag.
    """
    doc = await db.generateddocument.find_unique(where={"shareId": share_id})

    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if not doc.isPublic:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This document is no longer shared.",
        )

    # Redirect to the HTML preview on CDN
    return RedirectResponse(url=doc.previewUrl, status_code=302)


@router.get("/share/{share_id}/download")
async def download_shared_document(
    share_id: str,
    token: str = Query(default=None, description="Auth token for premium download"),
):
    """
    Download the actual document file (PDF/DOCX/PPTX).
    Can be restricted to premium users in the future.
    For now, allows download if document is public.
    """
    doc = await db.generateddocument.find_unique(where={"shareId": share_id})

    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if not doc.isPublic:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This document is no longer shared.",
        )

    # TODO: Add premium check here when restricting downloads
    # if restrict_downloads_to_premium:
    #     user = verify_token(token)
    #     if user.tier == "FREE":
    #         raise HTTPException(403, "Upgrade to download documents")

    return RedirectResponse(url=doc.fileUrl, status_code=302)


@router.get("/share/{share_id}/meta")
async def get_shared_document_metadata(share_id: str):
    """
    Get metadata for a shared document (for link previews, OG tags, etc.)
    """
    doc = await db.generateddocument.find_unique(where={"shareId": share_id})

    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if not doc.isPublic:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not shared.")

    return {
        "title": doc.title,
        "format": doc.format,
        "size": doc.size,
        "previewUrl": doc.previewUrl,
        "downloadUrl": f"/api/documents/share/{share_id}/download",
        "createdAt": doc.createdAt.isoformat(),
    }
