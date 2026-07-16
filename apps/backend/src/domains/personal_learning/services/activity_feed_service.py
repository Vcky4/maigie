"""
Activity Feed service — unified feed of personal and collaborative activities.

Presents one continuous learning journey rather than separate products.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def record(
    *,
    user_id: str,
    activity_type: str,
    title: str,
    description: str | None = None,
    context: dict | None = None,
) -> Any:
    """
    Record an activity in the feed.

    Called by services and event handlers when significant actions occur.
    Context dict indicates source: {"source": "personal"|"collaborative", ...}
    """
    now = datetime.now(timezone.utc)
    return await repo.create_feed_entry(
        {
            "userId": user_id,
            "activityType": activity_type,
            "title": title,
            "description": description,
            "context": context,
            "occurredAt": now,
        }
    )


async def list_feed(*, user_id: str, page: int = 1, page_size: int = 20) -> tuple[list[Any], int]:
    """
    List unified activity feed entries (paginated, sorted by occurred_at desc).

    Includes both personal study and collaborative activity.
    Req 16.5
    """
    skip = (page - 1) * page_size
    return await repo.list_feed_entries(user_id, skip=skip, take=page_size)
