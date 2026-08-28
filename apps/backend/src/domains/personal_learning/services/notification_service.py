"""Notification creation and delivery.

Every notification earns the right to exist. Right moment, right message.

Four phases of the adaptive-goal programme now depend on this path, and each one had to write a defensive
comment explaining that its message might silently vanish. Three defects caused that, and they are what this
module is about.

**Quiet hours were read off the wrong clock.** `_is_during_quiet_hours` called `dt.time()` on an aware UTC
instant, discarding the offset, and compared Greenwich's wall clock against the learner's stated `"22:00"`.
For a learner in Lagos the quiet window fired an hour early and ended an hour early; further from Greenwich,
further out. The predicate now lives in `src/shared/time/quiet_hours.py` and is **the same function object**
the agenda reads, which previously had its own correct implementation — so the app used to decline to plan a
session at 23:00 local while messaging that same learner during it.

**The daily cap destroyed messages rather than delaying them.** Over the allowance, `create_notification`
returned `None` and the notification simply never existed. That is indistinguishable, from the caller's side,
from never having tried — which is why `GoalLifecycleAction`, `PrepOutcome` and `StudyPlan.lastCheckInAt` all
had to record attempts instead of deliveries. The cap now **defers**: the row is written with `QUEUED` and
released at the start of the learner's next day. Nothing is thrown away, so the cap protects the learner's
attention without costing them information.

**Queued notifications were never delivered at all.** `list_pending_for_delivery` selected `PENDING` only,
so every notification quiet hours had ever deferred was written, given a later `scheduledAt`, and then read
by nothing. It still appeared in the in-app list, because that read filters on `READ`/`DISMISSED` rather than
on delivery, which is why this survived. That was the largest silent drop of the three and the plan does not
name it.

## What `create_notification` now guarantees

**It always returns the row.** There is no longer a suppression path that destroys a notification, so a
caller no longer has to treat `None` as "may or may not have happened". Volume defers; quiet hours defer.
Callers that count `if notification is not None` still work and are now simply always true.

## What is still not fixed

Push is attempted, honestly, and still reaches nobody — but for a narrower reason than before.
`PUT /users/me/device-tokens` now writes `DeviceToken` rows, so `no_tokens` is no longer universal. What
remains is a transport mismatch: every token registered so far is an `ExponentPushToken[...]`, issued by
Expo, while this application's sender speaks FCM. Those are skipped, not sent, and not pruned. Whichever way
that is resolved, `pushedAt` stays null until a device is actually addressed, so the data says what happened
instead of claiming a delivery. The in-app list is the channel that works today.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.shared.time import (
    UNKNOWN_TIMEZONE,
    LearnerTimezone,
    ensure_utc,
    is_within_quiet_hours,
    local_day_bounds,
    next_end_of_quiet_hours,
    parse_hhmm,
    resolve_learner_timezone,
    resolve_many,
)

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)

#: The band that outranks the learner's daily allowance.
#:
#: The allowance exists so a learner is not flooded with recommendations. It should not silence a message
#: about a date that passes tomorrow, and before this it did — `goal_at_risk` was priority 3, the same as the
#: daily plan, so the fifth recommendation of the day could bump the warning that an exam was in two days.
#:
#: Lower is more urgent, matching `list_unread`'s ascending sort. Only the messages whose value expires with
#: the deadline they describe belong here: everything else is *deferred* rather than dropped now, so it loses
#: nothing by waiting for tomorrow.
PRIORITY_TIME_CRITICAL = 1

#: The default allowance, when a learner has no profile row.
#:
#: Five, which is the number the column has always defaulted to. Named because it was written three times
#: across two functions as a bare `or 5`, and a limit that appears in several places is a limit that can
#: disagree with itself.
DEFAULT_MAX_DAILY = 5

#: How long a deferred notification stays worth delivering, in days.
#:
#: A message held back by quiet hours or a spent allowance is worth reading a few hours later. It is not
#: worth reading four days later: "your exam is in two days" arriving after the exam is worse than silence,
#: because the learner acts on it. Past this, the row is marked `EXPIRED` rather than delivered.
#:
#: This also bounds the first run after deployment. Queued rows have been accumulating unread for as long as
#: quiet hours have existed, and releasing all of them at once would be a flood of stale messages.
MAX_DEFERRAL_DAYS = 3

#: How many notifications one delivery sweep will handle.
#:
#: The sweep runs every five minutes under a 45 second soft limit and now does network I/O per row, so an
#: unbounded backlog would time out and make no progress rather than draining gradually.
DELIVERY_BATCH = 200

#: Which `UserPreferences` push toggle governs which notification type.
#:
#: The schema has offered `pushScheduleReminder` and `pushStudyTips` since before any of this existed and
#: **nothing has ever read either of them**. Starting to send push without consulting them would turn a
#: dormant column into a broken promise, so they are honoured here.
#:
#: A type absent from this map is allowed. That is the deliberate direction: inventing a mapping from, say,
#: `goal_at_risk` onto "study tips" would be reading consent into an answer the learner never gave about it.
#: The master `UserPreferences.notifications` switch still applies to everything.
_PUSH_TOGGLE_BY_TYPE = {
    "DAILY_PLAN": "push_schedule_reminder",
    "study_plan_check_in": "push_schedule_reminder",
    "study_plan_redistributed": "push_schedule_reminder",
    "ENGAGEMENT_NUDGE": "push_study_tips",
    "suggestion": "push_study_tips",
}


async def create_notification(
    *,
    user_id: str,
    type: str,
    title: str,
    body: str,
    priority: int = 5,
    action_data: dict | None = None,
    scheduled_at: datetime | None = None,
) -> Any:
    """Write a notification, and decide when the learner should see it.

    Always returns the row. The two things that used to suppress a notification now **defer** it, which is
    the whole point: a message the learner does not need at 3am is still a message they need.

    - **Quiet hours** hold it until they end, in the learner's own timezone. This applies to every priority,
      including `PRIORITY_TIME_CRITICAL` — a deadline a few hours away does not justify waking someone, and
      nothing here is urgent on the scale that would.
    - **The daily allowance** holds it until the start of their next day, unless it is time-critical, in
      which case it goes now. The allowance protects attention; it should not silence a date.
    """
    timezone_ = await _timezone_or_unknown(user_id)
    profile = await repo.get_profile_by_user(user_id)
    moment = ensure_utc(scheduled_at) if scheduled_at else datetime.now(UTC)

    quiet_from = parse_hhmm(getattr(profile, "quiet_hours_start", None))
    quiet_to = parse_hhmm(getattr(profile, "quiet_hours_end", None))

    status = "PENDING"
    release_at = moment

    if is_within_quiet_hours(moment, timezone_, quiet_from, quiet_to):
        status = "QUEUED"
        release_at = next_end_of_quiet_hours(moment, timezone_, quiet_to)
    elif priority > PRIORITY_TIME_CRITICAL and await _allowance_spent(
        user_id, profile=profile, timezone_=timezone_, moment=moment
    ):
        # Deferred, not dropped. The learner has had their fill of interruptions today; they have not
        # forfeited the information.
        status = "QUEUED"
        _, tomorrow = local_day_bounds(moment, timezone_)
        release_at = tomorrow
        logger.info(
            "Daily notification allowance spent; deferring to the learner's next day",
            extra={"user_id": user_id, "type": type, "release_at": release_at.isoformat()},
        )

    return await repo.create_notification(
        {
            "userId": user_id,
            "type": type,
            "title": title,
            "body": body,
            "priority": priority,
            "actionData": action_data,
            "scheduledAt": release_at,
            "status": status,
        }
    )


async def get_unread(*, user_id: str) -> list[Any]:
    """Unread notifications, most urgent first."""
    return await repo.list_unread(user_id)


async def mark_read(*, user_id: str, notification_id: str) -> None:
    await repo.mark_read(notification_id, user_id)


async def dismiss(*, user_id: str, notification_id: str) -> None:
    await repo.mark_dismissed(notification_id, user_id)


async def deliver_pending() -> int:
    """Release every notification whose moment has come. Returns how many were delivered.

    Called by `learning.notification_delivery` every five minutes.

    Three things happen per row, in an order chosen for how each one fails:

    1. **Still quiet?** Left alone, to be reconsidered in five minutes. Checked again here rather than
       trusted from creation time, because a learner can change their quiet hours after a notification was
       scheduled.
    2. **Too late to matter?** Marked `EXPIRED`. A deferred message that arrives four days after the deadline
       it describes is worse than one that never arrives, because the learner acts on it.
    3. **Otherwise delivered, and then pushed.** The status write comes *first*, deliberately. It is what
       stops the row being selected again, so a crash between the two loses a push rather than repeating one
       — the right way round for something that buzzes a phone in a pocket.

    Per-row error containment, because one learner's bad row must not strand every other learner's backlog.
    Timezones are resolved for the whole batch in one query, and each learner's profile is read once however
    many notifications they have waiting.
    """
    due = await repo.list_due_for_delivery(limit=DELIVERY_BATCH)
    if not due:
        return 0

    now = datetime.now(UTC)
    timezones = await resolve_many(sorted({row.user_id for row in due}))
    profiles: dict[str, Any] = {}
    delivered = 0

    for row in due:
        try:
            timezone_ = timezones.get(row.user_id, UNKNOWN_TIMEZONE)
            if row.user_id not in profiles:
                profiles[row.user_id] = await repo.get_profile_by_user(row.user_id)
            profile = profiles[row.user_id]

            if is_within_quiet_hours(
                now,
                timezone_,
                parse_hhmm(getattr(profile, "quiet_hours_start", None)),
                parse_hhmm(getattr(profile, "quiet_hours_end", None)),
            ):
                continue

            if ensure_utc(row.scheduled_at) < now - timedelta(days=MAX_DEFERRAL_DAYS):
                await repo.update_status(row.id, status="EXPIRED")
                logger.info(
                    "Notification expired before it could be delivered",
                    extra={"notification_id": row.id, "type": row.type},
                )
                continue

            await repo.update_status(row.id, status="DELIVERED", delivered_at=now)
            delivered += 1
            await _push(row, profile_type=row.type)
        except Exception:
            logger.exception(
                "Could not deliver one notification",
                extra={"notification_id": row.id, "user_id": row.user_id},
            )

    return delivered


async def _push(row: Any, *, profile_type: str) -> None:
    """Best-effort push for a notification already released in-app.

    Never raises and never reverses the delivery. The in-app list is the channel that works; a push is an
    extra, and its failure is not the notification's failure.

    `pushedAt` is written **only when a device actually received something** — not when a send was merely
    attempted. Today that means it stays null for everyone: registered tokens are Expo tokens and the sender
    speaks FCM, so the result is `skipped`. Recording an attempt as a push would be a claim in the database
    that nothing sent anything to.
    """
    from src.shared.infrastructure.push_notifications import send_push_notification

    try:
        if not await _push_allowed(row.user_id, profile_type):
            return
        result = await send_push_notification(
            user_id=row.user_id,
            title=row.title,
            body=row.body,
            data={str(k): str(v) for k, v in (row.action_data or {}).items()},
        )
        if result.get("sent"):
            await repo.update_status(row.id, status="DELIVERED", pushed_at=datetime.now(UTC))
    except Exception:
        logger.exception("Push failed for a delivered notification", extra={"id": row.id})


async def _push_allowed(user_id: str, notification_type: str) -> bool:
    """Whether this learner has agreed to be pushed about this kind of thing.

    Reads preferences that have existed unread for the whole life of the schema. The master switch governs
    everything; the two granular toggles govern the types they plainly describe, and a type they do not
    describe is allowed rather than mapped onto the nearest-sounding one.

    Fails **closed** on an error, which is the opposite of `parse_hhmm`'s choice and for the opposite reason:
    a missing preference row must not become consent to push. An unsent push costs the learner nothing —
    the notification is already in their list.
    """
    from sqlalchemy import select

    from src.domains.identity.db_models import UserPreferences
    from src.shared.database import get_session_factory

    toggle = _PUSH_TOGGLE_BY_TYPE.get(notification_type)
    try:
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    select(UserPreferences).where(UserPreferences.user_id == user_id)
                )
            ).scalar_one_or_none()
    except Exception:
        logger.exception("Could not read push preferences", extra={"user_id": user_id})
        return False

    if row is None:
        return False
    if not row.notifications:
        return False
    return True if toggle is None else bool(getattr(row, toggle, True))


async def _allowance_spent(
    user_id: str, *, profile: Any | None, timezone_: LearnerTimezone, moment: datetime
) -> bool:
    """Whether this learner has already had their day's worth of interruptions.

    Counted over the learner's **own** day. The window used to be a UTC calendar day, so the allowance
    refilled at 01:00 for a learner in Lagos and at 16:00 for one in Los Angeles — the second could be
    messaged their full quota twice inside one working day.
    """
    since, until = local_day_bounds(moment, timezone_)
    max_daily = getattr(profile, "max_daily_notifications", None) or DEFAULT_MAX_DAILY
    delivered = await repo.count_delivered_between(user_id, since=since, until=until)
    return delivered >= max_daily


async def _timezone_or_unknown(user_id: str) -> LearnerTimezone:
    """The learner's timezone, never raising.

    `resolve_learner_timezone` already tolerates missing rows and unparseable zones, but it opens its own
    session; a database blip while creating a notification should not lose the notification. Unknown reads
    as UTC, which is the behaviour being replaced — no worse, and now conditional on something a caller
    could check rather than silently assumed.
    """
    try:
        return await resolve_learner_timezone(user_id)
    except Exception:
        logger.exception("Could not resolve learner timezone", extra={"user_id": user_id})
        return UNKNOWN_TIMEZONE
