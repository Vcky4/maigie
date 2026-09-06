"""Reading instants back out of a database that stores most of them without an offset.

**176 of this database's 283 datetime columns are `timestamp without time zone`, and 107 are not.**
The split is historical: the Prisma-era tables carry no offset, while tables created since — the daily
snapshots, the goal progress history, the narrative cache — are `timestamptz`. Every ORM model declares
`DateTime(timezone=True)` regardless, and asyncpg honours the *column*, so which of the two a read
returns is decided by the table it came from and nothing else.

That has already cost four production defects, each identical in shape and each found only by running
the code against real rows:

1. `GET /progress/goals` returned 500 for any goal with a target date — `Goal.targetDate` is naive and
   every pace predicate compared it against an aware `datetime.now(UTC)`.
2. The home surface's weekly minutes silently degraded to "not measured", because `StudySession.startTime`
   is naive and the week-window comparison raised before the sum could happen.
3. Subject detail and subject insight returned 500 for any course holding *both* a dated topic completion
   (`Topic.completedAt`, aware) and a study session (naive) — the evidence merge sorts one list containing
   both, and `sort` cannot order a naive instant against an aware one.
4. The same latent break in the milestone merge: `Achievement.unlockedAt` is naive while
   `LearningMilestone` is aware, so it fails for the first learner who holds rows in both tables.

`ensure_utc` is the one place that rule lives. A naive value is read **as UTC**, which is how these
columns are written — `to_learner_local` already made the same choice, and the alternative, reading it as
server-local, would shift every legacy row by the offset of whichever machine did the reading.

Apply it where instants from different tables meet: any list that gets merged, sorted, bucketed or
compared against `now`. Applying it at the point a value enters a structure — a dataclass's
`__post_init__` — is better than at each call site, because a reader added later cannot forget.
"""

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """An aware UTC instant, whichever kind of column it came from.

    Idempotent, so it is safe to apply again to a value that has already been through it, and safe to
    apply to a value from an aware column — an offset that is already present is preserved and converted
    rather than overwritten.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def ensure_utc_optional(value: datetime | None) -> datetime | None:
    """`ensure_utc` for a nullable column. `None` stays `None` — an absent instant is not an instant."""
    return None if value is None else ensure_utc(value)
