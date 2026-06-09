"""
Pydantic models for Circle Knowledge Base management.

Curriculum, materials, knowledge links, and progress tracking.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Curriculum Request Models ---


class CurriculumCreate(BaseModel):
    """Schema for creating a curriculum."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    coverUrl: str | None = None
    status: str = Field(default="DRAFT", description="DRAFT or PUBLISHED")


class CurriculumUpdate(BaseModel):
    """Schema for updating a curriculum."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    coverUrl: str | None = None
    status: str | None = Field(None, description="DRAFT, PUBLISHED, or ARCHIVED")
    order: float | None = None


class CurriculumSectionCreate(BaseModel):
    """Schema for creating a curriculum section."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    objectives: list[str] | None = None
    estimatedMinutes: int = Field(default=30, ge=1, le=600)
    materialIds: list[str] | None = None  # Optional materials to link


class CurriculumSectionUpdate(BaseModel):
    """Schema for updating a curriculum section."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    objectives: list[str] | None = None
    estimatedMinutes: int | None = Field(None, ge=1, le=600)
    order: float | None = None


# --- Material Request Models ---


class MaterialCreate(BaseModel):
    """Schema for creating/uploading a material."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    type: str = Field(..., description="DOCUMENT, LINK, VIDEO, AUDIO, or IMAGE")
    fileUrl: str | None = None
    fileSize: int | None = None
    mimeType: str | None = None
    externalUrl: str | None = None
    folder: str | None = Field(None, max_length=200)


class MaterialUpdate(BaseModel):
    """Schema for updating material metadata."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    folder: str | None = Field(None, max_length=200)


# --- Knowledge Link Request Models ---


class KnowledgeLinkCreate(BaseModel):
    """Schema for creating a knowledge link."""

    # Source (one of these must be provided)
    curriculumId: str | None = None
    sectionId: str | None = None
    materialId: str | None = None

    # Target (one of these must be provided)
    chatGroupId: str | None = None
    sessionId: str | None = None


# --- Response Models ---


class MaterialResponse(BaseModel):
    """Response schema for a material."""

    id: str
    circleId: str
    title: str
    description: str | None = None
    type: str
    fileUrl: str | None = None
    fileSize: int | None = None
    mimeType: str | None = None
    externalUrl: str | None = None
    isIndexed: bool = False
    folder: str | None = None
    accessCount: int = 0
    uploadedById: str
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CurriculumSectionResponse(BaseModel):
    """Response schema for a curriculum section."""

    id: str
    curriculumId: str
    title: str
    description: str | None = None
    objectives: list[str] | None = None
    estimatedMinutes: int = 30
    order: float = 0
    materials: list[MaterialResponse] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CurriculumResponse(BaseModel):
    """Response schema for a curriculum."""

    id: str
    circleId: str
    title: str
    description: str | None = None
    coverUrl: str | None = None
    status: str
    order: float = 0
    createdById: str
    sectionCount: int = 0
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CurriculumDetailResponse(BaseModel):
    """Detailed response with sections included."""

    id: str
    circleId: str
    title: str
    description: str | None = None
    coverUrl: str | None = None
    status: str
    order: float = 0
    createdById: str
    sections: list[CurriculumSectionResponse] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeLinkResponse(BaseModel):
    """Response schema for a knowledge link."""

    id: str
    circleId: str
    curriculumId: str | None = None
    sectionId: str | None = None
    materialId: str | None = None
    chatGroupId: str | None = None
    sessionId: str | None = None
    # Include source item info
    sourceTitle: str | None = None
    sourceType: str | None = None  # "curriculum", "section", "material"
    createdById: str
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class CurriculumProgressResponse(BaseModel):
    """Response schema for curriculum progress."""

    curriculumId: str
    userId: str
    completedSections: int = 0
    totalSections: int = 0
    percentage: float = 0.0
    completedAt: datetime | None = None
    startedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class SectionProgressResponse(BaseModel):
    """Response schema for section progress."""

    sectionId: str
    userId: str
    completed: bool = False
    completedAt: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
