"""
Activity Feed service — unified feed of personal and collaborative activities.

Presents one continuous learning journey rather than separate products.
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Any, Literal

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


#: Artifacts an activity entry can point at. Closed, because the whole purpose of the pair below is
#: that a consumer can switch on it without knowing which service wrote the row.
ActivityEntity = Literal[
    "note",
    "document",
    "flashcard",
    "study_plan",
    "preparation",
    "quiz",
    "course",
    "topic",
]


async def record(
    *,
    user_id: str,
    activity_type: str,
    title: str,
    entity_type: ActivityEntity,
    entity_id: str,
    description: str | None = None,
    context: dict | None = None,
) -> Any:
    """
    Record an activity in the feed.

    Called by services and event handlers when significant actions occur.
    Context dict indicates source: {"source": "personal"|"collaborative", ...}

    ``entity_type`` and ``entity_id`` are **required**, and that is the point of them. Every writer
    already put an id in ``context`` and each chose its own key — `noteId`, `docId`, `cardId`,
    `prepId`, `quizId`, `planId` — so a reader wanting to make an entry clickable had to know all six
    names, and a seventh writer would invent a seventh. They are folded into the context under the
    fixed keys ``entityType`` and ``entityId``, which is what makes the feed routeable from one branch
    instead of six.

    Required rather than optional so a new writer cannot skip them: an optional field here would be
    populated by the six callers that exist today and forgotten by the next one, which is precisely
    how the six key names happened. The existing per-service keys are kept — they are already stored
    on historical rows, and dropping them would make old entries and new ones disagree about shape.
    """
    now = datetime.now(UTC)
    merged_context = {**(context or {}), "entityType": entity_type, "entityId": entity_id}
    entry = await repo.create_feed_entry(
        {
            "userId": user_id,
            "activityType": activity_type,
            "title": title,
            "description": description,
            "context": merged_context,
            "occurredAt": now,
        }
    )

    # Check streak milestone after recording activity
    try:
        streak = await _compute_current_streak(user_id)
        if streak >= 7:
            from . import milestone_service

            await milestone_service.check_milestones(user_id, {"current_streak": streak})
    except Exception:
        pass  # Don't let milestone check failure break activity recording

    return entry


async def list_feed(*, user_id: str, page: int = 1, page_size: int = 20) -> tuple[list[Any], int]:
    """
    List unified activity feed entries (paginated, sorted by occurred_at desc).

    Includes both personal study and collaborative activity.
    Req 16.5
    """
    skip = (page - 1) * page_size
    return await repo.list_feed_entries(user_id, skip=skip, take=page_size)


async def _compute_current_streak(user_id: str) -> int:
    """
    Compute the current consecutive-day streak for a user.

    Counts backwards from today: how many consecutive days have at least one activity.
    """
    from sqlalchemy import Date, cast, func, select

    from src.domains.personal_learning.db_models import ActivityFeedEntry
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Get distinct activity dates in the last 30 days, ordered desc
        thirty_days_ago = datetime.now(UTC) - __import__("datetime").timedelta(days=30)
        stmt = (
            select(func.distinct(cast(ActivityFeedEntry.occurred_at, Date)))
            .where(ActivityFeedEntry.user_id == user_id)
            .where(ActivityFeedEntry.occurred_at >= thirty_days_ago)
            .order_by(cast(ActivityFeedEntry.occurred_at, Date).desc())
        )
        result = await session.execute(stmt)
        dates = [row[0] for row in result.all()]

    if not dates:
        return 0

    # Count consecutive days from today backwards
    today = datetime.now(UTC).date()
    streak = 0

    for i, d in enumerate(dates):
        expected = today - __import__("datetime").timedelta(days=i)
        if d == expected:
            streak += 1
        else:
            break

    return streak
