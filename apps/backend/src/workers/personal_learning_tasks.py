"""
Personal Learning domain background tasks.

Document generation is CPU/LLM-intensive (typically 5-30 seconds).
Routing it through Celery frees up request workers and lets clients
poll for status instead of holding an HTTP connection open.
"""

from __future__ import annotations

import asyncio
import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="personal_learning.generate_document",
    queue="heavy",
    time_limit=180,
    soft_time_limit=150,
    bind=True,
)
def generate_document_task(
    self,
    *,
    user_id: str,
    doc_type: str,
    title: str,
    prompt: str,
    format: str = "pdf",
    style: str = "academic",
    course_id: str | None = None,
    topic_id: str | None = None,
) -> dict:
    """Run the full LLM-and-render pipeline in the background.

    Returns the generated document record (as a dict). Client can poll
    with the task id or wait for the WebSocket completion event.
    """
    from src.domains.personal_learning.services.document_impl import create_from_prompt
    from src.shared.database.session import connect_db, disconnect_db

    async def _run() -> dict:
        await connect_db()
        try:
            doc = await create_from_prompt(
                user_id=user_id,
                doc_type=doc_type,
                title=title,
                prompt=prompt,
                format=format,
                style=style,
                course_id=course_id,
                topic_id=topic_id,
            )
            # Return a plain dict — Celery serializes results as JSON.
            return _serialize_document(doc)
        finally:
            await disconnect_db()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    except Exception as e:
        logger.exception(f"Document generation failed for {user_id} / {title}: {e}")
        raise
    finally:
        loop.close()


def _serialize_document(doc) -> dict:
    """Convert a GeneratedDocument ORM row to a JSON-safe dict."""
    if doc is None:
        return {}
    return {
        "id": getattr(doc, "id", None),
        "user_id": getattr(doc, "user_id", None),
        "title": getattr(doc, "title", None),
        "format": getattr(doc, "format", None),
        "doc_type": getattr(doc, "doc_type", None),
        "style": getattr(doc, "style", None),
        "filename": getattr(doc, "filename", None),
        "file_url": getattr(doc, "file_url", None),
        "preview_url": getattr(doc, "preview_url", None),
        "size": getattr(doc, "size", None),
        "content_type": getattr(doc, "content_type", None),
        "share_id": getattr(doc, "share_id", None),
        "is_public": getattr(doc, "is_public", False),
        "created_at": (
            getattr(doc, "created_at", None).isoformat()
            if getattr(doc, "created_at", None)
            else None
        ),
    }
