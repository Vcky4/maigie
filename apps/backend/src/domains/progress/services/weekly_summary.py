"""Weekly learning summary emails.

Restores the pre-migration ``weekly_summary_email_service``, which was Prisma-based and
never migrated. Until now ``send_weekly_summaries`` raised, so the
``notifications.weekly_summary`` beat task failed loudly every week rather than pretending
to have sent anything.

The summary compares this week against the previous one, because a bare number is not
information a learner can act on: "4 hours" means nothing without knowing whether last
week was 2 or 8.

A user with nothing to report is skipped rather than emailed an empty summary. That is the
point of ``_is_worth_sending``: an engagement email that arrives to say nothing happened
teaches the recipient to ignore the sender.

The body is composed here rather than in a Jinja template, matching the original. There is
no ``weekly_summary`` template in ``src/templates/email``, and the content is a small
fixed set of figures.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from src.domains.identity.db_models import User, UserPreferences
from src.domains.intelligence.db_models import ChatMessage
from src.domains.progress.db_models import ScheduleBlock, UserStreak
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)

# Cap the fan-out so one run cannot sit in the queue for hours.
MAX_USERS_PER_RUN = 500


def _block_minutes(rows: list[Any]) -> int:
    total = 0.0
    for start_at, end_at in rows:
        if start_at is None or end_at is None:
            continue
        total += max(0.0, (end_at - start_at).total_seconds() / 60)
    return int(round(total))


def _describe_change(this_week: int, previous_week: int) -> str:
    """Plain-language comparison, avoiding a percentage against a zero baseline."""
    if previous_week == 0:
        return "your first tracked week" if this_week else "no time tracked yet"
    delta = this_week - previous_week
    if delta == 0:
        return "the same as last week"
    percent = abs(delta) / previous_week * 100
    direction = "more" if delta > 0 else "less"
    return f"{percent:.0f}% {direction} than last week"


async def generate_weekly_summary_for_user(user_id: str) -> dict[str, Any] | None:
    """Gather one user's week, or ``None`` if they should not be emailed."""
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(User, UserPreferences)
                .outerjoin(UserPreferences, UserPreferences.user_id == User.id)
                .where(User.id == user_id)
            )
        ).first()
        if row is None:
            return None

        user, prefs = row
        if not user.is_active or not user.email:
            return None
        # A missing preferences row means defaults, which are opt-in.
        if prefs is not None:
            if not getattr(prefs, "notifications", True):
                return None
            if not getattr(prefs, "email_weekly_tips", True):
                return None

        this_week = (
            await session.execute(
                select(ScheduleBlock.start_at, ScheduleBlock.end_at).where(
                    ScheduleBlock.user_id == user_id,
                    ScheduleBlock.start_at >= seven_days_ago,
                    ScheduleBlock.start_at <= now,
                )
            )
        ).all()
        previous_week = (
            await session.execute(
                select(ScheduleBlock.start_at, ScheduleBlock.end_at).where(
                    ScheduleBlock.user_id == user_id,
                    ScheduleBlock.start_at >= fourteen_days_ago,
                    ScheduleBlock.start_at < seven_days_ago,
                )
            )
        ).all()

        messages = (
            await session.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.user_id == user_id,
                    ChatMessage.role == "USER",
                    ChatMessage.created_at >= seven_days_ago,
                )
            )
        ).scalar() or 0

        streak = (
            await session.execute(select(UserStreak).where(UserStreak.user_id == user_id))
        ).scalar_one_or_none()

    minutes_this_week = _block_minutes(this_week)
    minutes_previous_week = _block_minutes(previous_week)

    return {
        "user_id": user_id,
        "email": user.email,
        "name": (user.name or "").split()[0] if user.name else "there",
        "minutes_this_week": minutes_this_week,
        "minutes_previous_week": minutes_previous_week,
        "change": _describe_change(minutes_this_week, minutes_previous_week),
        "sessions_this_week": len(this_week),
        "messages_this_week": int(messages),
        "current_streak": streak.current_streak if streak else 0,
        "longest_streak": streak.longest_streak if streak else 0,
    }


def _is_worth_sending(summary: dict[str, Any]) -> bool:
    """Only email a learner who actually did something this week."""
    return bool(
        summary["minutes_this_week"]
        or summary["sessions_this_week"]
        or summary["messages_this_week"]
    )


def render_weekly_summary(summary: dict[str, Any]) -> tuple[str, str]:
    """Return ``(html, text)`` for one summary."""
    hours = summary["minutes_this_week"] / 60
    lines = [
        f"Study time: {hours:.1f} hours ({summary['change']})",
        f"Study sessions: {summary['sessions_this_week']}",
        f"Questions asked: {summary['messages_this_week']}",
    ]
    if summary["current_streak"]:
        lines.append(
            f"Current streak: {summary['current_streak']} day"
            f"{'s' if summary['current_streak'] != 1 else ''}"
        )

    html_items = "".join(f"<li style='margin-bottom:8px;'>{line}</li>" for line in lines)
    html = (
        f"<p>Hi {summary['name']},</p>"
        "<p>Here's how your week went.</p>"
        f"<ul style='padding-left:20px;'>{html_items}</ul>"
    )
    text = f"Hi {summary['name']},\n\nHere's how your week went.\n\n" + "\n".join(
        f"- {line}" for line in lines
    )
    return html, text


async def send_weekly_summaries() -> dict[str, int]:
    """Email a weekly summary to every eligible, active user.

    Returns counts so an empty run is distinguishable from a broken one.
    """
    from src.shared.infrastructure.email import send_bulk_email

    factory = get_session_factory()
    async with factory() as session:
        user_ids = [
            row[0]
            for row in (
                await session.execute(
                    select(User.id)
                    .where(User.is_active.is_(True), User.email.is_not(None))
                    .limit(MAX_USERS_PER_RUN)
                )
            ).all()
        ]

    sent = 0
    skipped = 0
    failed = 0

    for user_id in user_ids:
        try:
            summary = await generate_weekly_summary_for_user(user_id)
            if summary is None or not _is_worth_sending(summary):
                skipped += 1
                continue

            html, _text = render_weekly_summary(summary)
            await send_bulk_email(
                email=summary["email"],
                name=summary["name"],
                subject="Your week in review",
                content=html,
            )
            sent += 1
        except Exception:
            # One user's bad data must not stop the run.
            failed += 1
            logger.exception("Failed to send weekly summary to user %s", user_id)

    summary_counts = {
        "considered": len(user_ids),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }
    logger.info("Weekly summaries: %s", summary_counts)
    return summary_counts
