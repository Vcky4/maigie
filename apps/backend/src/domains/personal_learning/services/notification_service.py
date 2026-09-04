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

## Push

There is no push from this module any more. It used to attempt an FCM send that reached nobody — every
registered token was an Expo token and the sender spoke FCM — so the legacy delivery sweep, that FCM
sender, and the write-only `pushedAt` column were retired in Phase 7. Real push now flows through the
canonical notification pipeline (`NotificationDelivery` + the Expo dispatcher). What remains here is the
create-time facade: it writes the in-app row (deferring for quiet hours and the daily allowance) and, when
given a canonical `action` and `idempotency_key`, forwards to the notifications domain, which owns every
channel decision.
"""

import logging
from datetime import UTC, datetime
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


async def create_notification(
    *,
    user_id: str,
    type: str,
    title: str,
    body: str,
    priority: int = 5,
    action_data: dict | None = None,
    scheduled_at: datetime | None = None,
    canonical_type: str | None = None,
    action: dict[str, object] | None = None,
    idempotency_key: str | None = None,
    source_domain: str | None = None,
    source_entity_type: str | None = None,
    source_entity_id: str | None = None,
    group_key: str | None = None,
) -> Any:
    """Compatibility facade; canonical writes are owned by notifications."""
    if action is not None and idempotency_key is not None:
        from src.domains.notifications.service import create_notification as create_canonical

        return await create_canonical(
            user_id=user_id,
            type=canonical_type or type,
            title=title,
            body=body,
            priority=priority,
            action_data=action_data,
            scheduled_at=scheduled_at,
            action=action,
            idempotency_key=idempotency_key,
            source_domain=source_domain,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            group_key=group_key,
        )

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
