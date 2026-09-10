"""Retention for anonymous landing drafts.

A draft is created by a stranger on the marketing site, holds the email address and study context
they typed, and stops being usable seven days later. Expiry was lazy from the start — every read
compares `expiresAt`, so an unswept draft is already unusable — which made the table correct but
made retention nobody's job: the rows, and the addresses on them, stayed forever.

Three steps, because "expired", "no longer holds personal data" and "gone" are different events and
collapsing them loses something each time:

  1. **Mark** open rows past their expiry as `expired`. Housekeeping — reads already treat them that
     way. It exists so unclaimed-draft volume is countable without recomputing expiry per row.
  2. **Redact** the email at expiry. The address was collected to hold a setup for seven days; once
     the setup cannot be used, the address has no purpose, so its retention ends exactly where the
     setup's does. This runs on claimed rows too, where the address is already on the account.
  3. **Delete** unclaimed rows once their expiry is `LANDING_DRAFT_RETENTION_GRACE_DAYS` old. The
     grace window is what gives step 1 a point: `expired` is a state the table can be counted in for
     a while rather than a status written moments before the row disappears.

Claimed drafts survive step 3 deliberately. The row is the record that the marketing site converted
someone — a fact about the site, not about the account, which is why `claimedBy` is
`ON DELETE SET NULL` — and after step 2 what remains is a purpose, a status and two timestamps.

**Not fail-closed, unlike notification retention.** That sweep defaults to off because deleting a
learner's history is irreversible and the windows are a policy call. This one is the opposite: the
privacy policy tells visitors their draft is deleted, so a sweep that defaults to off would make the
published copy false. `LANDING_DRAFT_RETENTION_ENABLED` exists to switch it off in an incident, not
to wait for an operator to switch it on.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from src.config import Settings, get_settings

from .repository import landing_draft_repo

logger = logging.getLogger(__name__)

#: A ceiling on batches per step per run, so a pathological backlog cannot spin forever inside one
#: sweep. Whatever is left is picked up by the next scheduled run.
_MAX_BATCHES = 1_000


async def _in_batches(step: Callable[[int], Awaitable[int]], *, batch: int, label: str) -> int:
    """Run a bounded repository step until it stops finding work, returning the total.

    Each call is its own transaction inside the repository, so a run interrupted between batches
    leaves the remainder for next time rather than a half-finished transaction. A step that reports
    fewer rows than it was allowed has drained its queue and ends the loop.
    """
    total = 0
    for _ in range(_MAX_BATCHES):
        changed = await step(batch)
        total += changed
        if changed < batch:
            return total
    logger.warning("Landing draft retention hit the per-run batch ceiling for %s", label)
    return total


async def sweep_expired(
    *, now: datetime | None = None, settings: Settings | None = None
) -> dict[str, int]:
    """Mark, redact and delete expired landing drafts. Returns per-step counts.

    `now` and `settings` are injectable so the windows can be exercised without waiting a week or
    editing the environment.
    """
    config = settings or get_settings()
    counts = {"marked": 0, "redacted": 0, "deleted": 0}
    if not config.LANDING_DRAFT_RETENTION_ENABLED:
        return counts

    moment = now or datetime.now(UTC)
    batch = config.LANDING_DRAFT_RETENTION_BATCH
    delete_cutoff = moment - timedelta(days=config.LANDING_DRAFT_RETENTION_GRACE_DAYS)

    counts["marked"] = await _in_batches(
        lambda limit: landing_draft_repo.expire_due(limit=limit),
        batch=batch,
        label="mark",
    )
    counts["redacted"] = await _in_batches(
        lambda limit: landing_draft_repo.redact_expired(now=moment, limit=limit),
        batch=batch,
        label="redact",
    )
    counts["deleted"] = await _in_batches(
        lambda limit: landing_draft_repo.delete_expired(cutoff=delete_cutoff, limit=limit),
        batch=batch,
        label="delete",
    )

    if any(counts.values()):
        logger.info(
            "Landing draft retention sweep: marked=%d redacted=%d deleted=%d",
            counts["marked"],
            counts["redacted"],
            counts["deleted"],
        )
    return counts
