"""
Note management service.

Handles CRUD, archiving, attachments, tags, AI retake/summary, and import to spaces.
"""

import logging
from typing import Any

from src.domains.personal_learning.db_models import Note
from src.shared.exceptions import NotFoundError, ValidationError
from src.shared.field_mapping import reject_unclearable

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

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="note_created",
        title=f"Created note: {data.get('title', 'Untitled')}",
        entity_type="note",
        entity_id=note.id,
        context={"source": "personal", "noteId": note.id},
    )

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
    sort: str = "recent",
) -> tuple[list, int]:
    """List notes with filters, pagination and ordering.

    ``sort`` belongs to the server for the same reason it does on the flashcard list: a
    page boundary means nothing without a defined order, and a client re-sorting the page
    it received gets a list ordered within pages and unordered across them. Both clients
    used to sort locally over a single 100-row fetch, which was correct only while every
    library fitted in one page.
    """
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
    return await repo.list_notes(user_id, where=where, skip=skip, take=size, sort=sort)


async def _snapshot_note(note: Any) -> None:
    """Record the note as it is now, before something replaces its content.

    Called on every write that overwrites ``content``. A note whose content is empty is still
    snapshotted: "it was blank here" is a fact a version list needs, and skipping it would make the
    first real draft look like the original.

    Failure to snapshot does not fail the write. The alternative — refusing an edit because the
    version log is unavailable — costs the learner the work they just did in order to protect a
    record of work they already had.
    """
    try:
        await repo.create_note_history(
            {
                "noteId": note.id,
                "userId": note.user_id,
                "title": note.title,
                "content": note.content,
            }
        )
    except Exception as error:  # pragma: no cover - defensive
        logger.warning(
            "Could not snapshot note before overwrite",
            extra={"note_id": note.id, "error": str(error)},
        )


async def update_note(*, user_id: str, note_id: str, data: dict[str, Any]) -> Any:
    """Update a note. Handles tags separately."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)

    tags = data.pop("tags", None)

    # Snapshot before an overwrite, and only when the content actually changes. An autosaving editor
    # sends the whole note on every keystroke pause, so snapshotting on "content was present in the
    # body" would fill the log with identical entries and bury the version worth going back to.
    if "content" in data and data.get("content") != note.content:
        await _snapshot_note(note)
    # An explicit null clears the field; an omitted key leaves it alone. This used to be
    # `{k: v for k, v in data.items() if v is not None}`, which — given the route dumps the body with
    # `exclude_unset=True` — made clearing any field impossible while still returning success.
    #
    # Nullability is read from the mapped columns, so a null aimed at a NOT NULL column is refused with
    # a message the client can act on instead of a database constraint error.
    try:
        reject_unclearable(data, Note)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    update_data = data

    if update_data:
        await repo.update_note(note_id, update_data)

    if tags is not None:
        await repo.delete_note_tags(note_id)
        if tags:
            await repo.create_note_tags(note_id, tags)

    return await repo.find_note(note_id, user_id)


async def list_history(
    *, user_id: str, note_id: str, page: int = 1, size: int = 20
) -> tuple[list, int]:
    """Versions of a note, newest first. Raises if the note is not the learner's."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)
    skip = max(0, (page - 1) * size)
    return await repo.list_note_history(note_id, user_id, skip=skip, take=size)


async def restore_version(*, user_id: str, note_id: str, version_id: str) -> Any:
    """Put a note's content back to a recorded version.

    Restoring is itself an overwrite, so the current content is snapshotted first — otherwise
    restoring the wrong version destroys the thing you were trying to get back to, and a version log
    whose use loses data is worse than none.

    **Content only, not the title.** A version carries the title it had so the entry is readable in a
    list, but titles are not restored: snapshots are taken when content changes, so a learner who
    renamed a note without editing it has no snapshot recording the new name, and restoring an older
    version would silently undo a rename they never asked to reverse. Renaming stays a `PATCH`.
    """
    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)

    version = await repo.find_note_history(version_id, note_id, user_id)
    if not version:
        raise NotFoundError("NoteHistory", version_id)

    if version.content != note.content:
        await _snapshot_note(note)
        await repo.update_note(note_id, {"content": version.content})

    return await repo.find_note(note_id, user_id)


async def list_tags(*, user_id: str, archived: bool = False) -> list[dict[str, Any]]:
    """The learner's whole tag catalogue with counts, commonest first."""
    counts = await repo.count_note_tags(user_id, archived=archived)
    return [{"tag": tag, "count": count} for tag, count in counts]


