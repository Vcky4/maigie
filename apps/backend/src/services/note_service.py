"""
Service for Note management.
"""

from typing import Any, List, Optional, Tuple

from src.core.database import Prisma
from src.models.notes import NoteAttachmentCreate, NoteCreate, NoteUpdate


async def latest_note_for_topic(
    db: Prisma, topic_id: str, user_id: str | None = None
) -> Any | None:
    """Most recently updated note linked to a topic (optionally scoped to a user)."""
    where: dict = {"topicId": topic_id}
    if user_id is not None:
        where["userId"] = user_id
    return await db.note.find_first(where=where, order={"updatedAt": "desc"})


async def create_note(db: Prisma, user_id: str, data: NoteCreate):
    """
    Create a new note.
    """
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
    search: str | None = None,
    tag: str | None = None,
    course_id: str | None = None,
    topic_id: str | None = None,
    archived: bool | None = False,
    circle_id: str | None = None,
) -> tuple[list[dict], int]:
    """
    List notes with filtering and pagination.
    """
    skip = (page - 1) * size

    # Build query
    if circle_id:
        # Notes shared in the circle
        where_clause = {"circleId": circle_id}
    else:
        # Personal notes (not shared in a circle)
        where_clause = {"userId": user_id, "circleId": None}

    if archived is not None:
        where_clause["archived"] = archived

    if course_id:
        where_clause["courseId"] = course_id

    if topic_id:
        where_clause["topicId"] = topic_id

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


async def add_attachment(db: Prisma, note_id: str, user_id: str, data: NoteAttachmentCreate):
    """
    Add an attachment to a note.
    """
    # Check ownership
    existing_note = await db.note.find_unique(where={"id": note_id})
    if not existing_note or existing_note.userId != user_id:
        return None

    # Create attachment
    attachment = await db.noteattachment.create(
        data={
            "noteId": note_id,
            "filename": data.filename,
            "url": data.url,
            "size": data.size,
        }
    )

    return attachment


async def remove_attachment(db: Prisma, note_id: str, attachment_id: str, user_id: str) -> bool:
    """
    Remove an attachment from a note.
    """
    # Check ownership of the note via the attachment
    # This also implicitly checks if the attachment exists and belongs to the note
    attachment = await db.noteattachment.find_first(
        where={
            "id": attachment_id,
            "noteId": note_id,
            "note": {"userId": user_id},
        }
    )

    if not attachment:
        return False

    await db.noteattachment.delete(where={"id": attachment_id})
    return True


async def import_note_to_circle(db: Prisma, note_id: str, circle_id: str, user_id: str):
    """
    Import a personal note to a circle by creating a copy.
    """
    # Verify the note belongs to the user and is a personal note
    original = await db.note.find_first(
        where={"id": note_id, "userId": user_id, "circleId": None},
        include={"tags": True, "attachments": True},
    )
    if not original:
        raise ValueError("Personal note not found or access denied")

    # Verify user is a member of the circle
    member = await db.circlemember.find_first(where={"circleId": circle_id, "userId": user_id})
    if not member:
        raise ValueError("User is not a member of the circle")

    # Create a copy of the note for the circle
    note_data = {
        "title": original.title,
        "content": original.content,
        "userId": user_id,
        "circleId": circle_id,
        "summary": original.summary,
    }

    new_note = await db.note.create(data=note_data)

    # Copy tags
    if original.tags:
        for tag_obj in original.tags:
            await db.notetag.create(
                data={
                    "noteId": new_note.id,
                    "tag": tag_obj.tag,
                }
            )

    # Copy attachments metadata (pointing to the same S3 URLs)
    if original.attachments:
        for att in original.attachments:
            await db.noteattachment.create(
                data={
                    "noteId": new_note.id,
                    "filename": att.filename,
                    "url": att.url,
                    "size": att.size,
                }
            )

    return await get_note(db, new_note.id, user_id)
