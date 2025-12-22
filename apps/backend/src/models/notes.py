"""
Pydantic models for Note management.
"""

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class NoteTagResponse(BaseModel):
    id: str
    tag: str

    class Config:
        from_attributes = True


class NoteAttachmentCreate(BaseModel):
    filename: str
    url: str
    size: Optional[int] = None


class NoteAttachmentResponse(BaseModel):
    id: str
    filename: str
    url: str
    size: Optional[int] = None
    createdAt: datetime

    class Config:
        from_attributes = True


class NoteBase(BaseModel):
    title: str
    content: Optional[str] = None
    summary: Optional[str] = None
    courseId: Optional[str] = None
    topicId: Optional[str] = None
    archived: bool = False
    voiceRecordingUrl: Optional[str] = None


class NoteCreate(NoteBase):
    tags: Optional[List[str]] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    courseId: Optional[str] = None
    topicId: Optional[str] = None
    archived: Optional[bool] = None
    voiceRecordingUrl: Optional[str] = None
    tags: Optional[List[str]] = None


class NoteResponse(NoteBase):
    id: str
    userId: str
    tags: Optional[List[NoteTagResponse]] = []
    attachments: Optional[List[NoteAttachmentResponse]] = []
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    items: List[NoteResponse]
    total: int
    page: int
    size: int
    pages: int
