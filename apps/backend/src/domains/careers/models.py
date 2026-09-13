"""Careers domain — Pydantic request/response schemas (camelCase to match the client contract)."""

from datetime import datetime

from pydantic import BaseModel

CAREER_APPLICATION_STATUSES = frozenset({"NEW", "REVIEWED", "ARCHIVED"})


# ---------------------------------------------------------------------------
# Job postings
# ---------------------------------------------------------------------------


class JobPostingResponse(BaseModel):
    id: str
    slug: str
    title: str
    location: str
    type: str
    stage: str
    description: str
    responsibilities: list[str] = []
    requirementsMustHave: list[str] = []
    requirementsNiceToHave: list[str] = []
    successMetrics: list[str] = []
    whyRoleMatters: list[str] = []
    compensation: list[str] = []
    published: bool
    sortOrder: int
    createdAt: datetime
    updatedAt: datetime


class JobPostingCreateRequest(BaseModel):
    slug: str
    title: str
    location: str
    type: str
    stage: str
    description: str
    responsibilities: list[str] = []
    requirementsMustHave: list[str] = []
    requirementsNiceToHave: list[str] = []
    successMetrics: list[str] = []
    whyRoleMatters: list[str] = []
    compensation: list[str] = []
    published: bool | None = None
    sortOrder: int | None = None


class JobPostingUpdateRequest(BaseModel):
    slug: str | None = None
    title: str | None = None
    location: str | None = None
    type: str | None = None
    stage: str | None = None
    description: str | None = None
    responsibilities: list[str] | None = None
    requirementsMustHave: list[str] | None = None
    requirementsNiceToHave: list[str] | None = None
    successMetrics: list[str] | None = None
    whyRoleMatters: list[str] | None = None
    compensation: list[str] | None = None
    published: bool | None = None
    sortOrder: int | None = None


# ---------------------------------------------------------------------------
# Career applications
# ---------------------------------------------------------------------------


class CareerApplicationResponse(BaseModel):
    id: str
    jobId: str
    jobTitle: str
    firstName: str
    lastName: str
    email: str
    linkedinUrl: str
    portfolioUrl: str | None = None
    coverLetter: str
    status: str
    adminNotes: str | None = None
    createdAt: datetime
    updatedAt: datetime


class CareerApplicationListResponse(BaseModel):
    applications: list[CareerApplicationResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class CareerApplicationCreateRequest(BaseModel):
    """Public application submission."""

    jobId: str
    firstName: str
    lastName: str
    email: str
    linkedinUrl: str
    coverLetter: str
    portfolioUrl: str | None = None


class CareerApplicationUpdateRequest(BaseModel):
    status: str | None = None
    adminNotes: str | None = None
