"""
Studio workspace API routes.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import base64
import logging

from fastapi import APIRouter, HTTPException, status

from src.dependencies import CurrentUser
from src.models.studio_preview import PagePreviewRequest, PagePreviewResponse
from src.services.studio_page_preview_service import fetch_studio_preview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])


@router.post("/page-preview", response_model=PagePreviewResponse)
async def post_page_preview(
    body: PagePreviewRequest,
    current_user: CurrentUser,
) -> PagePreviewResponse:
    """
    Fetch a public HTML page and return sanitized content for in-app “reader” preview.

    For HTML, returns sanitized fragment (no iframe). For PDF, returns base64 so the client
    can open a blob URL (avoids X-Frame-Options on third-party file hosts). Not a full browser.
    """
    url_str = str(body.url)
    try:
        content_type, title, html, pdf_bytes = await fetch_studio_preview(url_str)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning(
            "Studio page preview failed",
            extra={
                "user_id": current_user.id,
                "url": url_str[:120],
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception(
            "Studio page preview unexpected error",
            extra={"user_id": current_user.id, "url": url_str[:120]},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build preview",
        ) from e

    if content_type == "application/pdf" and pdf_bytes is not None:
        return PagePreviewResponse(
            originalUrl=url_str,
            title=title,
            contentType="application/pdf",
            html=None,
            pdfBase64=base64.standard_b64encode(pdf_bytes).decode("ascii"),
        )

    assert html is not None
    return PagePreviewResponse(
        originalUrl=url_str,
        title=title,
        contentType="text/html",
        html=html,
        pdfBase64=None,
    )
