"""
Schedule models for request/response schemas.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScheduleCreate(BaseModel):
    """Schema for creating a new schedule block."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    startAt: datetime
    endAt: datetime
    recurringRule: str | None = Field(
        None,
        max_length=500,
        description="Recurring rule (e.g., 'DAILY', 'WEEKLY', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR')",
    )
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class ScheduleUpdate(BaseModel):
    """Schema for updating a schedule block."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    startAt: datetime | None = None
    endAt: datetime | None = None
    recurringRule: str | None = Field(
        None,
        max_length=500,
        description="Recurring rule (e.g., 'DAILY', 'WEEKLY', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR')",
    )
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None


class ScheduleResponse(BaseModel):
    """Schema for schedule block response."""

    id: str
    userId: str
    title: str
    description: str | None
    startAt: str
    endAt: str
    recurringRule: str | None
    courseId: str | None = None
    topicId: str | None = None
    goalId: str | None = None
    googleCalendarEventId: str | None = None
    googleCalendarSyncedAt: str | None = None
    createdAt: str
    updatedAt: str

    model_config = ConfigDict(from_attributes=True)
