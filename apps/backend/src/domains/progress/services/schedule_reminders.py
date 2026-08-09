"""Email reminders for study blocks that are about to start.

Restores the behaviour of the pre-migration ``src/tasks/email_notifications``
``_send_schedule_reminders_impl``. That module could not be migrated as it stood: it
depended on the Prisma client, on an ``ai_email_service`` that was never migrated, and on
a parallel ``src/tasks`` framework (``base``, ``registry``, ``schedules``) that no longer
exists. Rebuilding that framework alongside the current Celery layout would have added a
second way to declare tasks, so the logic lives here as a plain service and the existing
worker in ``src/workers/notification_tasks`` calls it.

Two deliberate differences from the original:

* **Eligibility is ``tier != "FREE"``, not an allowlist.** The original enumerated
  ``PREMIUM_MONTHLY``, ``STUDY_CIRCLE_*`` and ``SQUAD_*``. Every one of those names is
  retired, so that check would now silently exclude paying subscribers on
  ``plus_monthly`` and ``plus_yearly``. The rest of the codebase treats "not FREE" as
  paid, and so does this.
* **No LLM-drafted copy.** See ``send_schedule_reminder_email``.

The window is deliberately narrow and the task is expected to run at roughly the same
cadence. A block is reminded once because a block only starts once; there is no
"already sent" column, so running this far more often than the window would email twice.
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


def _should_remind(user: Any, prefs: Any) -> bool:
    """Whether this user has opted in to schedule reminder email."""
    if not user or not user.email:
        return False
    if str(getattr(user, "tier", "FREE") or "FREE") == "FREE":
        return False

    if prefs is None:
        # No preferences row means defaults, and every relevant column defaults to true.
        return True
    if not getattr(prefs, "notifications", True):
        return False
    return bool(getattr(prefs, "email_schedule_reminder", True))


async def send_schedule_reminders() -> dict[str, int]:
    """Email every eligible user whose study block starts within the window.

    Returns counts rather than nothing, so the Celery task can log what it did and an
    empty run is distinguishable from a broken one.
    """
    from src.shared.infrastructure.email import send_schedule_reminder_email

    now = datetime.now(UTC)
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)

    factory = get_session_factory()
    async with factory() as session:
        # ScheduleBlock has no `user` relationship, so User and UserPreferences are
        # joined explicitly. The preferences join is an outer join because the row is
        # optional and its absence means "all defaults", not "opted out".
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

    sent = 0
    skipped = 0
    failed = 0

    for block, user, prefs in rows:
        if not _should_remind(user, prefs):
            skipped += 1
            continue

        try:
            tz = _resolve_timezone(getattr(prefs, "timezone", None) if prefs else None)
            description = (block.description or "")[:200] or None

            await send_schedule_reminder_email(
                email=user.email,
                name=user.name,
                schedule_title=block.title,
                schedule_time=_format_local_time(block.start_at, tz),
                schedule_description=description,
            )
            sent += 1
        except Exception:
            # One bad row must not stop the sweep.
            failed += 1
            logger.exception("Failed to send schedule reminder for block %s", block.id)

    summary = {"considered": len(rows), "sent": sent, "skipped": skipped, "failed": failed}
    if rows:
        logger.info("Schedule reminders: %s", summary)
    return summary
