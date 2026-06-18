"""
Weekly Summary Email Service.

Generates and sends personalized "Your Week in Review" emails to re-engage users.
Shows study progress, streak status, upcoming deadlines, and review items.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.database import db

logger = logging.getLogger(__name__)


async def generate_weekly_summary_for_user(user_id: str) -> dict[str, Any] | None:
    """
    Generate weekly summary data for a single user.

    Returns None if the user has no meaningful data to summarize.
    """
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    # Get user info
    user = await db.user.find_unique(
        where={"id": user_id},
        include={"userStreak": True, "preferences": True},
    )
    if not user or not user.isActive:
        return None

    # Check notification preferences
    prefs = user.preferences
    if prefs and not getattr(prefs, "notifications", True):
        return None
    if prefs and not getattr(prefs, "emailWeeklyTips", True):
        return None

    user_name = (user.name or "").split()[0] if user.name else "there"

    # ─── Study Time This Week ───
    this_week_blocks = await db.scheduleblock.find_many(
        where={
            "userId": user_id,
            "startAt": {"gte": seven_days_ago, "lte": now},
        },
    )
    study_minutes_this_week = sum(
        max(0, (b.endAt - b.startAt).total_seconds() / 60) for b in this_week_blocks
    )

    # Previous week for comparison
    prev_week_blocks = await db.scheduleblock.find_many(
        where={
            "userId": user_id,
            "startAt": {"gte": fourteen_days_ago, "lt": seven_days_ago},
        },
    )
    study_minutes_prev_week = sum(
        max(0, (b.endAt - b.startAt).total_seconds() / 60) for b in prev_week_blocks
    )

    # ─── Chat Activity ───
    messages_this_week = await db.chatmessage.count(
        where={
            "userId": user_id,
            "role": "USER",
            "createdAt": {"gte": seven_days_ago},
        }
    )
    messages_prev_week = await db.chatmessage.count(
        where={
            "userId": user_id,
            "role": "USER",
            "createdAt": {"gte": fourteen_days_ago, "lt": seven_days_ago},
        }
    )

    # ─── Streak ───
    streak = user.userStreak
    current_streak = streak.currentStreak if streak else 0
    longest_streak = streak.longestStreak if streak else 0

    # ─── Topics Completed ───
    topics_completed = await db.topic.count(
        where={
            "module": {"course": {"userId": user_id}},
            "completed": True,
            "updatedAt": {"gte": seven_days_ago},
        }
    )

    # ─── Goals Progress ───
    active_goals = await db.goal.find_many(
        where={"userId": user_id, "status": "ACTIVE"},
        take=3,
        order={"targetDate": "asc"},
    )
    upcoming_goals = []
    for goal in active_goals:
        days_until = (goal.targetDate - now).days if goal.targetDate else None
        upcoming_goals.append(
            {
                "title": goal.title,
                "progress": goal.progress or 0,
                "daysUntil": days_until,
            }
        )

    # ─── Reviews Due ───
    reviews_due = await db.reviewitem.count(
        where={
            "userId": user_id,
            "nextReviewAt": {"lte": now},
        }
    )

    # ─── Upcoming Schedule ───
    upcoming_blocks = await db.scheduleblock.find_many(
        where={
            "userId": user_id,
            "startAt": {"gte": now, "lte": now + timedelta(days=7)},
        },
        order={"startAt": "asc"},
        take=5,
    )
    upcoming_schedule = [
        {
            "title": b.title,
            "startAt": b.startAt.isoformat(),
            "day": b.startAt.strftime("%A"),
            "time": b.startAt.strftime("%I:%M %p"),
        }
        for b in upcoming_blocks
    ]

    # Skip if user has zero engagement this week and last
    if (
        study_minutes_this_week == 0
        and messages_this_week == 0
        and topics_completed == 0
        and not upcoming_goals
    ):
        return None

    # ─── Build Summary ───
    study_change = 0
    if study_minutes_prev_week > 0:
        study_change = round(
            (study_minutes_this_week - study_minutes_prev_week) / study_minutes_prev_week * 100
        )

    return {
        "userName": user_name,
        "userEmail": user.email,
        "studyMinutes": round(study_minutes_this_week),
        "studyMinutesPrev": round(study_minutes_prev_week),
        "studyChange": study_change,
        "messages": messages_this_week,
        "messagesPrev": messages_prev_week,
        "currentStreak": current_streak,
        "longestStreak": longest_streak,
        "topicsCompleted": topics_completed,
        "reviewsDue": reviews_due,
        "upcomingGoals": upcoming_goals,
        "upcomingSchedule": upcoming_schedule,
    }


def render_weekly_summary_html(summary: dict[str, Any]) -> tuple[str, str]:
    """
    Render the weekly summary into email subject + HTML content.

    Returns: (subject, html_content)
    """
    user_name = summary["userName"]
    study_mins = summary["studyMinutes"]
    study_hours = round(study_mins / 60, 1)
    study_change = summary["studyChange"]
    current_streak = summary["currentStreak"]
    topics_completed = summary["topicsCompleted"]
    reviews_due = summary["reviewsDue"]
    messages = summary["messages"]
    upcoming_goals = summary["upcomingGoals"]
    upcoming_schedule = summary["upcomingSchedule"]

    # Subject line
    if current_streak > 0:
        subject = (
            f"🔥 {user_name}, your week: {current_streak}-day streak + {topics_completed} topics!"
        )
    elif topics_completed > 0:
        subject = f"📚 {user_name}, you completed {topics_completed} topics this week!"
    elif study_mins > 0:
        subject = f"📊 {user_name}, your weekly study summary is ready"
    else:
        subject = f"👋 {user_name}, here's what's waiting for you this week"

    # Study trend indicator
    if study_change > 10:
        trend_html = (
            f'<span style="color: #059669; font-weight: 600;">↑ {study_change}% vs last week</span>'
        )
    elif study_change < -10:
        trend_html = f'<span style="color: #DC2626; font-weight: 600;">↓ {abs(study_change)}% vs last week</span>'
    else:
        trend_html = '<span style="color: #6B7280;">≈ Similar to last week</span>'

    # Build goals section
    goals_html = ""
    if upcoming_goals:
        goal_items = ""
        for g in upcoming_goals:
            days_str = f"{g['daysUntil']}d left" if g["daysUntil"] is not None else ""
            progress_color = (
                "#059669"
                if g["progress"] >= 70
                else "#D97706" if g["progress"] >= 30 else "#DC2626"
            )
            goal_items += f"""
            <tr>
              <td style="padding: 8px 0; border-bottom: 1px solid #F3F4F6;">
                <span style="font-weight: 500;">{g['title']}</span>
                <span style="color: #6B7280; font-size: 13px; margin-left: 8px;">{days_str}</span>
              </td>
              <td style="padding: 8px 0; border-bottom: 1px solid #F3F4F6; text-align: right;">
                <span style="color: {progress_color}; font-weight: 600;">{g['progress']:.0f}%</span>
              </td>
            </tr>"""
        goals_html = f"""
        <div style="margin: 24px 0;">
          <h3 style="font-size: 16px; font-weight: 600; margin-bottom: 12px;">🎯 Active Goals</h3>
          <table style="width: 100%; border-collapse: collapse;">{goal_items}</table>
        </div>"""

    # Build schedule section
    schedule_html = ""
    if upcoming_schedule:
        sched_items = "".join(
            f'<li style="padding: 4px 0; color: #374151;">'
            f'<strong>{s["day"]}</strong> at {s["time"]} — {s["title"]}</li>'
            for s in upcoming_schedule[:5]
        )
        schedule_html = f"""
        <div style="margin: 24px 0;">
          <h3 style="font-size: 16px; font-weight: 600; margin-bottom: 12px;">📅 Coming Up This Week</h3>
          <ul style="list-style: none; padding: 0; margin: 0;">{sched_items}</ul>
        </div>"""

    # Reviews callout
    reviews_html = ""
    if reviews_due > 0:
        reviews_html = f"""
        <div style="margin: 24px 0; padding: 16px; background: #FEF3C7; border-radius: 8px; border: 1px solid #FDE68A;">
          <p style="margin: 0; font-size: 15px; color: #92400E;">
            <strong>📝 {reviews_due} review{'s' if reviews_due != 1 else ''} waiting</strong> —
            Quick reviews will strengthen your long-term memory!
          </p>
        </div>"""

    # Main HTML
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; color: #1F2937;">
      <h2 style="font-size: 22px; font-weight: 700; margin-bottom: 4px;">
        Hey {user_name}, here's your week! 👋
      </h2>
      <p style="color: #6B7280; margin-top: 0; margin-bottom: 24px;">Your study summary for the past 7 days</p>

      <!-- Stats Grid -->
      <div style="display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap;">
        <div style="flex: 1; min-width: 120px; padding: 16px; background: #EFF6FF; border-radius: 8px; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #1D4ED8;">{study_hours}h</div>
          <div style="font-size: 13px; color: #6B7280; margin-top: 4px;">Study Time</div>
          <div style="font-size: 12px; margin-top: 4px;">{trend_html}</div>
        </div>
        <div style="flex: 1; min-width: 120px; padding: 16px; background: #F0FDF4; border-radius: 8px; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #059669;">{topics_completed}</div>
          <div style="font-size: 13px; color: #6B7280; margin-top: 4px;">Topics Completed</div>
        </div>
        <div style="flex: 1; min-width: 120px; padding: 16px; background: #FFF7ED; border-radius: 8px; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #EA580C;">🔥 {current_streak}</div>
          <div style="font-size: 13px; color: #6B7280; margin-top: 4px;">Day Streak</div>
        </div>
        <div style="flex: 1; min-width: 120px; padding: 16px; background: #F5F3FF; border-radius: 8px; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #7C3AED;">{messages}</div>
          <div style="font-size: 13px; color: #6B7280; margin-top: 4px;">AI Messages</div>
        </div>
      </div>

      {reviews_html}
      {goals_html}
      {schedule_html}

      <!-- CTA -->
      <div style="text-align: center; margin: 32px 0;">
        <a href="https://app.maigie.com/dashboard" style="display: inline-block; padding: 14px 32px; background: #4F46E5; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px;">
          Continue Studying →
        </a>
      </div>

      <p style="font-size: 13px; color: #9CA3AF; text-align: center; margin-top: 32px;">
        You're receiving this because you have an account on Maigie. Manage your email preferences in Settings.
      </p>
    </div>"""

    return subject, html_content


