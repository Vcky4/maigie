"""
Resource management — create, list, interact, delete, recommend.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from src.domains.identity.db_models import User

from src.shared.exceptions import NotFoundError

from ..events import emit_resource_added
from ..repository import knowledge_repo

logger = logging.getLogger(__name__)


async def list_resources(
    *,
    user_id: str,
    space_id: str | None = None,
    topic_id: str | None = None,
    course_id: str | None = None,
    resource_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "createdAt",
    sort_order: str = "desc",
) -> dict[str, Any]:
    """List resources with pagination and filters."""
    where: dict[str, Any] = {"userId": user_id}

    if space_id:
        where["spaceId"] = space_id
    else:
        where["spaceId"] = None

    if topic_id:
        where["topicId"] = topic_id
    if course_id:
        where["courseId"] = course_id
    if resource_type:
        where["type"] = resource_type
    if search:
        where["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"description": {"contains": search, "mode": "insensitive"}},
        ]

    skip = (page - 1) * page_size
    resources, total = await knowledge_repo.list_resources(
        where=where, skip=skip, take=page_size, order={sort_by: sort_order}
    )

    return {
        "resources": [_format_resource(r) for r in resources],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "hasMore": (skip + page_size) < total,
    }


async def create_resource(*, user: User, data: dict[str, Any]) -> dict[str, Any]:
    """Create a new resource and index it."""
    resource_data: dict[str, Any] = {
        "userId": user.id,
        "title": data["title"],
        "url": data["url"],
        "type": data.get("type", "OTHER"),
        "isRecommended": data.get("isRecommended", False),
    }
    if data.get("description"):
        resource_data["description"] = data["description"]
    if data.get("metadata"):
        resource_data["metadata"] = data["metadata"]
    if data.get("recommendationScore"):
        resource_data["recommendationScore"] = data["recommendationScore"]
    if data.get("recommendationSource"):
        resource_data["recommendationSource"] = data["recommendationSource"]
    if data.get("courseId"):
        resource_data["courseId"] = data["courseId"]
    if data.get("topicId"):
        resource_data["topicId"] = data["topicId"]
    if data.get("spaceId"):
        resource_data["spaceId"] = data["spaceId"]

    resource = await knowledge_repo.create_resource(resource_data)
    await emit_resource_added(user.id, resource.id, data.get("courseId"))

    return {
        "id": resource.id,
        "title": resource.title,
        "url": resource.url,
        "description": resource.description,
        "type": resource.type,
        "createdAt": resource.createdAt.isoformat(),
    }


async def record_interaction(*, user_id: str, resource_id: str, interaction_type: str) -> None:
    """Record a user interaction with a resource."""
    resource = await knowledge_repo.find_resource(resource_id, user_id)
    if not resource:
        raise NotFoundError("Resource", resource_id)

    update_data: dict[str, Any] = {}
    if interaction_type == "RESOURCE_CLICK":
        update_data["clickCount"] = {"increment": 1}
        update_data["lastAccessedAt"] = datetime.now(timezone.utc)
    elif interaction_type == "RESOURCE_BOOKMARK":
        update_data["bookmarkCount"] = {"increment": 1}

    if update_data:
        await knowledge_repo.update_resource(resource_id, update_data)


async def delete_resource(*, user_id: str, resource_id: str) -> None:
    """Delete a resource."""
    resource = await knowledge_repo.find_resource(resource_id, user_id)
    if not resource:
        raise NotFoundError("Resource", resource_id)
    await knowledge_repo.delete_resource(resource_id)


async def recommend_resources(
    *, user_id: str, query: str, limit: int = 5, context: dict | None = None
) -> dict[str, Any]:
    """Generate AI-powered resource recommendations via RAG."""
    from src.domains.intelligence.memory.memory_service import get_memory_context

    user_context = await get_memory_context(user_id)
    if context:
        user_context.update(context)

    # Use intelligence domain's RAG capability
    from src.domains.intelligence.reasoning.llm import generate_content

    prompt = (
        f"Based on the user's learning context, recommend {limit} resources for: {query}\n\n"
        f"User context: {str(user_context)[:500]}\n\n"
        "Return as a JSON array of objects with: title, url, description, type, relevance, score."
    )

    import json

    raw = await generate_content(prompt, max_tokens=1500, temperature=0.3)
    try:
        import re

        match = re.search(r"\[.*\]", raw, re.DOTALL)
        recommendations = json.loads(match.group(0)) if match else []
    except Exception:
        recommendations = []

    return {
        "recommendations": [
            {
                "title": r.get("title", "Untitled"),
                "url": r.get("url", ""),
                "description": r.get("description"),
                "type": r.get("type", "OTHER"),
                "relevance": r.get("relevance"),
                "score": r.get("score", 0.5),
            }
            for r in recommendations[:limit]
        ],
        "query": query,
        "personalized": True,
    }


def _format_resource(r) -> dict[str, Any]:
    """Format a Prisma resource record for API response."""
    return {
        "id": r.id,
        "userId": r.userId,
        "title": r.title,
        "url": r.url,
        "description": r.description,
        "type": r.type,
        "metadata": r.metadata,
        "isRecommended": r.isRecommended,
        "recommendationScore": r.recommendationScore,
        "recommendationSource": getattr(r, "recommendationSource", None),
        "clickCount": r.clickCount,
        "bookmarkCount": getattr(r, "bookmarkCount", 0),
        "spaceId": getattr(r, "space_id", None),
        "lastAccessedAt": r.lastAccessedAt.isoformat() if r.lastAccessedAt else None,
        "createdAt": r.createdAt.isoformat(),
        "updatedAt": r.updatedAt.isoformat(),
    }
