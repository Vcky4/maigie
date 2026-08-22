"""
Activity Feed service — unified feed of personal and collaborative activities.

Presents one continuous learning journey rather than separate products.
"""

import logging
from datetime import UTC, date, datetime, timezone
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


async def list_feed(
    *,
    user_id: str,
    activity_types: list[str] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """
    List unified activity feed entries (paginated, sorted by occurred_at desc).

    Includes both personal study and collaborative activity.
    Req 16.5

    The filters are optional and additive: omitting all three gives the previous behaviour exactly,
    which is why extending this could not break the existing caller. The total is computed under the
    same filters as the page — a filtered list beside an unfiltered count is a pagination control
    that walks off the end of its own list.
    """
    skip = (page - 1) * page_size
    return await repo.list_feed_entries(
        user_id,
        activity_types=activity_types,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        skip=skip,
        take=page_size,
    )


async def list_daily_counts(
    *,
    user_id: str,
    activity_types: list[str] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> list[tuple[date, int]]:
    """How much happened on each day in the window, oldest first.

    Its own read rather than a field on the paginated feed. The density strip covers a range far
    wider than any one page, so folding it into the envelope would either mean the counts describing
    only the current page — silently wrong — or the envelope carrying a whole-range aggregate that
    changes as the learner turns pages.
    """
    return await repo.count_feed_entries_by_day(
        user_id,
        activity_types=activity_types,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )


async def list_activity_types(*, user_id: str) -> list[str]:
    """The activity types this learner has, so the filter offers only what can return something."""
    return await repo.list_feed_activity_types(user_id)


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