async def send_weekly_summary_emails() -> dict[str, Any]:
    """
    Generate and send weekly summary emails to all eligible users.

    Returns stats about how many emails were sent.
    """
    from src.services.email import send_bulk_email

    # Get all active, onboarded users
    users = await db.user.find_many(
        where={
            "isActive": True,
            "isOnboarded": True,
            "role": "USER",
        },
        include={"preferences": True},
        take=2000,
    )

    sent = 0
    skipped = 0
    errors = []

    for user in users:
        try:
            # Check email preferences
            prefs = user.preferences
            if prefs and not getattr(prefs, "notifications", True):
                skipped += 1
                continue
            if prefs and not getattr(prefs, "emailWeeklyTips", True):
                skipped += 1
                continue

            # Generate summary
            summary = await generate_weekly_summary_for_user(user.id)
            if not summary:
                skipped += 1
                continue

            # Render email
            subject, html_content = render_weekly_summary_html(summary)

            # Send
            await send_bulk_email(
                email=user.email,
                name=user.name,
                subject=subject,
                content=html_content,
            )
            sent += 1

        except Exception as e:
            logger.warning("Weekly summary email failed for user %s: %s", user.id, e)
            errors.append({"userId": user.id, "error": str(e)})

    logger.info(
        "Weekly summary emails: sent=%d, skipped=%d, errors=%d",
        sent,
        skipped,
        len(errors),
    )
    return {"sent": sent, "skipped": skipped, "errors": len(errors)}
