"""
Service for Note management.
"""

from typing import List, Optional, Tuple

from src.core.database import Prisma
from src.models.notes import NoteCreate, NoteUpdate


async def create_note(db: Prisma, user_id: str, data: NoteCreate):
    """
    Create a new note.
    """
    # Check if a note already exists for this topic
    if data.topicId:
        existing_topic_note = await db.note.find_first(where={"topicId": data.topicId})
        if existing_topic_note:
            raise ValueError("A note already exists for this topic")

    # Prepare data for creation
    note_data = data.model_dump(exclude={"tags"})
    note_data["userId"] = user_id

    # Create the note
    note = await db.note.create(data=note_data)

    # Handle tags if provided
    if data.tags:
        for tag in data.tags:
            await db.notetag.create(
                data={
                    "noteId": note.id,
                    "tag": tag,
                }
            )

    # Return the created note with relations
    return await get_note(db, note.id, user_id)


async def get_note(db: Prisma, note_id: str, user_id: str):
    """
    Get a note by ID and user ID.
    """
    return await db.note.find_unique(
        where={
            "id": note_id,
        },
        include={
            "tags": True,
            "attachments": True,
        },
    )


async def update_note(db: Prisma, note_id: str, user_id: str, data: NoteUpdate):
    """
    Update a note.
    """
    # Check ownership
    existing_note = await db.note.find_unique(where={"id": note_id})
    if not existing_note or existing_note.userId != user_id:
        return None

    # Prepare update data
    update_data = data.model_dump(exclude={"tags"}, exclude_unset=True)

    # Check if updating topicId and if it conflicts
    if "topicId" in update_data and update_data["topicId"] is not None:
        # Check if another note has this topicId (excluding self)
        existing_topic_note = await db.note.find_first(
            where={
                "topicId": update_data["topicId"],
                "NOT": {"id": note_id},
            }
        )
        if existing_topic_note:
            raise ValueError("A note already exists for this topic")

    # Update note fields
    if update_data:
        await db.note.update(
            where={"id": note_id},
            data=update_data,
        )

    # Update tags if provided (replace all)
    if data.tags is not None:
        # Remove existing tags
        await db.notetag.delete_many(where={"noteId": note_id})

        # Add new tags
        for tag in data.tags:
            await db.notetag.create(
                data={
                    "noteId": note_id,
                    "tag": tag,
                }
            )

    return await get_note(db, note_id, user_id)


async def delete_note(db: Prisma, note_id: str, user_id: str) -> bool:
    """
    Delete a note.
    """
    # Check ownership
    existing_note = await db.note.find_unique(where={"id": note_id})
    if not existing_note or existing_note.userId != user_id:
        return False

    await db.note.delete(where={"id": note_id})
    return True


async def list_notes(
    db: Prisma,
    user_id: str,
    page: int = 1,
    size: int = 20,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    course_id: Optional[str] = None,
    archived: Optional[bool] = False,
) -> Tuple[List[dict], int]:
    """
    List notes with filtering and pagination.
    """
    skip = (page - 1) * size

    # Build query
    where_clause = {"userId": user_id}

    if archived is not None:
        where_clause["archived"] = archived

    if course_id:
        where_clause["courseId"] = course_id

    if tag:
        where_clause["tags"] = {"some": {"tag": tag}}

    if search:
        # Simple case-insensitive search on title or content
        where_clause["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"content": {"contains": search, "mode": "insensitive"}},
        ]

    # Get total count
    total = await db.note.count(where=where_clause)

    # Get items
    notes = await db.note.find_many(
        where=where_clause,
        skip=skip,
        take=size,
        order={"updatedAt": "desc"},
        include={
            "tags": True,
            "attachments": True,
        },
    )

    return notes, total
