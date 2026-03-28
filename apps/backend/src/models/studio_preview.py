"""
Request/response models for Studio in-app page preview (reader mode).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from pydantic import BaseModel, Field, HttpUrl


class PagePreviewRequest(BaseModel):
    """Ask the backend to fetch and sanitize a public page for safe HTML display."""

    url: HttpUrl = Field(..., description="HTTPS (or HTTP) page URL to preview")


class PagePreviewResponse(BaseModel):
    """Sanitized HTML fragment plus metadata for Studio reader view."""

    originalUrl: str
    title: str | None = None
    html: str = Field(
        ...,
        description="Sanitized HTML body fragment safe for embedding in the client",
    )
