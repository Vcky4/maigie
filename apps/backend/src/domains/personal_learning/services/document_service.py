"""
Document generation service.

Handles AI-powered document creation (essays, reports, presentations, CVs)
and sharing via public links.
"""

import logging
from typing import Any

from prisma.models import User

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def generate_document(*, user: User, data: dict[str, Any]) -> Any:
    """Generate a document using AI."""
    from src.domains.personal_learning.services.document_impl import generate_document as _generate

    return await _generate(
        user_id=user.id,
        doc_type=data["type"],
        title=data["title"],
        prompt=data["prompt"],
        output_format=data.get("format", "pdf"),
        course_id=data.get("courseId"),
        topic_id=data.get("topicId"),
    )


async def list_documents(*, user_id: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """List generated documents."""
    skip = (page - 1) * page_size
    items, total = await repo.list_documents(user_id, skip=skip, take=page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_shared_document(share_id: str) -> Any:
    """Get a publicly shared document by share ID."""
    doc = await repo.find_document_by_share_id(share_id)
    if not doc or not doc.isPublic:
        return None
    return doc
