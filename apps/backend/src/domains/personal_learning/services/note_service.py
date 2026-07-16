"""
Note management service.

Handles CRUD, archiving, attachments, tags, AI retake/summary, and import to spaces.
"""

import logging
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def create_note(*, user_id: str, data: dict[str, Any]) -> Any:
    """Create a note with optional tags."""
    tags = data.pop("tags", None)
    note_data: dict[str, Any] = {"userId": user_id, **data}

    note = await repo.create_note(note_data)

    if tags:
        await repo.create_note_tags(note.id, tags)
        note = await repo.find_note(note.id, user_id)

    return note


async def get_note(*, user_id: str, note_id: str) -> Any:
    """Get a note by ID. Raises if not found or not owned."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)
    return note


async def list_notes(
    *,
    user_id: str,
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    tag: str | None = None,
    course_id: str | None = None,
    topic_id: str | None = None,
    archived: bool | None = False,
    space_id: str | None = None,
) -> tuple[list, int]:
    """List notes with filters and pagination."""
    where: dict[str, Any] = {}

    if space_id:
        where["spaceId"] = space_id
    else:
        where["spaceId"] = None

    if archived is not None:
        where["archived"] = archived
    if course_id:
        where["courseId"] = course_id
    if topic_id:
        where["topicId"] = topic_id
    if search:
        where["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"content": {"contains": search, "mode": "insensitive"}},
        ]
    if tag:
        where["tags"] = {"some": {"tag": tag}}

    skip = (page - 1) * size
    return await repo.list_notes(user_id, where=where, skip=skip, take=size)


async def update_note(*, user_id: str, note_id: str, data: dict[str, Any]) -> Any:
    """Update a note. Handles tags separately."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)

    tags = data.pop("tags", None)
    update_data = {k: v for k, v in data.items() if v is not None}

    if update_data:
        await repo.update_note(note_id, update_data)

    if tags is not None:
        await repo.delete_note_tags(note_id)
        if tags:
            await repo.create_note_tags(note_id, tags)

    return await repo.find_note(note_id, user_id)


async def delete_note(*, user_id: str, note_id: str) -> bool:
    """Delete a note."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        return False
    await repo.delete_note(note_id)
    return True


async def add_attachment(*, user_id: str, note_id: str, data: dict[str, Any]) -> Any:
    """Add an attachment to a note."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)
    return await repo.create_attachment(
        {
            "noteId": note_id,
            "filename": data["filename"],
            "url": data["url"],
            "size": data.get("size"),
        }
    )


async def remove_attachment(*, user_id: str, note_id: str, attachment_id: str) -> bool:
    """Remove an attachment from a note."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        return False
    attachment = await repo.find_attachment(attachment_id, note_id)
    if not attachment:
        return False
    await repo.delete_attachment(attachment_id)
    return True


async def retake_note(*, user_id: str, note_id: str) -> Any:
    """Rewrite note content using AI for improved formatting."""
    import re

    note = await repo.find_note(note_id, user_id)
    if not note or not note.content:
        raise NotFoundError("Note", note_id)

    from src.domains.intelligence.reasoning.llm import generate_content

    # Build context
    context_parts = [f"Title: {note.title}"]
    if note.topicId:
        from sqlalchemy import select as sa_select
        from src.domains.knowledge.db_models import Topic, Module, Course
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            stmt = sa_select(Topic).where(Topic.id == note.topicId)
            result = await session.execute(stmt)
            topic = result.scalar_one_or_none()
            topic_module = None
            topic_course = None
            if topic and topic.module_id:
                mod_stmt = sa_select(Module).where(Module.id == topic.module_id)
                mod_result = await session.execute(mod_stmt)
                topic_module = mod_result.scalar_one_or_none()
            if topic_module and topic_module.course_id:
                course_stmt = sa_select(Course).where(Course.id == topic_module.course_id)
                course_result = await session.execute(course_stmt)
                topic_course = course_result.scalar_one_or_none()
        if topic:
            context_parts.append(f"Topic: {topic.title}")
            if topic_module:
                context_parts.append(f"Module: {topic_module.title}")
                if topic_course:
                    context_parts.append(f"Course: {topic_course.title}")

    cleaned = re.sub(
        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*", "", note.content, flags=re.DOTALL
    ).strip()

    prompt = (
        f"Rewrite and improve this note with better markdown formatting, "
        f"clearer structure, and enhanced readability.\n\n"
        f"Context: {' | '.join(context_parts)}\n\n"
        f"Original content:\n{cleaned}\n\n"
        f"Return ONLY the improved note content in markdown. No meta-commentary."
    )

    rewritten = await generate_content(prompt, max_tokens=3000)
    rewritten = re.sub(
        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*", "", rewritten, flags=re.DOTALL
    ).strip()

    await repo.update_note(note_id, {"content": rewritten})
    return await repo.find_note(note_id, user_id)


async def add_summary(*, user_id: str, note_id: str) -> Any:
    """Generate AI summary for a note."""
    import re

    note = await repo.find_note(note_id, user_id)
    if not note or not note.content:
        raise NotFoundError("Note", note_id)

    from src.domains.intelligence.reasoning.llm import generate_content

    cleaned = re.sub(
        r"\s*<<<ACTION_START>>>.*?<<<ACTION_END>>>\s*", "", note.content, flags=re.DOTALL
    ).strip()

    prompt = (
        f"Summarize this note concisely. Include key points, definitions, and takeaways.\n\n"
        f"Note title: {note.title}\n"
        f"Content:\n{cleaned[:3000]}\n\n"
        f"Return a clear, scannable summary in markdown (bullet points preferred)."
    )

    summary = await generate_content(prompt, max_tokens=1000)
    await repo.update_note(note_id, {"summary": summary})
    return await repo.find_note(note_id, user_id)


async def import_to_space(*, user_id: str, note_id: str, space_id: str) -> Any:
    """Import a personal note into a Learning Space."""
    from src.domains.personal_learning.services.note_impl import import_note_to_circle

    return await import_note_to_circle(None, note_id, space_id, user_id)
