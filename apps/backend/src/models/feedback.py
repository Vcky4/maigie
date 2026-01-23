"""
Feedback models for request/response schemas.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    """Schema for creating feedback."""

    type: Literal[
        "BUG_REPORT",
        "FEATURE_REQUEST",
        "GENERAL_FEEDBACK",
        "UI_UX_FEEDBACK",
        "PERFORMANCE_ISSUE",
        "OTHER",
    ] = Field(..., description="Type of feedback")
    title: str = Field(..., min_length=1, max_length=200, description="Brief title")
    description: str = Field(..., min_length=1, max_length=5000, description="Detailed description")
    pageUrl: str | None = Field(
        None, max_length=500, description="URL where feedback was submitted"
    )
    metadata: dict | None = Field(None, description="Additional context/metadata")


class FeedbackUpdate(BaseModel):
    """Schema for updating feedback (admin only)."""

    status: Literal["PENDING", "REVIEWED", "RESOLVED", "ARCHIVED"] | None = None
    adminNotes: str | None = Field(None, max_length=2000, description="Admin notes/response")


class FeedbackResponse(BaseModel):
    """Schema for feedback response."""

    id: str
    userId: str | None
    type: str
    title: str
    description: str
    status: str
    pageUrl: str | None
    metadata: dict | None
    adminNotes: str | None
    resolvedAt: str | None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class FeedbackListResponse(BaseModel):
    """Schema for paginated feedback list response."""

    feedback: list[FeedbackResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool
