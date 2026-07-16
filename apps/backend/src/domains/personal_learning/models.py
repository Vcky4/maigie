"""
Personal Learning domain — Pydantic request/response schemas.

Covers notes, exam preparation, document generation, and study mode.
These are the learner's private artifacts.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Notes
# ===========================================================================


class NoteTagResponse(BaseModel):
    id: str
    tag: str

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool = False
    voiceRecordingUrl: str | None = None
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


class NoteResponse(BaseModel):
    id: str
    userId: str
    title: str
    content: str | None = None
    summary: str | None = None
    courseId: str | None = None
    topicId: str | None = None
    archived: bool = False
    voiceRecordingUrl: str | None = None
    tags: list[NoteTagResponse] = []
    attachments: list[NoteAttachmentResponse] = []
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class NoteListResponse(BaseModel):
    items: list[NoteResponse]
    total: int
    page: int
    size: int
    pages: int


class NoteImportRequest(BaseModel):
    """Import a personal note into a Learning Space."""

    spaceId: str


# ===========================================================================
# Exam Preparation
# ===========================================================================


class ExamPrepCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    exam_date: str = Field(..., description="ISO date string (e.g. 2025-03-15)")
    description: str | None = None


class ExamPrepUpdate(BaseModel):
    subject: str | None = None
    exam_date: str | None = None
    description: str | None = None
    status: str | None = None


class ExamPrepMaterialResponse(BaseModel):
    id: str
    filename: str
    url: str
    extractedText: str | None = None
    fileType: str | None = None
    size: int | None = None
    category: str = "OTHER"
    label: str | None = None
    createdAt: str


class MaterialUpdate(BaseModel):
    category: str | None = None
    label: str | None = None


class TopicUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class QuizStartRequest(BaseModel):
    mode: str = Field(
        ..., description="FULL_PRACTICE, WEAK_AREAS, TOPIC_FOCUS, PAST_PAPER_SIM, QUICK_REVIEW"
    )
    topic_id: str | None = None
    question_count: int | None = None


class AnswerSubmitRequest(BaseModel):
    question_id: str
    user_answer: str
    time_taken_seconds: int | None = None


class QuizCompleteRequest(BaseModel):
    duration_seconds: int | None = None


# ===========================================================================
# Document Generation
# ===========================================================================


class DocumentGenerateRequest(BaseModel):
    """Request to generate an academic document."""

    type: str = Field(..., description="essay, report, presentation, letter, cv")
    title: str = Field(..., min_length=1, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=5000)
    format: str = Field("pdf", description="pdf, docx, pptx")
    courseId: str | None = None
    topicId: str | None = None


class DocumentResponse(BaseModel):
    id: str
    userId: str
    title: str
    type: str
    format: str
    status: str
    downloadUrl: str | None = None
    previewUrl: str | None = None
    shareId: str | None = None
    isPublic: bool = False
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    pageSize: int


# ===========================================================================
# Generic
# ===========================================================================


class MessageResponse(BaseModel):
    message: str
