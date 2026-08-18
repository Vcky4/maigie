"""Collection service — CRUD, item management, and auto-seeding."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.shared.exceptions import NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def create_collection(user_id: str, data: dict[str, Any]) -> models.CollectionResponse:
    """Create a new collection for the learner."""
    collection = await repo.create_collection({"userId": user_id, **data})
    return _to_response(collection)


async def update_collection(
    user_id: str, collection_id: str, data: dict[str, Any]
) -> models.CollectionResponse:
    """Update a collection's title or description."""
    existing = await repo.find_collection(collection_id, user_id)
    if not existing:
        raise NotFoundError("Collection not found")

    updated = await repo.update_collection(collection_id, data)
    if not updated:
        raise NotFoundError("Collection not found")
    return _to_response(updated)


async def delete_collection(user_id: str, collection_id: str) -> bool:
    """Soft-delete a collection."""
    deleted = await repo.soft_delete_collection(collection_id, user_id)
    if not deleted:
        raise NotFoundError("Collection not found")
    return True


async def add_item(
    user_id: str, collection_id: str, entity_type: str, entity_id: str
) -> models.CollectionItemResponse:
    """Add an item to a collection. Raises 409 on duplicate."""
    from fastapi import HTTPException, status

    collection = await repo.find_collection(collection_id, user_id)
    if not collection:
        raise NotFoundError("Collection not found")

    # Check for duplicate
    for existing_item in collection.items:
        if existing_item.entity_type == entity_type and existing_item.entity_id == entity_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Item already exists in this collection",
            )

    item = await repo.create_collection_item(
        {
            "collectionId": collection_id,
            "entityType": entity_type,
            "entityId": entity_id,
        }
    )
    return models.CollectionItemResponse(
        id=item.id,
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        title="",  # Title is resolved on detail reads
        position=item.position,
        added_at=item.added_at,
    )


async def remove_item(user_id: str, collection_id: str, item_id: str) -> bool:
    """Remove an item from a collection."""
    collection = await repo.find_collection(collection_id, user_id)
    if not collection:
        raise NotFoundError("Collection not found")

    deleted = await repo.delete_collection_item(item_id, collection_id)
    if not deleted:
        raise NotFoundError("Item not found")
    return True


async def reorder_items(
    user_id: str, collection_id: str, item_ids: list[str]
) -> models.CollectionResponse:
    """Reorder items in a collection."""
    collection = await repo.find_collection(collection_id, user_id)
    if not collection:
        raise NotFoundError("Collection not found")

    await repo.reorder_collection_items(collection_id, item_ids)
    # Re-fetch to get updated state
    refreshed = await repo.find_collection(collection_id, user_id)
    return _to_response(refreshed)  # type: ignore[arg-type]


async def get_detail(user_id: str, collection_id: str) -> models.CollectionDetailResponse:
    """Get a collection with its resolved items."""
    collection = await repo.find_collection(collection_id, user_id)
    if not collection:
        raise NotFoundError("Collection not found")

    items_with_titles = await repo.list_collection_items_with_titles(collection_id)
    entity_types = list({item["entity_type"] for item in items_with_titles})

    return models.CollectionDetailResponse(
        id=collection.id,
        title=collection.title,
        description=collection.description,
        source_tag=collection.source_tag,
        item_count=len(items_with_titles),
        entity_types=entity_types,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        items=[
            models.CollectionItemResponse(
                id=item["id"],
                entity_type=item["entity_type"],
                entity_id=item["entity_id"],
                title=item["title"],
                position=item["position"],
                added_at=item["added_at"],
            )
            for item in items_with_titles
        ],
    )


async def list_collections(
    user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list[models.CollectionResponse], int]:
    """List collections with pagination."""
    skip = (page - 1) * page_size
    collections, total = await repo.list_collections(user_id, skip=skip, take=page_size)
    items = [_to_response(c) for c in collections]
    return items, total


async def auto_seed_collections(user_id: str) -> None:
    """Auto-seed collections from cross-type tags. Bounded, idempotent, never raises."""
    try:
        cross_tags = await repo.find_cross_type_tags(user_id, limit=8)
        if not cross_tags:
            return

        for tag, _type_count in cross_tags:
            try:
                collection = await repo.create_collection(
                    {
                        "userId": user_id,
                        "title": tag,
                        "sourceTag": tag,
                    }
                )
                # Populate items for this tag
                items_to_add = await _find_items_for_tag(user_id, tag)
                if items_to_add:
                    await repo.bulk_create_collection_items(collection.id, items_to_add)
            except Exception:
                logger.warning(
                    "Failed to seed collection for tag",
                    extra={"user_id": user_id, "tag": tag},
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Collection auto-seeding failed",
            extra={"user_id": user_id},
            exc_info=True,
        )


async def _find_items_for_tag(user_id: str, tag: str) -> list[dict[str, Any]]:
    """Find note and resource items matching a tag for seeding."""
    from sqlalchemy import select as sa_select
    from sqlalchemy import text as sql_text

    from src.shared.database import get_session_factory

    factory = get_session_factory()
    items: list[dict[str, Any]] = []

    async with factory() as s:
        # Find notes with this tag
        note_query = sql_text(
            """
            SELECT n.id FROM "Note" n
            JOIN "NoteTag" nt ON nt."noteId" = n.id
            WHERE n."userId" = :user_id AND n."spaceId" IS NULL AND nt.tag = :tag
        """
        )
        note_rows = (await s.execute(note_query, {"user_id": user_id, "tag": tag})).all()
        for row in note_rows:
            items.append({"entityType": "note", "entityId": row[0]})

        # Find resources with this tag
        resource_query = sql_text(
            """
            SELECT sr.id FROM "SavedResource" sr
            WHERE sr."userId" = :user_id AND :tag = ANY(sr.tags)
        """
        )
        resource_rows = (await s.execute(resource_query, {"user_id": user_id, "tag": tag})).all()
        for row in resource_rows:
            items.append({"entityType": "saved_resource", "entityId": row[0]})

    return items


async def get_dashboard_collections(user_id: str, limit: int = 6) -> list[dict[str, Any]]:
    """Auto-seed, then return dashboard summaries."""
    await auto_seed_collections(user_id)
    return await repo.list_dashboard_collections(user_id, take=limit)


def _to_response(collection: Any) -> models.CollectionResponse:
    """Convert a Collection ORM object to a response model."""
    items = list(collection.items) if collection.items else []
    entity_types = list({item.entity_type for item in items})
    return models.CollectionResponse(
        id=collection.id,
        title=collection.title,
        description=collection.description,
        source_tag=collection.source_tag,
        item_count=len(items),
        entity_types=entity_types,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )
