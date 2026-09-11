"""Feedback domain — Pydantic request/response schemas.

Field names are camelCase to match the client contract (`admin.types.ts`).
"""

from datetime import datetime

from pydantic import BaseModel

FEEDBACK_TYPES = frozenset(
    {
        "BUG_REPORT",
        "FEATURE_REQUEST",
        "GENERAL_FEEDBACK",
        "UI_UX_FEEDBACK",
        "PERFORMANCE_ISSUE",
        "OTHER",
    }
)
FEEDBACK_STATUSES = frozenset({"PENDING", "REVIEWED", "RESOLVED", "ARCHIVED"})


class FeedbackResponse(BaseModel):
    id: str
    userId: str | None = None
    type: str
    title: str
    description: str
    status: str
    pageUrl: str | None = None
    metadata: dict | None = None
    adminNotes: str | None = None
    resolvedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime


class FeedbackListResponse(BaseModel):
    feedback: list[FeedbackResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class FeedbackCreateRequest(BaseModel):
    type: str
    title: str
    description: str
    pageUrl: str | None = None
    metadata: dict | None = None


class FeedbackUpdateRequest(BaseModel):
    status: str | None = None
    adminNotes: str | None = None
