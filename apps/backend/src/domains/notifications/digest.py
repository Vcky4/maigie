"""Bundling held notifications into one digest per learner, category, and period.

A digest preference used to mean almost nothing: only types that were themselves periodic were
emailed, and every other digestible notification was recorded as unsupported and never sent. This
is the missing half — the learner asked to hear about these together, so they are collected and
sent together.

Three decisions shape the implementation.

**Periods are the learner's, not the server's.** A week ends at a different instant in every
timezone, so bounds come from `local_week_bounds`/`local_day_bounds`, which are DST-correct: a week
containing a spring-forward is 167 hours, not 168. A UTC window would summarise somebody else's
week and would close on a Sunday afternoon for a learner in Auckland.

**The planner runs often and usually does nothing.** Because periods close at different moments, it
has to wake up hourly and ask "has this learner's period ended, and have I already summarised it?"
The unique key on the digest row is what makes that safe to repeat.

**A digest is per settings category.** Consent is expressed per category, so a learner who asked
for a weekly Learning digest has said nothing about Progress. One cross-category digest would
either send more than they agreed to or withhold what they asked for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import get_settings
from src.shared.time import LearnerTimezone, local_day_bounds, local_week_bounds

from .digest_copy import resolve_digest_copy
from .feature_flags import capability_enabled_for
from .repository import notification_repo
from .taxonomy import notification_spec

logger = logging.getLogger(__name__)

#: The settings categories that can produce a digest, mapped to the canonical type of the digest
#: they emit and the database categories whose notifications they collect. `PRODUCT_UPDATES` is
#: absent because no digestible type belongs to `OPERATIONS`; emitting an always-empty digest for
#: it would be dead code pretending to be a feature.
DIGEST_CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "LEARNING": ("learning.digest", ("LEARNING",)),
    "PROGRESS": ("progress.digest", ("PROGRESS",)),
    "SOCIAL_CLASSROOM": ("social.digest", ("SOCIAL", "CLASSROOM")),
}

_TITLES: dict[str, dict[str, str]] = {
    "LEARNING": {"DAILY": "Your learning today", "WEEKLY": "Your learning this week"},
    "PROGRESS": {"DAILY": "Your progress today", "WEEKLY": "Your progress this week"},
    "SOCIAL_CLASSROOM": {
        "DAILY": "Classroom activity today",
        "WEEKLY": "Classroom activity this week",
    },
}


@dataclass(frozen=True)
class DigestPeriod:
    period: str
    start: datetime
    end: datetime


def _timezone_of(policy) -> LearnerTimezone:
    try:
        return LearnerTimezone(
            zone=ZoneInfo(policy.timezone),
            name=policy.timezone,
            is_known=True,
            source=policy.timezone_source,
        )
    except (ZoneInfoNotFoundError, ValueError):
        return LearnerTimezone(zone=ZoneInfo("UTC"), name="UTC", is_known=False, source=None)


def _timezone_named(name: str) -> LearnerTimezone:
    """Rebuild a `LearnerTimezone` from just its name, for the heavy-queue task.

    Only the name crosses the broker — the `source` is preferences metadata that does not affect the
    period bounds, which is all `completed_period` reads from this. An unknown name falls back to UTC,
    the same as `_timezone_of`, so a corrupted arg cannot lose the digest.
    """
    try:
        return LearnerTimezone(zone=ZoneInfo(name), name=name, is_known=True, source=None)
    except (ZoneInfoNotFoundError, ValueError):
        return LearnerTimezone(zone=ZoneInfo("UTC"), name="UTC", is_known=False, source=None)


def completed_period(
    period: str, now: datetime, timezone_: LearnerTimezone, *, digest_day_of_week: int
) -> DigestPeriod | None:
    """The most recently *finished* period, or ``None`` if none has closed yet.

    Always the previous period, never the current one: summarising a period still in progress
    would send a partial week and then have nothing left to say when it actually ended.

    `digestDayOfWeek` uses 0 for Sunday in the settings contract, while Python's `weekday()` uses
    0 for Monday. Converted here, explicitly, because the two conventions genuinely differ and a
    silent off-by-one would shift every learner's digest by a day.
    """

    if period == "DAILY":
        start, end = local_day_bounds(now, timezone_)
        previous_start, previous_end = local_day_bounds(start - _one_minute(), timezone_)
        return DigestPeriod("DAILY", previous_start, previous_end)
    if period == "WEEKLY":
        week_starts_on = (digest_day_of_week - 1) % 7
        start, _end = local_week_bounds(now, timezone_, week_starts_on=week_starts_on)
        previous_start, previous_end = local_week_bounds(
            start - _one_minute(), timezone_, week_starts_on=week_starts_on
        )
        return DigestPeriod("WEEKLY", previous_start, previous_end)
    return None


def _one_minute():
    from datetime import timedelta

    return timedelta(minutes=1)


def render_digest_body(items: list[tuple[str, str]]) -> str:
    """One line per notification: its title, and its body when that adds something.

    Plain text with no markup, like every canonical notification body — it is read by the
    notification centre and the email template alike, so it cannot carry either one's HTML.
    """

    lines: list[str] = []
    for title, body in items:
        summary = (body or "").strip().splitlines()
        first = summary[0].strip() if summary else ""
        lines.append(f"• {title}" + (f" — {first}" if first and first != title else ""))
    return "\n".join(lines)


async def plan_due_digests(*, now: datetime | None = None, limit: int = 500) -> dict[str, int]:
    """Build every digest whose period has closed and which has something to say.

    Returns counts so an empty run is distinguishable from a broken one.
    """

    settings = get_settings()
    moment = now or datetime.now(UTC)
    considered = 0
    created = 0
    skipped_empty = 0
    already = 0
    #: Audit counters for the optional LLM copy step. `proposed` counts digests where the model
    #: produced valid, safe copy; `applied` counts those where the learner actually received it
    #: (only possible with shadow mode off). In the default configuration all three stay zero.
    llm_proposed = 0
    llm_applied = 0

    enqueued = 0

    candidates = await notification_repo.digest_subscriptions(limit=limit)
    for subscription in candidates:
        considered += 1
        user_id = subscription["user_id"]
        settings_category = subscription["settings_category"]
        period = subscription["digest_period"]
        policy = subscription["policy"]

        if settings_category not in DIGEST_CATEGORIES:
            continue
        if not settings.NOTIFICATION_EMAIL_ENABLED:
            # The digest exists to be emailed. Building one while the channel is off would
            # create an in-app item the learner never asked for and no email at all.
            continue
        if not capability_enabled_for("EMAIL", user_id, settings=settings):
            continue

        timezone_ = _timezone_of(policy)
        digest_day_of_week = policy.digest_day_of_week if policy is not None else 0

        # When the LLM copy writer is enabled for this learner, the whole digest is built on the
        # heavy queue instead of here. Its model call is bounded but far slower than the rest of this
        # loop, and running it inline would let one learner's digest hold up every other learner's and
        # tie up a `default`-queue worker for the duration — at volume, that is how an hourly planner
        # misses its window. The task resolves the copy *before* it creates the notification, exactly
        # as the inline path does, so there is never a moment where a half-written digest is emailable.
        # Everyone not in the LLM cohort is built inline below, unchanged. `claim_digest` runs inside
        # the task, so a duplicate enqueue across two hourly runs is deduped rather than doubled, and a
        # broker message that is lost simply means the next hour rebuilds it — nothing was claimed.
        if capability_enabled_for("DIGEST_LLM", user_id, settings=settings):
            if _enqueue_digest(
                user_id=user_id,
                settings_category=settings_category,
                period=period,
                timezone_name=timezone_.name,
                digest_day_of_week=digest_day_of_week,
            ):
                enqueued += 1
                continue
            # The broker is unreachable. Fall through and build it inline rather than drop it —
            # a slow planner run beats a missing digest.

        result = await _process_one_digest(
            user_id=user_id,
            settings_category=settings_category,
            period=period,
            timezone_=timezone_,
            digest_day_of_week=digest_day_of_week,
            now=moment,
            settings=settings,
        )
        outcome = result["outcome"]
        if outcome == "created":
            created += 1
        elif outcome == "skippedEmpty":
            skipped_empty += 1
        elif outcome == "already":
            already += 1
        llm_proposed += result["llmProposed"]
        llm_applied += result["llmApplied"]

    summary = {
        "considered": considered,
        "created": created,
        "skippedEmpty": skipped_empty,
        "alreadySummarised": already,
        "enqueued": enqueued,
        "llmProposed": llm_proposed,
        "llmApplied": llm_applied,
    }
    if created or already or enqueued:
        logger.info("Digest planning completed", extra=summary)
    return summary


async def _process_one_digest(
    *,
    user_id: str,
    settings_category: str,
    period: str,
    timezone_: LearnerTimezone,
    digest_day_of_week: int,
    now: datetime,
    settings,
) -> dict:
    """Build one learner's digest for one category, and return what happened.

    Self-contained and idempotent, so the same call is safe inline (the planner) or on the heavy
    queue (the LLM cohort). It recomputes the closed period, collects the items, and *claims* them —
    the claim is the dedupe, so two runs for one period cannot both create a digest — then writes the
    copy (deterministic, or the LLM's when that is enabled, live and valid) and creates the
    notification. The copy is resolved before the notification exists, so no half-written digest can
    be dispatched.

    Returns `{"outcome": "created"|"skippedEmpty"|"already"|"skipped", "llmProposed": int,
    "llmApplied": int}` so the caller can count without re-deriving anything.
    """

    from .service import create_notification

    result = {"outcome": "skipped", "llmProposed": 0, "llmApplied": 0}

    if settings_category not in DIGEST_CATEGORIES:
        return result
    digest_type, source_categories = DIGEST_CATEGORIES[settings_category]
    window = completed_period(period, now, timezone_, digest_day_of_week=digest_day_of_week)
    if window is None:
        return result

    items = await notification_repo.digestible_notifications(
        user_id,
        categories=source_categories,
        since=window.start,
        until=window.end,
        # Only types whose own taxonomy permits email. `learning.review_due` is digestible
        # but email-forbidden, and a digest must not smuggle it into an inbox.
        email_allowed_types=[
            name
            for name in _digestible_type_names(source_categories)
            if "EMAIL" in notification_spec(name).allowed_channels
        ],
    )
    if not items:
        result["outcome"] = "skippedEmpty"
        return result

    digest = await notification_repo.claim_digest(
        user_id=user_id,
        category=settings_category,
        period=window.period,
        period_start=window.start,
        period_end=window.end,
        notification_ids=[item["id"] for item in items],
    )
    if digest is None:
        # Another run already summarised this period, or every item was already spoken for.
        result["outcome"] = "already"
        return result

    # The deterministic copy is computed first and is always the fallback. The optional LLM step may
    # replace it, but only when its capability is enabled for this learner, shadow mode is off, and
    # its output passed validation and the content-safety pass — otherwise `resolve` hands these exact
    # values straight back, and it never raises.
    deterministic_title = _TITLES[settings_category][window.period]
    deterministic_body = render_digest_body([(item["title"], item["body"]) for item in items])
    copy = await resolve_digest_copy(
        user_id=user_id,
        settings_category=settings_category,
        period=window.period,
        items=[(item["title"], item["body"]) for item in items],
        deterministic_title=deterministic_title,
        deterministic_body=deterministic_body,
        settings=settings,
    )
    if copy.proposed:
        result["llmProposed"] = 1
    if copy.status == "APPLIED":
        result["llmApplied"] = 1

    notification = await create_notification(
        user_id=user_id,
        type=digest_type,
        title=copy.title,
        body=copy.body,
        action={"version": 1, "kind": "NONE"},
        idempotency_key=f"digest:{settings_category}:{window.period}:{window.start.date()}",
        priority=6,
        source_domain="notifications",
        source_entity_type="digest",
        source_entity_id=digest["id"],
    )
    await notification_repo.attach_digest_notification(digest["id"], notification.id)
    result["outcome"] = "created"
    return result


async def process_digest_for_learner(
    *,
    user_id: str,
    settings_category: str,
    period: str,
    timezone_name: str,
    digest_day_of_week: int,
) -> dict:
    """Heavy-queue entry point: build one learner's digest, resolving its LLM copy off the hot path.

    A thin wrapper over `_process_one_digest` that rebuilds the timezone from its name and reads the
    current settings, so the Celery task can call it with plain scalar arguments. Computes its own
    `now`: it may run minutes after the planner enqueued it, but a day or week has already closed, so
    `completed_period` returns the same period regardless.
    """

    return await _process_one_digest(
        user_id=user_id,
        settings_category=settings_category,
        period=period,
        timezone_=_timezone_named(timezone_name),
        digest_day_of_week=digest_day_of_week,
        now=datetime.now(UTC),
        settings=get_settings(),
    )


def _enqueue_digest(
    *,
    user_id: str,
    settings_category: str,
    period: str,
    timezone_name: str,
    digest_day_of_week: int,
) -> bool:
    """Enqueue the heavy-queue digest build. Returns whether the broker accepted it.

    Lazily imported so importing this module does not pull in the Celery app, and mirrors the
    broker-failure handling used elsewhere in the codebase: a publish that raises returns `False` so
    the caller can build the digest inline rather than lose it.
    """

    try:
        from src.workers.notification_tasks import process_digest_task

        process_digest_task.delay(
            user_id=user_id,
            settings_category=settings_category,
            period=period,
            timezone_name=timezone_name,
            digest_day_of_week=digest_day_of_week,
        )
        return True
    except Exception:
        logger.warning(
            "Could not enqueue heavy digest build; will build inline",
            extra={"settings_category": settings_category, "period": period},
            exc_info=True,
        )
        return False


def _digestible_type_names(categories: tuple[str, ...]) -> list[str]:
    from .taxonomy import NOTIFICATION_SPECS

    return [
        name
        for name, spec in NOTIFICATION_SPECS.items()
        if spec.digestible and spec.category in categories
    ]
