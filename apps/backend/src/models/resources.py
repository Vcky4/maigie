"""
Resource models for request/response schemas.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResourceType(str):
    """Resource type enum values."""

    VIDEO = "VIDEO"
    ARTICLE = "ARTICLE"
    BOOK = "BOOK"
    COURSE = "COURSE"
    DOCUMENT = "DOCUMENT"
    WEBSITE = "WEBSITE"
    PODCAST = "PODCAST"
    OTHER = "OTHER"


class ResourceCreate(BaseModel):
    """Schema for creating a new resource."""

    title: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1)
    description: str | None = Field(None, max_length=1000)
    type: str = Field(default="OTHER")
    metadata: dict[str, Any] | None = None
    isRecommended: bool = Field(default=False)
    recommendationScore: float | None = Field(None, ge=0.0, le=1.0)
    courseId: str | None = None
    topicId: str | None = None


class ResourceRecommendationRequest(BaseModel):
    """Schema for resource recommendation request."""

    query: str = Field(..., min_length=1, description="Search query or learning intent")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of recommendations")
    context: dict[str, Any] | None = Field(
        None, description="Additional context (courses, goals, recent activity)"
    )


class ResourceRecommendationItem(BaseModel):
    """Schema for a single resource recommendation."""

    title: str
    url: str
    description: str | None = None
    type: str
    relevance: str | None = None
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance score")


class ResourceRecommendationResponse(BaseModel):
    """Schema for resource recommendation response."""

    recommendations: list[ResourceRecommendationItem]
    query: str
    personalized: bool = Field(default=True, description="Whether recommendations are personalized")


class ResourceResponse(BaseModel):
    """Schema for resource response."""

    id: str
    userId: str
    title: str
    url: str
    description: str | None
    type: str
    metadata: dict[str, Any] | None
    isRecommended: bool
    recommendationScore: float | None
    recommendationSource: str | None
    clickCount: int
    bookmarkCount: int
    lastAccessedAt: str | None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)


class ResourceListResponse(BaseModel):
    """Schema for paginated resource list response."""

    resources: list[ResourceResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool
