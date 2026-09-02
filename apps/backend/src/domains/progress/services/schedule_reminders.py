"""Email reminders for study blocks that are about to start.

Restores the behaviour of the pre-migration ``src/tasks/email_notifications``
``_send_schedule_reminders_impl``. That module could not be migrated as it stood: it
depended on the Prisma client, on an ``ai_email_service`` that was never migrated, and on
a parallel ``src/tasks`` framework (``base``, ``registry``, ``schedules``) that no longer
exists. Rebuilding that framework alongside the current Celery layout would have added a
second way to declare tasks, so the logic lives here as a plain service and the existing
worker in ``src/workers/notification_tasks`` calls it.

Two deliberate differences from the original:

* **Eligibility is ``entitlement_service.resolve``, not a tier comparison.** The original
  enumerated ``PREMIUM_MONTHLY``, ``STUDY_CIRCLE_*`` and ``SQUAD_*``; this then replaced it
  with ``tier != "FREE"``, reasoning that the rest of the codebase treated "not FREE" as
  paid. That stopped being true in the same week: ``entitlement_service`` is now the one
  resolver (MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision B), and a tier string cannot answer this
  question because it says nothing about a **trial** or a **pass**. The comparison was wrong
  in both directions — it excluded every trialling learner, who is supposed to be
  indistinguishable from a subscriber, and admitted five retired tiers the resolver denies.
  A fifth mechanism deciding "is this learner paid" is exactly what Decision B exists to
  prevent, and this one arrived inside notification plumbing where nobody was looking for a
  commercial policy decision.
* **No LLM-drafted copy.** A reminder has to arrive in the fifteen minutes before a block
  starts; making that wait on a model call adds a failure mode and a latency budget for no
  benefit the learner can perceive.

The window is deliberately narrow and the task is expected to run at roughly the same
cadence.

**This no longer sends email itself.** It produces one canonical notification per block and
the notification orchestrator decides the channels: in-app always, email and push only where
the learner has consented and the channel is enabled. That change fixed two things at once.
The reminder now appears in the notification centre rather than only in an inbox, and
"already sent" is no longer a gap in the schema — the idempotency key is the block id, so a
block cannot be reminded twice however often this runs.

Consent for a *channel* is deliberately not checked here any more; the orchestrator owns it
and rechecks it immediately before sending, so a preference changed after the reminder was
produced still takes effect. What remains here is the product rule about who gets reminders
at all, which is a paid-plan feature.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.domains.identity.db_models import User, UserPreferences
from src.domains.progress.db_models import ScheduleBlock
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

# Must match the schedule this task runs on, or blocks get reminded twice or not at all.
REMINDER_WINDOW_MINUTES = 15


def _resolve_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        # An unknown or malformed timezone must not lose the reminder.
        return ZoneInfo("UTC")


def _format_local_time(moment: datetime, tz: ZoneInfo) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).strftime("%I:%M %p on %A, %b %d")


async def _should_remind(user: Any, prefs: Any) -> bool:
    """Whether a reminder should exist for this user at all.

    Only the plan gate and account state, not channel consent: whether the reminder becomes
    an email, a push, or stays in the app is the orchestrator's decision, made later and
    rechecked at send time.

    Async because entitlement is a read now rather than a field comparison. One resolve per
    eligible block-owner per run; the window is fifteen minutes wide, so the volume is small.
    """
    if not user or not user.is_active:
        return False

    from src.domains.billing.services import entitlement_service

    entitlement = await entitlement_service.resolve(user.id)
    return entitlement.tier == "plus"


async def send_schedule_reminders() -> dict[str, int]:
    """Create one canonical reminder per study block starting inside the window.

    Returns counts rather than nothing, so the Celery task can log what it did and an
    empty run is distinguishable from a broken one.
    """
    from src.domains.notifications.service import create_notification

    now = datetime.now(UTC)
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)

    factory = get_session_factory()
    async with factory() as session:
        # ScheduleBlock has no `user` relationship, so User and UserPreferences are
        # joined explicitly. The preferences join is an outer join because the row is
        # optional and only supplies the timezone used to phrase the local start time.
        result = await session.execute(
            select(ScheduleBlock, User, UserPreferences)
            .join(User, User.id == ScheduleBlock.user_id)
            .outerjoin(UserPreferences, UserPreferences.user_id == User.id)
            .where(
                ScheduleBlock.start_at >= now,
                ScheduleBlock.start_at <= window_end,
            )
            .order_by(ScheduleBlock.start_at.asc())
        )
        rows = result.all()

    created = 0
    skipped = 0
    failed = 0

    for block, user, prefs in rows:
        if not await _should_remind(user, prefs):
            skipped += 1
            continue

        try:
            tz = _resolve_timezone(getattr(prefs, "timezone", None) if prefs else None)
            local_time = _format_local_time(block.start_at, tz)
            description = (block.description or "")[:200]
            body = f"{block.title} starts at {local_time}."
            if description:
                body = f"{body}\n{description}"

            await create_notification(
                user_id=user.id,
                type="learning.study_session_reminder",
                title=f"Starting soon: {block.title}",
                body=body,
                action={"version": 1, "kind": "OPEN_SESSION", "entityId": block.id},
                # The block is the unit of work and it starts once, so its id is the
                # whole key. Re-running this sweep cannot produce a second reminder.
                idempotency_key=f"schedule-reminder:{block.id}",
                priority=2,
                source_domain="progress",
                source_entity_type="schedule_block",
                source_entity_id=block.id,
            )
            created += 1
        except Exception:
            # One bad row must not stop the sweep.
            failed += 1
            logger.exception("Failed to create schedule reminder for block %s", block.id)

    summary = {
        "considered": len(rows),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }
    if rows:
        logger.info("Schedule reminders: %s", summary)
    return summary
