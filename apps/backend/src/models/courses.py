"""
Course models (Pydantic schemas).

Copyright (C) 2025 Maigie
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict  # <--- Added ConfigDict here


class DifficultyLevel(str, Enum):
    """Course difficulty levels."""

    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    EXPERT = "EXPERT"


class AICourseRequest(BaseModel):
    """Schema for AI Course Generation request."""

    topic: str = Field(..., min_length=1, max_length=100)
    difficulty: DifficultyLevel = DifficultyLevel.BEGINNER


# ============================================================================
# Topic Models
# ============================================================================


class TopicCreate(BaseModel):
    """Schema for creating a new topic."""

    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)


class TopicUpdate(BaseModel):
    """Schema for updating a topic."""

    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    content: str | None = None
    estimatedHours: float | None = Field(None, ge=0)
    completed: bool | None = None


from ..models.notes import NoteResponse  # Import NoteResponse


class TopicResponse(BaseModel):
    """Schema for topic response."""

    id: str
    moduleId: str
    title: str
    order: float
    content: str | None
    completed: bool
    estimatedHours: float | None
    createdAt: datetime
    updatedAt: datetime
    note: NoteResponse | None = None  # Use NoteResponse model instead of dict

    # CHANGED: Replaced class Config with model_config
    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Module Models
# ============================================================================


class ModuleCreate(BaseModel):
    """Schema for creating a new module."""

    title: str = Field(..., min_length=1, max_length=255)
    order: float = Field(..., ge=0)
    description: str | None = None


class ModuleUpdate(BaseModel):
    """Schema for updating a module."""

    title: str | None = Field(None, min_length=1, max_length=255)
    order: float | None = Field(None, ge=0)
    description: str | None = None


class ModuleResponse(BaseModel):
    """Schema for module response with calculated completion."""

    id: str
    courseId: str
    title: str
    order: float
    description: str | None
    completed: bool  # Calculated field (not stored in DB)
    progress: float  # Percentage of completed topics
    topicCount: int
    completedTopicCount: int
    topics: list[TopicResponse]
    createdAt: datetime
    updatedAt: datetime

    # CHANGED: Replaced class Config with model_config
    model_config = ConfigDict(from_attributes=True)


class ModuleSummary(BaseModel):
    """Lightweight module summary without topics."""

    id: str
    title: str
    order: float
    completed: bool
    progress: float
    topicCount: int
    completedTopicCount: int


# ============================================================================
# Course Models
# ============================================================================


class CourseCreate(BaseModel):
    """Schema for creating a new course."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    isAIGenerated: bool = False


class CourseUpdate(BaseModel):
    """Schema for updating a course."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    difficulty: DifficultyLevel | None = None
    targetDate: datetime | None = None
    archived: bool | None = None


class CourseResponse(BaseModel):
    """Schema for detailed course response with all modules and topics."""

    id: str
    userId: str
    title: str
    description: str | None
    difficulty: DifficultyLevel | None
    targetDate: datetime | None
    isAIGenerated: bool
    archived: bool
    progress: float  # Overall course progress percentage
    totalTopics: int
    completedTopics: int
    modules: list[ModuleResponse]
    createdAt: datetime
    updatedAt: datetime

    # CHANGED: Replaced class Config with model_config
    model_config = ConfigDict(from_attributes=True)


class CourseListItem(BaseModel):
    """Schema for course list item (lightweight, no modules/topics)."""

    id: str
    userId: str
    title: str
    description: str | None
    difficulty: DifficultyLevel | None
    targetDate: datetime | None
    isAIGenerated: bool
    archived: bool
    progress: float
    totalTopics: int
    completedTopics: int
    moduleCount: int
    createdAt: datetime
    updatedAt: datetime

    # CHANGED: Replaced class Config with model_config
    model_config = ConfigDict(from_attributes=True)


class CourseListResponse(BaseModel):
    """Schema for paginated course list response."""

    courses: list[CourseListItem]
    total: int
    page: int
    pageSize: int
    hasMore: bool


# ============================================================================
# Progress & Analytics Models
# ============================================================================


class ModuleProgress(BaseModel):
    """Progress details for a single module."""

    moduleId: str
    title: str
    order: float
    progress: float
    totalTopics: int
    completedTopics: int
    completed: bool


class ProgressResponse(BaseModel):
    """Detailed progress analytics for a course."""

    courseId: str
    overallProgress: float
    totalTopics: int
    completedTopics: int
    totalModules: int
    completedModules: int
    totalEstimatedHours: float
    completedEstimatedHours: float
    modules: list[ModuleProgress]


# ============================================================================
# Filter & Query Models
# ============================================================================


class CourseFilters(BaseModel):
    """Query parameters for filtering courses."""

    archived: bool | None = None
    difficulty: DifficultyLevel | None = None
    isAIGenerated: bool | None = None
    search: str | None = Field(None, max_length=255)
    page: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    sortBy: str = Field("createdAt", pattern="^(createdAt|updatedAt|title|progress)$")
    sortOrder: str = Field("desc", pattern="^(asc|desc)$")
