"""Resolving a learner's own timezone, and saying so when we cannot.

Every timestamp in the database is a UTC instant, which is correct. The problem is
that several things we want to say are claims about a learner's *local* wall clock:
when they practise well, when their day ends, when not to notify them. Converting
requires knowing where they are, and until now nothing did the conversion — the
timezone column existed and no code in the learning path read it.

The trap this module exists to close: ``UserPreferences.timezone`` is ``NOT NULL``
with a ``"UTC"`` default, so reading it naively makes every learner who has never
been asked look like a learner in London. Any conclusion drawn from that is wrong
for most of the world, and wrong silently, which is worse. So resolution returns
``is_known`` alongside the zone, and callers that make a *claim* to a learner are
expected to check it.

Three existing bugs share that root cause and are why this is shared rather than
local to one service:

- ``notification_service`` compares a learner's ``"22:00"`` quiet hours against
  ``datetime.now(UTC).time()``, so quiet hours fire at the wrong wall-clock time
  for anyone outside UTC.
- ``prep_snapshot_service`` truncates to a UTC date, so a learner east or west of
  UTC has their readiness "day" boundary in the wrong place.
- ``memory_impl`` reports a raw UTC hour to the learner as their most productive
  study time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: Sources that count as an observation rather than a default.
_TRUSTED_SOURCES = frozenset({"DEVICE", "MANUAL"})

_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class LearnerTimezone:
    """A learner's timezone, and whether we actually know it.

    ``zone`` is always usable so display code needs no special case. ``is_known``
    is the part that matters for anything asserted back to the learner: when it is
    ``False`` the zone is a fallback, not a fact, and a claim about their local
    time should be withheld rather than guessed.
    """

    zone: ZoneInfo
    name: str
    is_known: bool
    source: str | None = None

    @property
    def is_assumed(self) -> bool:
        return not self.is_known


#: What we fall back to. UTC, and flagged as not known, so it cannot be mistaken
#: for a learner who is genuinely in UTC.
UNKNOWN_TIMEZONE = LearnerTimezone(zone=_UTC, name="UTC", is_known=False, source=None)


def _from_parts(name: str | None, source: str | None) -> LearnerTimezone:
    """Build a result from a stored name and source, tolerating bad data.

    An unparseable zone is treated as unknown rather than raising: a corrupt or
    retired IANA name in one row must not break a read for that learner.
    """
    if not name or source not in _TRUSTED_SOURCES:
        return UNKNOWN_TIMEZONE
    try:
        return LearnerTimezone(zone=ZoneInfo(name), name=name, is_known=True, source=source)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Stored timezone could not be resolved; treating as unknown",
            extra={"timezone": name, "timezone_source": source},
        )
        return UNKNOWN_TIMEZONE


async def resolve_learner_timezone(user_id: str) -> LearnerTimezone:
    """The learner's timezone, or ``UNKNOWN_TIMEZONE`` if it was never captured.

    Deliberately never raises. A missing preferences row, an unknown user and an
    unparseable zone all resolve to unknown, because the caller's decision is the
    same in every case: fall back, and do not claim a local time.
    """
    from sqlalchemy import select

    from src.domains.identity.db_models import UserPreferences
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(UserPreferences.timezone, UserPreferences.timezone_source).where(
            UserPreferences.user_id == user_id
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

    if row is None:
        return UNKNOWN_TIMEZONE
    return _from_parts(row[0], row[1])


async def resolve_many(user_ids: list[str]) -> dict[str, LearnerTimezone]:
    """Resolve several learners in one query.

    For batch jobs — the daily behaviour analyser would otherwise issue a query
    per learner. Learners with no preferences row are present in the result and
    map to unknown, so callers never have to distinguish missing from unknown.
    """
    if not user_ids:
        return {}

    from sqlalchemy import select

    from src.domains.identity.db_models import UserPreferences
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = select(
            UserPreferences.user_id,
            UserPreferences.timezone,
            UserPreferences.timezone_source,
        ).where(UserPreferences.user_id.in_(user_ids))
        result = await session.execute(stmt)
        rows = result.all()

    resolved = {row[0]: _from_parts(row[1], row[2]) for row in rows}
    for user_id in user_ids:
        resolved.setdefault(user_id, UNKNOWN_TIMEZONE)
    return resolved


def to_learner_local(instant: datetime, timezone: LearnerTimezone) -> datetime:
    """Convert a stored instant into the learner's wall clock.

    A naive input is read as UTC, which matches how these columns are written —
    the alternative, reading it as local, would shift every legacy row.
    """
    aware = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
    return aware.astimezone(timezone.zone)


def local_hour(instant: datetime, timezone: LearnerTimezone) -> int:
    """The hour of the learner's day an instant fell in, 0-23.

    The unit for any time-of-day aggregation. Note that this is only meaningful
    when ``timezone.is_known``; with an assumed zone it returns a UTC hour, which
    is fine for bucketing internal telemetry and not fine for telling a learner
    when they study best.
    """
    return to_learner_local(instant, timezone).hour
