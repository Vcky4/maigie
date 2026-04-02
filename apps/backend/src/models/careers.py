"""
Pydantic models for public career applications and admin responses.
"""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CareerApplicationCreate(BaseModel):
    jobId: str = Field(..., min_length=1, max_length=120)
    jobTitle: str = Field(..., min_length=1, max_length=300)
    firstName: str = Field(..., min_length=1, max_length=120)
    lastName: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    linkedinUrl: str = Field(..., min_length=4, max_length=2000)
    portfolioUrl: str | None = Field(None, max_length=2000)
    coverLetter: str = Field(..., min_length=10, max_length=50_000)


class CareerApplicationResponse(BaseModel):
    id: str
    jobId: str
    jobTitle: str
    firstName: str
    lastName: str
    email: str
    linkedinUrl: str
    portfolioUrl: str | None
    coverLetter: str
    status: str
    adminNotes: str | None
    createdAt: str
    updatedAt: str


class CareerApplicationListResponse(BaseModel):
    applications: list[CareerApplicationResponse]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class CareerApplicationAdminUpdate(BaseModel):
    status: Literal["NEW", "REVIEWED", "ARCHIVED"] | None = None
    adminNotes: str | None = Field(None, max_length=10_000)
