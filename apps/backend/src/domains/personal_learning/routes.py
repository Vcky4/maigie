"""
Personal Learning domain — API routes.

The learner's private environment: notes, exam prep, documents, study mode.

Mounted at: /api/v1/learning
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from prisma.models import User

from src.shared.auth import CurrentUser

from . import models
from .services import document_service, exam_prep_service, note_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["personal-learning"])


# ===========================================================================
# Notes
# ===========================================================================


@router.post("/notes", response_model=models.NoteResponse, status_code=201)
async def create_note(body: models.NoteCreate, current_user: CurrentUser):
    """Create a new note."""
    return await note_service.create_note(
        user_id=current_user.id, data=body.model_dump(exclude_unset=True)
    )


@router.get("/notes", response_model=models.NoteListResponse)
async def list_notes(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    tag: str | None = None,
    courseId: str | None = Query(None),
    topicId: str | None = Query(None),
    archived: bool | None = False,
    circleId: str | None = Query(None),
):
    """List notes with filtering and pagination."""
    items, total = await note_service.list_notes(
        user_id=current_user.id,
        page=page,
        size=size,
        search=search,
        tag=tag,
        course_id=courseId,
        topic_id=topicId,
        archived=archived,
        circle_id=circleId,
    )
    pages = (total + size - 1) // size
    return models.NoteListResponse(items=items, total=total, page=page, size=size, pages=pages)


@router.get("/notes/{note_id}", response_model=models.NoteResponse)
async def get_note(note_id: str, current_user: CurrentUser):
    """Get a note by ID."""
    return await note_service.get_note(user_id=current_user.id, note_id=note_id)


@router.put("/notes/{note_id}", response_model=models.NoteResponse)
async def update_note(note_id: str, body: models.NoteUpdate, current_user: CurrentUser):
    """Update a note."""
    return await note_service.update_note(
        user_id=current_user.id, note_id=note_id, data=body.model_dump(exclude_unset=True)
    )


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str, current_user: CurrentUser):
    """Delete a note."""
    success = await note_service.delete_note(user_id=current_user.id, note_id=note_id)
    if not success:
        raise HTTPException(status_code=404, detail="Note not found")


@router.post("/notes/{note_id}/archive", response_model=models.NoteResponse)
async def archive_note(note_id: str, current_user: CurrentUser):
    """Archive a note."""
    return await note_service.update_note(
        user_id=current_user.id, note_id=note_id, data={"archived": True}
    )


@router.post("/notes/{note_id}/unarchive", response_model=models.NoteResponse)
async def unarchive_note(note_id: str, current_user: CurrentUser):
    """Unarchive a note."""
    return await note_service.update_note(
        user_id=current_user.id, note_id=note_id, data={"archived": False}
    )


@router.post("/notes/{note_id}/retake", response_model=models.NoteResponse)
async def retake_note(note_id: str, current_user: CurrentUser):
    """Rewrite note content using AI for improved formatting."""
    return await note_service.retake_note(user_id=current_user.id, note_id=note_id)


@router.post("/notes/{note_id}/add-summary", response_model=models.NoteResponse)
async def add_summary(note_id: str, current_user: CurrentUser):
    """Generate AI summary for a note."""
    return await note_service.add_summary(user_id=current_user.id, note_id=note_id)


@router.post("/notes/{note_id}/import", response_model=models.NoteResponse)
async def import_to_space(note_id: str, body: models.NoteImportRequest, current_user: CurrentUser):
    """Import a personal note into a Learning Space."""
    return await note_service.import_to_space(
        user_id=current_user.id, note_id=note_id, space_id=body.circleId
    )


@router.post("/notes/{note_id}/attachments", response_model=models.NoteAttachmentResponse, status_code=201)
async def add_attachment(note_id: str, body: models.NoteAttachmentCreate, current_user: CurrentUser):
    """Add an attachment to a note."""
    return await note_service.add_attachment(
        user_id=current_user.id, note_id=note_id, data=body.model_dump()
    )


@router.delete("/notes/{note_id}/attachments/{attachment_id}", status_code=204)
async def remove_attachment(note_id: str, attachment_id: str, current_user: CurrentUser):
    """Remove an attachment from a note."""
    success = await note_service.remove_attachment(
        user_id=current_user.id, note_id=note_id, attachment_id=attachment_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Attachment not found")


# ===========================================================================
# Exam Preparation
# ===========================================================================


@router.post("/exam-prep", status_code=201)
async def create_exam_prep(body: models.ExamPrepCreate, current_user: CurrentUser):
    """Create a new exam preparation."""
    return await exam_prep_service.create_exam_prep(
        user=current_user, data=body.model_dump()
    )


@router.put("/exam-prep/{prep_id}")
async def update_exam_prep(prep_id: str, body: models.ExamPrepUpdate, current_user: CurrentUser):
    """Update exam prep metadata."""
    return await exam_prep_service.update_exam_prep(
        user=current_user, prep_id=prep_id, data=body.model_dump(exclude_unset=True)
    )


@router.get("/exam-prep/{prep_id}/progress")
async def get_exam_prep_progress(prep_id: str, current_user: CurrentUser):
    """Get exam prep progress and statistics."""
    return await exam_prep_service.get_exam_prep_progress(user=current_user, prep_id=prep_id)


@router.post("/exam-prep/{prep_id}/study-plan")
async def generate_study_plan(prep_id: str, current_user: CurrentUser):
    """Generate AI study plan."""
    return await exam_prep_service.generate_study_plan(user=current_user, prep_id=prep_id)


@router.post("/exam-prep/{prep_id}/quiz/start")
async def start_quiz(prep_id: str, body: models.QuizStartRequest, current_user: CurrentUser):
    """Start a quiz session."""
    return await exam_prep_service.start_quiz(
        user=current_user,
        prep_id=prep_id,
        mode=body.mode,
        topic_id=body.topic_id,
        question_count=body.question_count,
    )


@router.post("/exam-prep/{prep_id}/quiz/{session_id}/answer")
async def submit_answer(
    prep_id: str, session_id: str, body: models.AnswerSubmitRequest, current_user: CurrentUser
):
    """Submit an answer to a quiz question."""
    return await exam_prep_service.submit_answer(
        user=current_user,
        session_id=session_id,
        question_id=body.question_id,
        user_answer=body.user_answer,
        time_taken=body.time_taken_seconds,
    )


@router.post("/exam-prep/{prep_id}/quiz/{session_id}/complete")
async def complete_quiz(
    prep_id: str, session_id: str, body: models.QuizCompleteRequest, current_user: CurrentUser
):
    """Complete a quiz session."""
    return await exam_prep_service.complete_quiz(
        user=current_user,
        session_id=session_id,
        duration_seconds=body.duration_seconds,
    )


# ===========================================================================
# Document Generation
# ===========================================================================


@router.post("/documents/generate")
async def generate_document(body: models.DocumentGenerateRequest, current_user: CurrentUser):
    """Generate an academic document using AI."""
    return await document_service.generate_document(
        user=current_user, data=body.model_dump()
    )


@router.get("/documents")
async def list_documents(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
):
    """List generated documents."""
    return await document_service.list_documents(
        user_id=current_user.id, page=page, page_size=pageSize
    )
