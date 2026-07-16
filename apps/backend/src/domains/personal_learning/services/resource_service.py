"""
Resource service — personal library of saved resources.

Learners save and organize resources from courses, spaces, and external
sources for quick access during study.
"""

import logging
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def save_resource(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Save a resource to the learner's personal library.
    Req 6.1
    """
    resource_data = {
        "userId": user_id,
        "title": data["title"],
        "url": data.get("url"),
        "sourceType": data["sourceType"],
        "sourceId": data.get("sourceId"),
        "tags": data.get("tags"),
    }
    return await repo.create_resource(resource_data)


async def list_resources(
    *,
    user_id: str,
    source_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """
    List saved resources with pagination and filters.
    Req 6.2
    """
    skip = (page - 1) * page_size
    return await repo.list_resources(
        user_id, source_type=source_type, search=search, skip=skip, take=page_size
    )


async def delete_resource(*, user_id: str, resource_id: str) -> bool:
    """
    Remove a resource from the learner's library (doesn't affect source).
    Req 6.3
    """
    return await repo.delete_resource(resource_id, user_id)


async def update_tags(*, user_id: str, resource_id: str, tags: list[str]) -> Any:
    """
    Update tags on a saved resource.
    Req 6.4
    """
    return await repo.update_resource_tags(resource_id, user_id, tags)


async def track_access(*, resource_id: str) -> None:
    """
    Update last_accessed_at for "recently used" sorting.
    Req 6.5
    """
    await repo.update_last_accessed(resource_id)
