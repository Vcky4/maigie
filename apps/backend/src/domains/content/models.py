"""Content domain — Pydantic schemas (camelCase to match the client contract)."""

from datetime import datetime

from pydantic import BaseModel


class BlogPostResponse(BaseModel):
    id: str
    slug: str
    title: str
    description: str
    content: str
    authorName: str
    authorRole: str | None = None
    publishedAt: datetime
    tags: list[str] = []
    category: str
    coverImage: str | None = None
    readTime: int | None = None
    featured: bool
    published: bool
    createdAt: datetime
    updatedAt: datetime


class BlogPostCreateRequest(BaseModel):
    slug: str
    title: str
    description: str
    content: str
    authorName: str
    authorRole: str | None = None
    publishedAt: datetime
    tags: list[str] = []
    category: str
    coverImage: str | None = None
    readTime: int | None = None
    featured: bool | None = None
    published: bool | None = None


class BlogPostUpdateRequest(BaseModel):
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    content: str | None = None
    authorName: str | None = None
    authorRole: str | None = None
    publishedAt: datetime | None = None
    tags: list[str] | None = None
    category: str | None = None
    coverImage: str | None = None
    readTime: int | None = None
    featured: bool | None = None
    published: bool | None = None


# ---------------------------------------------------------------------------
# Content calendar
# ---------------------------------------------------------------------------


class ContentCalendarEntryResponse(BaseModel):
    id: str
    topic: str
    keywords: list[str] = []
    category: str
    scheduledDate: datetime
    status: str
    blogPostId: str | None = None
    coverImageUrl: str | None = None
    notes: str | None = None
    autoPublish: bool
    errorMessage: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ContentCalendarEntryCreateRequest(BaseModel):
    topic: str
    keywords: list[str] = []
    category: str | None = None
    scheduledDate: datetime
    autoPublish: bool | None = None
    notes: str | None = None


class ContentCalendarEntryUpdateRequest(BaseModel):
    topic: str | None = None
    keywords: list[str] | None = None
    category: str | None = None
    scheduledDate: datetime | None = None
    status: str | None = None
    autoPublish: bool | None = None
    notes: str | None = None
    coverImageUrl: str | None = None
