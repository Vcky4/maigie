"""
API routes for Notes management.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.dependencies import CurrentUser, DBDep
from src.models.notes import (
    NoteCreate,
    NoteListResponse,
    NoteResponse,
    NoteUpdate,
)
from src.services import note_service

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
