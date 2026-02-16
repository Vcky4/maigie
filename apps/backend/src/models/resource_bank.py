"""
Resource Bank models for request/response schemas.

Copyright (C) 2026 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------
# Request Models
# --------------------------------------------------


class ResourceBankUploadRequest(BaseModel):
    """Metadata fields sent alongside the multipart file upload."""

    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=2000)
    type: str = Field(
        default="OTHER",
        description="One of: TEXTBOOK, NOTE, ASSIGNMENT, PAST_QUESTION, SLIDES, SUMMARY, LAB_REPORT, OTHER",
    )
    universityName: str = Field(..., min_length=1, max_length=300)
    courseName: str | None = Field(None, max_length=300)
    courseCode: str | None = Field(None, max_length=50)


class ResourceBankBrowseParams(BaseModel):
    """Query parameters for browsing the resource bank."""

    universityName: str | None = None
    courseCode: str | None = None
    type: str | None = None
    search: str | None = Field(None, max_length=255)
    page: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    sortBy: str = Field("createdAt", pattern="^(createdAt|downloadCount|viewCount)$")
    sortOrder: str = Field("desc", pattern="^(asc|desc)$")


class ResourceBankReportRequest(BaseModel):
    """Request body to report/flag a resource."""

    reason: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)


# --------------------------------------------------
# Response Models
# --------------------------------------------------


class ResourceBankFileResponse(BaseModel):
    """A single file attached to a resource bank item."""

    id: str
    filename: str
    url: str
    size: int | None = None
    mimeType: str | None = None
    createdAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceBankItemResponse(BaseModel):
    """Full resource bank item response (for detail view)."""

    id: str
    uploaderId: str
    uploaderName: str | None = None
    title: str
    description: str | None = None
    type: str
    universityName: str
    courseName: str | None = None
    courseCode: str | None = None
    status: str
    downloadCount: int
    viewCount: int
    fileCount: int = 0
    files: list[ResourceBankFileResponse] = []
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceBankItemSummary(BaseModel):
    """Compact resource bank item for list/browse views."""

    id: str
    uploaderName: str | None = None
    title: str
    description: str | None = None
    type: str
    universityName: str
    courseName: str | None = None
    courseCode: str | None = None
    downloadCount: int
    viewCount: int
    fileCount: int = 0
    createdAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceBankListResponse(BaseModel):
    """Paginated list of resource bank items."""

    items: list[ResourceBankItemSummary]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class ResourceBankMyUploadResponse(BaseModel):
    """User's own upload with moderation status."""

    id: str
    title: str
    description: str | None = None
    type: str
    universityName: str
    courseName: str | None = None
    courseCode: str | None = None
    status: str
    moderationNotes: str | None = None
    downloadCount: int
    viewCount: int
    fileCount: int = 0
    createdAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceUploadRewardResponse(BaseModel):
    """A claimable upload reward."""

    id: str
    resourceBankItemId: str
    resourceTitle: str | None = None
    tokens: int
    isClaimed: bool
    claimedAt: str | None = None
    createdAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceUploadRewardClaimResponse(BaseModel):
    """Response after claiming an upload reward."""

    rewardId: str
    tokensClaimed: int
    claimDate: str
    dailyLimitIncrease: int
