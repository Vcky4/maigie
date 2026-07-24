"""
Classrooms domain — Pydantic request/response schemas.

A Classroom is the operational unit of collaborative learning.
It exists within a Learning Space around a shared learning objective.
Contains discussions, learning sessions, assigned courses, and announcements.

Note: In the current DB schema, Classrooms map to CircleChatGroup (discussions)
and CircleSession (sessions). The full Classroom entity will be introduced
in the Prisma schema migration (Phase 10).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Classroom (future entity — currently mapped to ChatGroup + metadata)
# ===========================================================================


class ClassroomCreate(BaseModel):
    """Create a Classroom within a Learning Space."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    visibility: str = Field(default="PUBLIC", description="PUBLIC or PRIVATE")
    memberIds: list[str] | None = None  # For PRIVATE: initial member IDs


class ClassroomUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    visibility: str | None = None


class ClassroomResponse(BaseModel):
    """A Classroom within a Learning Space."""

    id: str
    spaceId: str
    name: str
    description: str | None = None
    visibility: str = "PUBLIC"
    chatSessionId: str | None = None  # Linked AI conversation
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Learning Sessions (group study, live class, voice, workshop)
# ===========================================================================


class SessionCreate(BaseModel):
    """Schedule a Learning Session within a Classroom."""

    title: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    scheduledAt: datetime
    duration: int = Field(60, ge=5, le=480, description="Duration in minutes")
    classroomId: str = Field(..., description="Classroom (chat group) this session belongs to")
    topicId: str | None = None
    goalId: str | None = None


class SessionUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    scheduledAt: datetime | None = None
    duration: int | None = None
    status: Literal["SCHEDULED", "ACTIVE", "COMPLETED", "CANCELLED"] | None = None
    classroomId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class SessionResponse(BaseModel):
    """A Learning Session within a Classroom."""

    id: str
    spaceId: str
    classroomId: str | None = None
    title: str
    description: str | None = None
    scheduledAt: datetime
    duration: int
    status: str = "SCHEDULED"
    topicId: str | None = None
    goalId: str | None = None
    createdById: str
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionSuggestion(BaseModel):
    """AI-suggested learning session."""

    title: str
    description: str
    duration: int
    reason: str


class SessionSuggestionResponse(BaseModel):
    suggestions: list[SessionSuggestion]


# ===========================================================================
# Discussions (messages within a Classroom)
# ===========================================================================


class DiscussionMessageResponse(BaseModel):
    """A message within a Classroom discussion."""

    id: str
    userId: str
    userName: str | None = None
    content: str
    replyToId: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Assigned Courses (courses linked to a Classroom)
# ===========================================================================


class AssignCourseRequest(BaseModel):
    """Assign a course to a Classroom for collaborative study."""

    courseId: str


class AssignedCourseResponse(BaseModel):
    """A course assigned to a Classroom."""

    id: str
    courseId: str
    courseTitle: str
    progress: float = 0.0
    assignedAt: datetime

    model_config = ConfigDict(from_attributes=True)
