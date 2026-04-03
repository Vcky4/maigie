"""
Pydantic models for marketing CMS (blog posts and job postings).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BlogPostPublic(BaseModel):
    """Public blog shape aligned with the marketing site."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    description: str
    content: str
    authorName: str
    authorRole: str | None = None
    publishedAt: datetime
    tags: list[str]
    category: str
    coverImage: str | None = None
    readTime: int | None = None
    featured: bool = False


class BlogPostAdminCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=2000)
    content: str = Field(..., min_length=1)
    authorName: str = Field(..., min_length=1, max_length=200)
    authorRole: str | None = Field(None, max_length=200)
    publishedAt: datetime
    tags: list[str] = Field(default_factory=list)
    category: str = Field(..., min_length=1, max_length=120)
    coverImage: str | None = Field(None, max_length=2000)
    readTime: int | None = Field(None, ge=1, le=600)
    featured: bool = False
    published: bool = False


class BlogPostAdminUpdate(BaseModel):
    slug: str | None = Field(None, min_length=1, max_length=200)
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, min_length=1, max_length=2000)
    content: str | None = Field(None, min_length=1)
    authorName: str | None = Field(None, min_length=1, max_length=200)
    authorRole: str | None = Field(None, max_length=200)
    publishedAt: datetime | None = None
    tags: list[str] | None = None
    category: str | None = Field(None, min_length=1, max_length=120)
    coverImage: str | None = Field(None, max_length=2000)
    readTime: int | None = Field(None, ge=1, le=600)
    featured: bool | None = None
    published: bool | None = None


class JobPostingPublic(BaseModel):
    """Public job shape aligned with the marketing careers site."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    location: str
    type: str
    stage: str
    description: str
    responsibilities: list[str]
    requirementsMustHave: list[str]
    requirementsNiceToHave: list[str]
    successMetrics: list[str]
    whyRoleMatters: list[str]
    compensation: list[str]


class JobPostingAdminCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=300)
    location: str = Field(..., max_length=200)
    type: str = Field(..., max_length=120)
    stage: str = Field(..., max_length=200)
    description: str = Field(..., min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    requirementsMustHave: list[str] = Field(default_factory=list)
    requirementsNiceToHave: list[str] = Field(default_factory=list)
    successMetrics: list[str] = Field(default_factory=list)
    whyRoleMatters: list[str] = Field(default_factory=list)
    compensation: list[str] = Field(default_factory=list)
    published: bool = True
    sortOrder: int = 0


class JobPostingAdminUpdate(BaseModel):
    slug: str | None = Field(None, min_length=1, max_length=120)
    title: str | None = Field(None, min_length=1, max_length=300)
    location: str | None = Field(None, max_length=200)
    type: str | None = Field(None, max_length=120)
    stage: str | None = Field(None, max_length=200)
    description: str | None = Field(None, min_length=1)
    responsibilities: list[str] | None = None
    requirementsMustHave: list[str] | None = None
    requirementsNiceToHave: list[str] | None = None
    successMetrics: list[str] | None = None
    whyRoleMatters: list[str] | None = None
    compensation: list[str] | None = None
    published: bool | None = None
    sortOrder: int | None = None


class BlogPostAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    title: str
    description: str
    content: str
    authorName: str
    authorRole: str | None
    publishedAt: datetime
    tags: list[str]
    category: str
    coverImage: str | None
    readTime: int | None
    featured: bool
    published: bool
    createdAt: datetime
    updatedAt: datetime


class JobPostingAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    title: str
    location: str
    type: str
    stage: str
    description: str
    responsibilities: list[str]
    requirementsMustHave: list[str]
    requirementsNiceToHave: list[str]
    successMetrics: list[str]
    whyRoleMatters: list[str]
    compensation: list[str]
    published: bool
    sortOrder: int
    createdAt: datetime
    updatedAt: datetime


class StaffMemberResponse(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    adminStaffRole: str
    isActive: bool


class StaffMemberUpdate(BaseModel):
    adminStaffRole: str = Field(..., description="SUPER_ADMIN or CONTENT_MANAGER")
