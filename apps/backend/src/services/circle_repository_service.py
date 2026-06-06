"""
Circle Repository service — public discovery and search.

Provides listing, search, and featured Circle discovery for the public
Circle Repository. Only Circles with ``visibility = PUBLIC`` and
``hiddenByModeration = false`` are surfaced.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from prisma import Prisma

from src.core.database import db as default_db

logger = logging.getLogger(__name__)


async def list_public(
    *,
    query: str | None = None,
    category: str | None = None,
    page: int = 1,
    size: int = 20,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """List public, non-hidden Circles with optional search and category filter.

    Results are ranked by relevance (name/description match) then by member
    count descending.

    Returns a paginated response dict with ``items`` and ``total``.
    """
    client = db_client or default_db

    where: dict[str, Any] = {
        "visibility": "PUBLIC",
        "hiddenByModeration": False,
    }

    if category:
        where["category"] = category

    if query:
        where["OR"] = [
            {"name": {"contains": query, "mode": "insensitive"}},
            {"description": {"contains": query, "mode": "insensitive"}},
        ]

    total = await client.circle.count(where=where)

    circles = await client.circle.find_many(
        where=where,
        include={"members": True},
        order={"createdAt": "desc"},
        skip=(page - 1) * size,
        take=size,
    )

    items = []
    for c in circles:
        member_count = len(c.members) if c.members else 0
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "category": c.category,
                "avatarUrl": c.avatarUrl,
                "bannerUrl": getattr(c, "bannerUrl", None),
                "memberCount": member_count,
                "featured": getattr(c, "featured", False),
            }
        )

    # Sort by member count descending (secondary sort after DB ordering)
    items.sort(key=lambda x: x["memberCount"], reverse=True)

    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    }


async def list_featured(
    *,
    db_client: Prisma | None = None,
) -> list[dict[str, Any]]:
    """List featured Circles for the Repository carousel.

    Featured requires ``circlePlanActive = true AND featured = true``
    and the Circle must be public and not hidden.
    """
    client = db_client or default_db

    circles = await client.circle.find_many(
        where={
            "visibility": "PUBLIC",
            "hiddenByModeration": False,
            "circlePlanActive": True,
            "featured": True,
        },
        include={"members": True},
        order={"createdAt": "desc"},
        take=20,
    )

    items = []
    for c in circles:
        member_count = len(c.members) if c.members else 0
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "category": c.category,
                "avatarUrl": c.avatarUrl,
                "bannerUrl": getattr(c, "bannerUrl", None),
                "memberCount": member_count,
                "featured": True,
            }
        )

    return items


async def get_public_detail(
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any] | None:
    """Get public detail for a single Circle.

    Returns None if the Circle is not found, not public, or hidden.
    """
    client = db_client or default_db

    circle = await client.circle.find_unique(
        where={"id": circle_id},
        include={"members": True},
    )

    if circle is None:
        return None

    if str(circle.visibility) != "PUBLIC":
        return None

    if circle.hiddenByModeration:
        return None

    member_count = len(circle.members) if circle.members else 0

    return {
        "id": circle.id,
        "name": circle.name,
        "description": circle.description,
        "category": circle.category,
        "avatarUrl": circle.avatarUrl,
        "bannerUrl": getattr(circle, "bannerUrl", None),
        "memberCount": member_count,
        "featured": getattr(circle, "featured", False),
        "circlePlanActive": circle.circlePlanActive,
        "joinPolicy": getattr(circle, "joinPolicy", "AUTO_JOIN"),
        "createdAt": circle.createdAt,
    }
