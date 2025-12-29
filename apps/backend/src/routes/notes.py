"""
API routes for Notes management.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.dependencies import CurrentUser, DBDep
from src.models.notes import (
    NoteAttachmentCreate,
    NoteAttachmentResponse,
    NoteCreate,
    NoteListResponse,
    NoteResponse,
    NoteUpdate,
)
from src.services import note_service
from src.services.llm_service import llm_service

router = APIRouter(tags=["notes"])


@router.post("/", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    data: NoteCreate,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Create a new note.
    """
    try:
        return await note_service.create_note(db, current_user.id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/", response_model=NoteListResponse)
async def list_notes(
    current_user: CurrentUser,
    db: DBDep,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    tag: Optional[str] = None,
    course_id: Optional[str] = Query(None, alias="courseId"),
    archived: Optional[bool] = False,
):
    """
    List user notes with filtering and pagination.
    """
    items, total = await note_service.list_notes(
        db,
        current_user.id,
        page=page,
        size=size,
        search=search,
        tag=tag,
        course_id=course_id,
        archived=archived,
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": (total + size - 1) // size,
    }


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Get a specific note by ID.
    """
    note = await note_service.get_note(db, note_id, current_user.id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )

    # Security check: Ensure note belongs to user (though service handles this mostly,
    # basic get_unique might return it if we don't filter by user in query)
    if note.userId != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,  # Don't reveal existence
            detail="Note not found",
        )

    return note


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    data: NoteUpdate,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Update a note.
    """
    try:
        note = await note_service.update_note(db, note_id, current_user.id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )
    return note


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Delete a note.
    """
    success = await note_service.delete_note(db, note_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )
    return None


@router.post("/{note_id}/archive", response_model=NoteResponse)
async def archive_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Archive a note (shortcut for update).
    """
    note = await note_service.update_note(db, note_id, current_user.id, NoteUpdate(archived=True))
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )
    return note


@router.post("/{note_id}/unarchive", response_model=NoteResponse)
async def unarchive_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Unarchive a note.
    """
    note = await note_service.update_note(db, note_id, current_user.id, NoteUpdate(archived=False))
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )
    return note


@router.post(
    "/{note_id}/attachments",
    response_model=NoteAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_note_attachment(
    note_id: str,
    data: NoteAttachmentCreate,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Create a new attachment for a note.
    """
    attachment = await note_service.add_attachment(db, note_id, current_user.id, data)
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )
    return attachment


@router.delete("/{note_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note_attachment(
    note_id: str,
    attachment_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Delete a note attachment.
    """
    success = await note_service.remove_attachment(db, note_id, attachment_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found or access denied",
        )
    return None


@router.post("/{note_id}/retake", response_model=NoteResponse)
async def retake_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Retake/rewrite a note using AI to improve content and markdown formatting.
    """
    # Get the note
    note = await note_service.get_note(db, note_id, current_user.id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )

    if note.userId != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )

    if not note.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Note has no content to retake",
        )

    try:
        # Build context for AI
        context = {}
        if note.topicId:
            topic = await db.topic.find_unique(
                where={"id": note.topicId}, include={"module": {"include": {"course": True}}}
            )
            if topic:
                context["topicTitle"] = topic.title
                if topic.module:
                    context["moduleTitle"] = topic.module.title
                    if topic.module.course:
                        context["courseTitle"] = topic.module.course.title
                        context["courseId"] = (
                            topic.module.course.id
                        )  # Include courseId from module->course

        # Strip any action blocks from the original content before rewriting
        cleaned_content = re.sub(
            r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
            "",
            note.content,
            flags=re.DOTALL,
        ).strip()

        # Use AI to rewrite the content
        rewritten_content = await llm_service.rewrite_note_content(
            content=cleaned_content, title=note.title, context=context
        )

        # Strip any action blocks from the rewritten content (in case AI includes them)
        rewritten_content = re.sub(
            r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
            "",
            rewritten_content,
            flags=re.DOTALL,
        ).strip()

        # Update the note with rewritten content
        updated_note = await note_service.update_note(
            db, note_id, current_user.id, NoteUpdate(content=rewritten_content)
        )

        return updated_note

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error retaking note: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retake note",
        )


@router.post("/{note_id}/add-summary", response_model=NoteResponse)
async def add_summary_to_note(
    note_id: str,
    current_user: CurrentUser,
    db: DBDep,
):
    """
    Add a summary to a note using AI.
    """
    # Get the note
    note = await note_service.get_note(db, note_id, current_user.id)
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )

    if note.userId != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found or access denied",
        )

    if not note.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Note has no content to summarize",
        )

    try:
        # Strip any action blocks from content before summarizing
        cleaned_content = re.sub(
            r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*",
            "",
            note.content,
            flags=re.DOTALL,
        ).strip()

        # Generate summary using AI
        summary = await llm_service.generate_summary(cleaned_content)

        # Update the note with summary in the summary field
        updated_note = await note_service.update_note(
            db, note_id, current_user.id, NoteUpdate(summary=summary)
        )

        return updated_note

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error adding summary to note: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add summary to note",
        )
