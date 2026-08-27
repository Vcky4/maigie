"""When not to disturb a learner, defined once.

There were two implementations of this, and they disagreed in both of the ways that matter.

`agenda_service._within_quiet_hours` converted the instant to the learner's own wall clock and treated the
window as half-open. `notification_service._is_during_quiet_hours` called `dt.time()` on an aware UTC
instant — **throwing the offset away** — and compared the resulting UTC wall clock against the learner's
stated `"22:00"`, with both bounds inclusive. So the agenda would refuse to schedule a session at 23:00
Lagos time while the notification path happily sent that same learner a push at 23:00, because 22:00 UTC is
23:00 in Lagos and the check was reading the wrong clock. The further from Greenwich a learner lived, the
further off the hour was.

Two definitions of "do not disturb" is worse than either one of them, because they are read by the same
learner: the app declines to plan work in their evening and then messages them during it.

The local, half-open version wins. Local because quiet hours are a statement about the learner's evening,
not about Greenwich's. Half-open because a window ending at 07:00 should not still be quiet *at* 07:00 —
with both bounds inclusive there is no instant at which a boundary minute is available.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .learner_timezone import LearnerTimezone, to_learner_local


def parse_hhmm(value: str | None) -> time | None:
    """`"22:00"` as a `time`, or `None` for anything unusable.

    Quiet hours are stored as nullable `"HH:MM"` strings on `LearningProfile`, which means malformed data is
    reachable — a hand-edited row, or an older client. **Unparseable fails open**, to `None`, which the
    predicate below reads as "no quiet hours".

    Open rather than closed deliberately, and it is the uncomfortable direction: it means a corrupt value
    results in a learner being messaged during what should have been their quiet hours. The alternative is
    worse in a way that is harder to notice — a single bad string would silence every notification for that
    learner forever, and nothing would report it. A message at a bad hour is visible and complainable; total
    silence is not.
    """
    if not value:
        return None
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
        return time(hour, minute)
    except (ValueError, IndexError, TypeError):
        return None


def is_within_quiet_hours(
    instant: datetime,
    timezone_: LearnerTimezone,
    quiet_from: time | None,
    quiet_to: time | None,
) -> bool:
    """Whether an instant falls in the learner's quiet hours, on **their** clock.

    Half-open: `quiet_from` is quiet, `quiet_to` is not. A window that crosses midnight is expressed by
    `quiet_from > quiet_to` and is handled by the second branch.

    Either bound missing means no quiet hours, because half a window is not a window and guessing the other
    end would invent a rule the learner never set.

    Note the timezone may be `UNKNOWN_TIMEZONE`, in which case this reads UTC and is no better than the
    behaviour it replaces — but it is no worse either, and it is now visibly conditional on something the
    caller can check rather than silently assumed.
    """
    if quiet_from is None or quiet_to is None:
        return False
    local = to_learner_local(instant, timezone_).time()
    if quiet_from <= quiet_to:
        return quiet_from <= local < quiet_to
    return local >= quiet_from or local < quiet_to


def next_end_of_quiet_hours(
    instant: datetime,
    timezone_: LearnerTimezone,
    quiet_to: time | None,
) -> datetime:
    """The next instant at which quiet hours are over, as a UTC instant.

    **Built in the learner's local wall clock and then converted**, rather than by adding hours to a UTC
    instant. The naive version — `dt.replace(hour=7)` on a UTC datetime — sets Greenwich's 07:00, which is
    a different moment from the learner's, and `+ timedelta(days=1)` across a daylight-saving boundary lands
    an hour out because a local day is not always 24 hours long.

    Returns `instant` unchanged when there is no end to wait for; the caller then delivers immediately,
    which is right, since a learner with no quiet hours configured has not asked to wait.
    """
    if quiet_to is None:
        return instant

    local = to_learner_local(instant, timezone_)
    end = local.replace(hour=quiet_to.hour, minute=quiet_to.minute, second=0, microsecond=0)
    if end <= local:
        # Tomorrow's end of quiet hours. Stepped on the **local** datetime, which keeps the wall clock at
        # `quiet_to` and lets the zone recompute the offset for the new date — so a window spanning a
        # daylight-saving change still ends at the hour the learner named. Adding a day to the UTC instant
        # instead would land an hour out, once a year, in a way nobody would trace back to here.
        end += timedelta(days=1)
    return end.astimezone(instant.tzinfo) if instant.tzinfo else end
