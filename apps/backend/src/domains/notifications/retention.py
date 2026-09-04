"""Data retention for the notification platform (Phase 7).

The platform accumulates operational and evidence rows for every notification it sends —
per-attempt delivery records, interaction events, decision audits, digest runs, provider webhook
events. They are worth keeping for a while (debugging a delivery, measuring an outcome, auditing a
decision) and worth deleting eventually (a two-year-old email-accepted attempt tells no one
anything, and holding learner data longer than it is useful is its own liability).

Three rules make this safe to run unattended:

  - **Fail-closed.** Nothing is deleted unless `NOTIFICATION_RETENTION_ENABLED` is `True`. The default
    is off, because deleting learner data is irreversible and the windows are a policy decision, not a
    default this code should assume.
  - **Evidence only, never the learner's history.** The sweep prunes `NotificationDeliveryAttempt`,
    `NotificationDelivery`, `NotificationInteraction`, `NotificationDecision`, `NotificationDigest`,
    and `EmailProviderEvent`. It never deletes a `Notification` row — that is what a learner sees in
    their notification centre, and its lifetime is a separate, more sensitive question.
  - **Only settled rows, in bounded batches.** Delivery rows are pruned only in a terminal state, so a
    still-in-flight delivery (`PLANNED`/`QUEUED`/`SENDING`) is preserved however old it is — an old
    in-flight row is a bug to investigate, not garbage to collect. Every table is deleted in batches so
    a large backlog holds only short row locks and a run interrupted between batches leaves no
    half-finished transaction.

Cascades do the rest: deleting a `NotificationDelivery` removes its attempts, and deleting a
`NotificationDigest` removes its items, both by `ON DELETE CASCADE`. Deleting a `NotificationDecision`
nulls the `Notification.intelligenceDecisionId` link (`ON DELETE SET NULL`), so the notification
survives with its audit pointer cleared.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from src.config import Settings, get_settings
from src.shared.database import get_session_factory

from .db_models import (
    EmailProviderEvent,
    NotificationDecision,
    NotificationDelivery,
    NotificationDigest,
    NotificationInteraction,
)

logger = logging.getLogger(__name__)

#: Delivery states that are settled — the provider request is finished, one way or another. An
#: in-flight row (PLANNED/QUEUED/SENDING) is never pruned, so retention can never race the dispatcher
#: or delete something still waiting to be sent.
_TERMINAL_DELIVERY_STATES = (
    "ACCEPTED",
    "DELIVERED",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    "SUPPRESSED",
)

#: A hard ceiling on batches per table per run, so a pathological backlog cannot spin forever inside
#: one sweep. Anything left is picked up by the next scheduled run.
_MAX_BATCHES = 10_000


async def _prune_in_batches(model: Any, id_predicate, *, batch: int) -> int:
    """Delete rows matching `id_predicate` in batches, returning the total removed.

    Each batch is its own transaction — short locks, and a crash between batches simply leaves the
    remainder for the next pass. Selecting a bounded set of ids and deleting by id keeps every
    statement's lock footprint to `batch` rows rather than locking the whole matched range.
    """

    factory = get_session_factory()
    removed = 0
    for _ in range(_MAX_BATCHES):
        async with factory() as session, session.begin():
            ids = list(
                (await session.execute(select(model.id).where(id_predicate).limit(batch)))
                .scalars()
                .all()
            )
            if not ids:
                return removed
            result = await session.execute(delete(model).where(model.id.in_(ids)))
            removed += int(getattr(result, "rowcount", 0) or 0)
        if len(ids) < batch:
            return removed
    logger.warning("Retention hit the per-run batch ceiling for %s; more remains", model.__name__)
    return removed


async def prune_expired(
    *, now: datetime | None = None, settings: Settings | None = None
) -> dict[str, int]:
    """Delete notification evidence past its retention window. Returns per-table counts.

    A no-op that returns zeros when retention is disabled, so scheduling it costs nothing until an
    operator turns it on. Each table has its own window because they age differently: a delivery
    attempt is debugging exhaust, an interaction is an outcome measurement worth keeping longer.
    """

    config = settings or get_settings()
    counts = {
        "deliveries": 0,
        "interactions": 0,
        "decisions": 0,
        "digests": 0,
        "emailEvents": 0,
    }
    if not config.NOTIFICATION_RETENTION_ENABLED:
        return counts

    moment = now or datetime.now(UTC)
    batch = config.NOTIFICATION_RETENTION_BATCH

    def cutoff(days: int) -> datetime:
        return moment - timedelta(days=days)

    # Deliveries: terminal only, so nothing in flight is ever touched. Attempts cascade.
    counts["deliveries"] = await _prune_in_batches(
        NotificationDelivery,
        (NotificationDelivery.status.in_(_TERMINAL_DELIVERY_STATES))
        & (NotificationDelivery.created_at < cutoff(config.NOTIFICATION_RETENTION_DELIVERY_DAYS)),
        batch=batch,
    )
    # Interactions: keyed on when the response happened, not when the row was written.
    counts["interactions"] = await _prune_in_batches(
        NotificationInteraction,
        NotificationInteraction.occurred_at
        < cutoff(config.NOTIFICATION_RETENTION_INTERACTION_DAYS),
        batch=batch,
    )
    # Decisions: the FK from Notification nulls on delete, so the notification survives.
    counts["decisions"] = await _prune_in_batches(
        NotificationDecision,
        NotificationDecision.created_at < cutoff(config.NOTIFICATION_RETENTION_DECISION_DAYS),
        batch=batch,
    )
    # Digest runs: items cascade.
    counts["digests"] = await _prune_in_batches(
        NotificationDigest,
        NotificationDigest.created_at < cutoff(config.NOTIFICATION_RETENTION_DIGEST_DAYS),
        batch=batch,
    )
    # Provider webhook events: append-only, keyed on when the event occurred.
    counts["emailEvents"] = await _prune_in_batches(
        EmailProviderEvent,
        EmailProviderEvent.occurred_at < cutoff(config.NOTIFICATION_RETENTION_EMAIL_EVENT_DAYS),
        batch=batch,
    )

    if any(counts.values()):
        logger.info("Notification retention sweep completed", extra=counts)
    return counts
