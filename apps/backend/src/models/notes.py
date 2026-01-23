"""
Pydantic models for Note management.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NoteTagResponse(BaseModel):
    id: str
    tag: str

    class Config:
        from_attributes = True


class NoteAttachmentCreate(BaseModel):
    filename: str
    url: str
    size: int | None = None


class NoteAttachmentResponse(BaseModel):
    id: str
    filename: str
    url: str
    size: int | None = None
    createdAt: datetime

    class Config:
        from_attributes = True


class NoteBase(BaseModel):
    title: str
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool = False
    voiceRecordingUrl: str | None = None


class NoteCreate(NoteBase):
    tags: list[str] | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool | None = None
    voiceRecordingUrl: str | None = None
    tags: list[str] | None = None


class NoteResponse(NoteBase):
    id: str
    userId: str
    tags: list[NoteTagResponse] | None = []
    attachments: list[NoteAttachmentResponse] | None = []
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    items: list[NoteResponse]
    total: int
    page: int
    size: int
    pages: int
