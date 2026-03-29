"""
Request/response models for Studio in-app page preview (reader mode).

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, HttpUrl, model_validator


class PagePreviewRequest(BaseModel):
    """Ask the backend to fetch and sanitize a public page for safe HTML display."""

    url: HttpUrl = Field(..., description="HTTPS (or HTTP) page URL to preview")


class PagePreviewResponse(BaseModel):
    """Sanitized HTML fragment or PDF (base64) plus metadata for Studio reader view."""

    originalUrl: str
    title: str | None = None
    contentType: Literal["text/html", "application/pdf"] = "text/html"
    html: str | None = Field(
        default=None,
        description="Sanitized HTML body fragment when contentType is text/html",
    )
    pdfBase64: str | None = Field(
        default=None,
        description="PDF bytes as standard base64 when contentType is application/pdf",
    )

    @model_validator(mode="after")
    def _one_payload(self) -> Self:
        if self.contentType == "text/html":
            if self.html is None or not str(self.html).strip():
                raise ValueError("html is required when contentType is text/html")
            if self.pdfBase64 is not None:
                raise ValueError("pdfBase64 must be omitted when contentType is text/html")
        else:
            if self.pdfBase64 is None or not str(self.pdfBase64).strip():
                raise ValueError("pdfBase64 is required when contentType is application/pdf")
            if self.html is not None:
                raise ValueError("html must be omitted when contentType is application/pdf")
        return self