async def get_summary(*, user_id: str, archived: bool = False) -> dict[str, Any]:
    """Library-wide note figures, plus a seven-day capture trend in the learner's own days.

    The trend's days are the learner's, where we know them. ``UserPreferences.timezone`` is `NOT NULL`
    with a `"UTC"` default and was never prompted for, so it is only a fact when its source says it
    was observed; ``resolve_learner_timezone`` encodes that and falls back to UTC flagged as unknown.
    Every day in the window is present, zeros included, so a client renders gaps rather than
    inferring them.
    """
    import asyncio
    from datetime import UTC, datetime, timedelta

    from src.shared.time.learner_timezone import resolve_learner_timezone, to_learner_local

    learner_timezone = await resolve_learner_timezone(user_id)
    local_now = to_learner_local(datetime.now(UTC), learner_timezone)
    local_today = local_now.date()
    # Seven days inclusive of today, so the window starts six days back.
    window_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=6
    )
    window_start = window_start_local.astimezone(UTC)

    counts, created_at_values = await asyncio.gather(
        repo.count_note_summary(user_id, archived=archived),
        repo.list_note_creation_times(user_id, since=window_start, archived=archived),
    )
    total, tagged, linked_to_course, with_attachments = counts

    per_day: dict[Any, int] = {
        (window_start_local + timedelta(days=offset)).date(): 0 for offset in range(7)
    }
    for created_at in created_at_values:
        local_day = to_learner_local(created_at, learner_timezone).date()
        if local_day in per_day:
            per_day[local_day] += 1
        elif local_day > local_today:
            # A clock skew or a zone change can put a row a few hours "ahead". Counted on the last
            # day rather than dropped: the note exists, and silently losing it from a trend is how a
            # chart comes to disagree with the total beside it.
            per_day[local_today] += 1

    return {
        "total": total,
        "tagged": tagged,
        "linkedToCourse": linked_to_course,
        "withAttachments": with_attachments,
        "capturedLastWeek": [
            {"date": day, "count": count} for day, count in sorted(per_day.items())
        ],
    }


async def delete_note(*, user_id: str, note_id: str) -> bool:
    """Delete a note, and any attachment files it owns.

    `Note -> NoteAttachment` cascades in the database, so deleting a note used to leave every
    uploaded attachment sitting in storage with nothing referencing it — unfindable and permanent.
    The rows go with the note; the objects have to be removed deliberately.
    """
    note = await repo.find_note(note_id, user_id)
    if not note:
        return False

    for attachment in list(getattr(note, "attachments", None) or []):
        await _delete_attachment_object(attachment)

    await repo.delete_note(note_id)
    return True


async def _delete_attachment_object(attachment: Any) -> None:
    """Remove an attachment's stored object, if it is one of ours.

    An attachment can be a URL the learner pasted rather than a file they uploaded — the JSON
    registration route accepts any URL — so provenance is checked before deleting anything.
    """
    from src.shared.infrastructure.storage import storage_service

    url = getattr(attachment, "url", None)
    if not url or not storage_service.owns_url(url):
        return
    if not await storage_service.delete(url):
        logger.warning(
            "Attachment object could not be deleted from storage",
            extra={"attachment_id": getattr(attachment, "id", None)},
        )


async def add_attachment(*, user_id: str, note_id: str, data: dict[str, Any]) -> Any:
    """Register an already-hosted file as an attachment, by URL."""
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


async def upload_attachment(*, user_id: str, note_id: str, file: Any) -> Any:
    """Upload a file and attach it to a note.

    The JSON route above takes a URL, which means a client could only attach something it had already
    hosted somewhere — and nothing in this API would host it. This is the missing half: multipart in,
    through the same storage service study-plan materials and generated documents use, with the row
    written only after the upload succeeds so no attachment points at a URL holding nothing.
    """
    from src.shared.infrastructure.storage import StorageError, storage_service

    note = await repo.find_note(note_id, user_id)
    if not note:
        raise NotFoundError("Note", note_id)

    try:
        # Scoped by learner and note, so two notes can hold files of the same name and one
        # learner's upload cannot overwrite another's.
        stored = await storage_service.upload_upload_file(
            file, path_prefix=f"note-attachments/{user_id}/{note_id}"
        )
    except StorageError as error:
        raise ValueError(f"Upload failed: {error}") from error

    return await repo.create_attachment(
        {
            "noteId": note_id,
            "filename": stored["filename"],
            "url": stored["url"],
            "size": stored.get("size"),
        }
    )


async def remove_attachment(*, user_id: str, note_id: str, attachment_id: str) -> bool:
    """Remove an attachment from a note, and its stored object if we host it."""
    note = await repo.find_note(note_id, user_id)
    if not note:
        return False
    attachment = await repo.find_attachment(attachment_id, note_id)
    if not attachment:
        return False
    await _delete_attachment_object(attachment)
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
    if note.topic_id:
        from sqlalchemy import select as sa_select

        from src.domains.knowledge.db_models import Course, Module, Topic
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            stmt = sa_select(Topic).where(Topic.id == note.topic_id)
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

    if not rewritten:
        # An empty rewrite used to be written straight over the note, so a model returning nothing
        # erased what the learner wrote and reported success.
        raise ValidationError("The rewrite came back empty; the note is unchanged.")

    # The learner's own prose is about to be replaced by a model's version of it. This is the write
    # `NoteHistory` exists for, and until now the original was simply gone.
    await _snapshot_note(note)

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
    from src.domains.personal_learning.services.note_impl import import_note_to_space

    return await import_note_to_space(None, note_id, space_id, user_id)
