"""
Service for Note management.
"""

from typing import Any

from sqlalchemy import select, update, delete, func, or_

from src.shared.database import get_session_factory
from src.domains.personal_learning.db_models import Note, NoteTag, NoteAttachment
from src.domains.personal_learning.repository import personal_learning_repo
from src.domains.learning_spaces.db_models import SpaceMember
from src.models.notes import NoteAttachmentCreate, NoteCreate, NoteUpdate


async def latest_note_for_topic(
    db: Any = None, topic_id: str = "", user_id: str | None = None
) -> Any | None:
    """Most recently updated note linked to a topic (optionally scoped to a user)."""
    factory = get_session_factory()
    async with factory() as session:
        conditions = [Note.topic_id == topic_id]
        if user_id is not None:
            conditions.append(Note.user_id == user_id)
        stmt = select(Note).where(*conditions).order_by(Note.updated_at.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def create_note(db: Any = None, user_id: str = "", data: NoteCreate = None):
    """Create a new note."""
    note_data = data.model_dump(exclude={"tags"})
    note_data["userId"] = user_id

    note = await personal_learning_repo.create_note(note_data)

    # Handle tags if provided
    if data.tags:
        await personal_learning_repo.create_note_tags(note.id, data.tags)

    return await personal_learning_repo.find_note(note.id, user_id)


async def get_note(db: Any = None, note_id: str = "", user_id: str = ""):
    """Get a note by ID and user ID."""
    return await personal_learning_repo.find_note(note_id, user_id)


async def update_note(
    db: Any = None, note_id: str = "", user_id: str = "", data: NoteUpdate = None
):
    """Update a note."""
    existing_note = await personal_learning_repo.find_note(note_id, user_id)
    if not existing_note or existing_note.user_id != user_id:
        return None

    update_data = data.model_dump(exclude={"tags"}, exclude_unset=True)

    if update_data:
        await personal_learning_repo.update_note(note_id, {**update_data, "userId": user_id})

    # Update tags if provided (replace all)
    if data.tags is not None:
        await personal_learning_repo.delete_note_tags(note_id)
        await personal_learning_repo.create_note_tags(note_id, data.tags)

    return await personal_learning_repo.find_note(note_id, user_id)


async def delete_note(db: Any = None, note_id: str = "", user_id: str = "") -> bool:
    """Delete a note."""
    existing_note = await personal_learning_repo.find_note(note_id, user_id)
    if not existing_note or existing_note.user_id != user_id:
        return False

    await personal_learning_repo.delete_note(note_id)
    return True


async def list_notes(
    db: Any = None,
    user_id: str = "",
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    tag: str | None = None,
    course_id: str | None = None,
    topic_id: str | None = None,
    archived: bool | None = False,
    space_id: str | None = None,
) -> tuple[list, int]:
    """List notes with filtering and pagination."""
    skip = (page - 1) * size

    factory = get_session_factory()
    async with factory() as session:
        conditions = []

        if space_id:
            conditions.append(Note.space_id == space_id)
        else:
            conditions.append(Note.user_id == user_id)
            conditions.append(Note.space_id.is_(None))

        if archived is not None:
            conditions.append(Note.archived == archived)
        if course_id:
            conditions.append(Note.course_id == course_id)
        if topic_id:
            conditions.append(Note.topic_id == topic_id)
        if search:
            conditions.append(
                or_(Note.title.ilike(f"%{search}%"), Note.content.ilike(f"%{search}%"))
            )
        if tag:
            # Subquery for tag filter
            tag_subq = select(NoteTag.note_id).where(NoteTag.tag == tag).subquery()
            conditions.append(Note.id.in_(select(tag_subq.c.noteId)))

        count_stmt = select(func.count()).select_from(Note).where(*conditions)
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Note)
            .where(*conditions)
            .order_by(Note.updated_at.desc())
            .offset(skip)
            .limit(size)
        )
        result = await session.execute(stmt)
        notes = list(result.scalars().all())

    return notes, total


async def add_attachment(
    db: Any = None, note_id: str = "", user_id: str = "", data: NoteAttachmentCreate = None
):
    """Add an attachment to a note."""
    existing_note = await personal_learning_repo.find_note(note_id, user_id)
    if not existing_note or existing_note.user_id != user_id:
        return None

    return await personal_learning_repo.create_attachment(
        {
            "noteId": note_id,
            "filename": data.filename,
            "url": data.url,
            "size": data.size,
        }
    )


async def remove_attachment(
    db: Any = None, note_id: str = "", attachment_id: str = "", user_id: str = ""
) -> bool:
    """Remove an attachment from a note."""
    attachment = await personal_learning_repo.find_attachment(attachment_id, note_id)
    if not attachment:
        return False

    # Check note ownership
    note = await personal_learning_repo.find_note(note_id, user_id)
    if not note or note.user_id != user_id:
        return False

    await personal_learning_repo.delete_attachment(attachment_id)
    return True


async def import_note_to_space(
    db: Any = None, note_id: str = "", space_id: str = "", user_id: str = ""
):
    """Import a personal note to a space by creating a copy."""
    # Verify the note belongs to the user and is personal
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Note).where(
            Note.id == note_id, Note.user_id == user_id, Note.space_id.is_(None)
        )
        result = await session.execute(stmt)
        original = result.scalar_one_or_none()

    if not original:
        raise ValueError("Personal note not found or access denied")

    # Verify membership
    from src.domains.learning_spaces.repository import space_repo

    member = await space_repo.find_member(space_id, user_id)
    if not member:
        raise ValueError("User is not a member of the space")

    # Create copy
    new_note = await personal_learning_repo.create_note(
        {
            "title": original.title,
            "content": original.content,
            "userId": user_id,
            "spaceId": space_id,
            "summary": original.summary,
        }
    )

    # Copy tags
    if original.tags:
        await personal_learning_repo.create_note_tags(new_note.id, [t.tag for t in original.tags])

    # Copy attachments
    if original.attachments:
        for att in original.attachments:
            await personal_learning_repo.create_attachment(
                {
                    "noteId": new_note.id,
                    "filename": att.filename,
                    "url": att.url,
                    "size": att.size,
                }
            )

    return await personal_learning_repo.find_note(new_note.id, user_id)
