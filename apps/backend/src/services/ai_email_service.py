"""
AI-powered email drafting service.

Generates personalized email content (subject, body) for notification emails
using Gemini. Content is tailored to each user's context (courses, goals,
schedules, streak, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


async def _call_gemini_for_email(
    prompt: str, response_format: str, max_tokens: int = 800
) -> dict[str, Any] | None:
    """Call Gemini to generate email content. Returns parsed JSON or None."""
    try:
        from google import genai
        from google.genai import types
        import os

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set; skipping AI email draft")
            return None

        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.7,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return None

        # Extract JSON (handle markdown code blocks)
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as e:
        logger.exception("AI email draft failed: %s", e)
        return None


async def draft_morning_schedule_email(
    db_client: Any,
    user: Any,
    schedules_today: list[dict[str, Any]],
    date_label: str,
) -> tuple[str, dict[str, Any]]:
    """
    Draft morning schedule email content using AI.

    Returns:
        (subject, template_data) where template_data includes:
        intro, intro_plain, date_label, schedules (list of {title, time})
    """
    name = (getattr(user, "name", "") or "").split()[0] or "there"
    schedules_str = (
        "\n".join([f"- {s['title']} at {s['time']}" for s in schedules_today])
        if schedules_today
        else "No scheduled items today."
    )

    ctx_parts = [f"User's first name: {name}", f"Date: {date_label}"]
    try:
        streak = await db_client.userstreak.find_unique(where={"userId": user.id})
        if streak and getattr(streak, "currentStreak", 0) > 0:
            ctx_parts.append(f"Study streak: {streak.currentStreak} days")
    except Exception:
        pass

    ctx_str = "\n".join(ctx_parts)
    prompt = f"""You are Maigie, a friendly study companion. Write a brief, personalized intro (2-3 sentences) for a daily morning email.

Context:
{ctx_str}

Today's schedule:
{schedules_str}

Return a JSON object with:
- "intro": HTML paragraph(s) for the intro (use <p> tags, keep it warm and motivating)
- "intro_plain": Plain text version of the intro (no HTML)
- "date_label": Short label like "Here's your day for Wednesday, Feb 12" (can be same as provided or slightly varied)

If the user has no schedule, gently encourage them to add study time. If they have a busy day, acknowledge it positively.
Output only valid JSON, no markdown."""

    result = await _call_gemini_for_email(prompt, "morning", max_tokens=400)
    date_label_fallback = date_label or "Here's your day"

    if not result:
        if schedules_today:
            intro = f"<p>Good morning! You have {len(schedules_today)} item(s) on your schedule today. Let's make it a productive day!</p>"
            intro_plain = f"Good morning! You have {len(schedules_today)} item(s) on your schedule today. Let's make it a productive day!"
        else:
            intro = "<p>Good morning! It's a fresh day—great time to add some study blocks to your schedule.</p>"
            intro_plain = "Good morning! It's a fresh day—great time to add some study blocks to your schedule."
        return (
            "Your schedule for today",
            {
                "intro": intro,
                "intro_plain": intro_plain,
                "date_label": date_label_fallback,
                "schedules": schedules_today,
            },
        )

    return (
        result.get("subject") or "Your schedule for today",
        {
            "intro": result.get("intro") or "<p>Good morning! Here's your schedule for today.</p>",
            "intro_plain": result.get("intro_plain")
            or "Good morning! Here's your schedule for today.",
            "date_label": result.get("date_label") or date_label,
            "schedules": schedules_today,
        },
    )


async def draft_schedule_reminder_email(
    schedule_title: str,
    schedule_time: str,
    schedule_description: str | None,
    user_name: str,
) -> tuple[str, dict[str, Any]]:
    """
    Draft schedule reminder email content using AI.

    Returns:
        (subject, template_data) with reminder_message, reminder_message_plain
    """
    name = (user_name or "").split()[0] or "there"
    prompt = f"""You are Maigie, a friendly study companion. Write a very brief reminder message (1-2 sentences) for an upcoming schedule item.

Schedule: {schedule_title}
Time: {schedule_time}
User: {name}

Return a JSON object with:
- "reminder_message": HTML (e.g. <p>Your session starts in about 15 minutes. Time to get ready!</p>)
- "reminder_message_plain": Plain text version
- "subject": Email subject line (optional, default: "Reminder: {schedule_title} starts soon")

Keep it short, warm, and motivating. Output only valid JSON."""

    result = await _call_gemini_for_email(prompt, "reminder", max_tokens=200)
    if not result:
        reminder = (
            "<p>Your scheduled session is starting in about 15 minutes. Time to get ready!</p>"
        )
        reminder_plain = (
            "Your scheduled session is starting in about 15 minutes. Time to get ready!"
        )
    else:
        reminder = (
            result.get("reminder_message")
            or "<p>Your session is starting soon. Time to get ready!</p>"
        )
        reminder_plain = (
            result.get("reminder_message_plain")
            or "Your session is starting soon. Time to get ready!"
        )

    subject = (
        result.get("subject") if result else f"Reminder: {schedule_title} starts in 15 minutes"
    )
    return (
        subject,
        {
            "reminder_message": reminder,
            "reminder_message_plain": reminder_plain,
            "schedule_title": schedule_title,
            "schedule_time": schedule_time,
            "schedule_description": schedule_description or "",
        },
    )


async def draft_weekly_tips_email(db_client: Any, user: Any) -> tuple[str, dict[str, Any]]:
    """
    Draft weekly encouragement/tips email using AI.

    Returns:
        (subject, template_data) with intro, intro_plain, tips (list), encouragement
    """
    name = (getattr(user, "name", "") or "").split()[0] or "there"
    ctx_parts = [f"User: {name}"]

    try:
        courses = await db_client.course.find_many(
            where={"userId": user.id, "archived": False},
            order={"updatedAt": "desc"},
            take=5,
            include={"modules": {"include": {"topics": True}}},
        )
        if courses:
            lines = []
            for c in courses:
                total = sum(len(m.topics) for m in c.modules)
                completed = sum(1 for m in c.modules for t in m.topics if t.completed)
                pct = round((completed / total * 100) if total > 0 else 0)
                lines.append(f"- {c.title}: {pct}% complete")
            ctx_parts.append("Courses: " + "; ".join(lines))
    except Exception:
        ctx_parts.append("Courses: (unknown)")

    try:
        goals = await db_client.goal.find_many(
            where={"userId": user.id, "status": "ACTIVE"},
            take=5,
        )
        if goals:
            ctx_parts.append("Goals: " + "; ".join([g.title for g in goals]))
    except Exception:
        pass

    try:
        streak = await db_client.userstreak.find_unique(where={"userId": user.id})
        if streak and getattr(streak, "currentStreak", 0) > 0:
            ctx_parts.append(f"Study streak: {streak.currentStreak} days")
    except Exception:
        pass

    ctx_str = "\n".join(ctx_parts)
    prompt = f"""You are Maigie, a supportive study companion. Write a weekly encouragement email with personalized study tips.

Context:
{ctx_str}

Return a JSON object with:
- "intro": HTML paragraph (2-3 sentences) - warm weekly greeting, reference their progress
- "intro_plain": Plain text version
- "tips": Array of 3-5 short study tips (strings), personalized to their courses/goals if possible
- "encouragement": One uplifting sentence (string) to close
- "subject": Email subject (optional, e.g. "Your weekly study tips from Maigie")

Tips should be practical (e.g. Pomodoro technique, spaced repetition, rest breaks). Keep each tip to one sentence.
Output only valid JSON."""

    result = await _call_gemini_for_email(prompt, "weekly", max_tokens=600)
    if not result:
        intro = "<p>Hope you had a great week! Here are some tips to keep your momentum going.</p>"
        intro_plain = "Hope you had a great week! Here are some tips to keep your momentum going."
        tips = [
            "Try the Pomodoro technique: 25 minutes of focus, then a 5-minute break.",
            "Review notes within 24 hours of a study session to strengthen memory.",
            "Get enough sleep—it's when your brain consolidates what you learned.",
        ]
        encouragement = "You're making progress every day. Keep going!"
    else:
        intro = result.get("intro") or "<p>Hope you had a great week!</p>"
        intro_plain = result.get("intro_plain") or "Hope you had a great week!"
        tips = result.get("tips") or [
            "Try the Pomodoro technique for focused study sessions.",
            "Review material within 24 hours to strengthen memory.",
        ]
        encouragement = result.get("encouragement") or "You've got this!"

    subject = result.get("subject") if result else "Your weekly study tips from Maigie"
    return (
        subject,
        {
            "intro": intro,
            "intro_plain": intro_plain,
            "tips": tips if isinstance(tips, list) else [str(t) for t in [tips]],
            "encouragement": encouragement,
        },
    )


# ==========================================
#  Proactive Agent Nudge Emails
# ==========================================


async def draft_agent_nudge_email(
    nudge_type: str,
    nudge_title: str,
    nudge_message: str,
    user_name: str,
    action_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Draft an AI-personalized nudge email for proactive outreach.

    Args:
        nudge_type: Type of nudge (goal_nudge, study_gap, review_reminder)
        nudge_title: Short title of the nudge
        nudge_message: Detailed nudge message
        user_name: User's first name
        action_data: Optional contextual data (goalId, streak, etc.)

    Returns:
        (subject, html_content) tuple ready for send_bulk_email
    """
    name = user_name or "there"

    prompt = f"""You are Maigie, a warm and encouraging AI study companion.
Draft a short, personalized email for a proactive nudge notification.

Nudge type: {nudge_type}
Title: {nudge_title}
Message: {nudge_message}
User's first name: {name}
Additional context: {json.dumps(action_data or {})}

Write a JSON object with:
- "subject": email subject line (short, personal, engaging — use emoji sparingly)
- "body_html": email body in HTML (2-4 short paragraphs max, warm and encouraging tone, include a clear call-to-action to open Maigie)

Keep it brief and actionable. Sound human, not robotic.
"""

    result = await _call_gemini_for_email(prompt, "json", max_tokens=600)

    if result:
        subject = result.get("subject", nudge_title)
        body_html = result.get("body_html", "")
    else:
        # Fallback: use the nudge message directly
        subject = nudge_title
        body_html = (
            f"<p>Hi {name},</p>"
            f"<p>{nudge_message}</p>"
            f"<p>Open <a href='https://app.maigie.com'>Maigie</a> to take action!</p>"
            f"<p>— Maigie, your study companion 📚</p>"
        )

    return subject, body_html


async def send_agent_nudge_email(
    user_email: str,
    user_name: str | None,
    nudge_type: str,
    nudge_title: str,
    nudge_message: str,
    action_data: dict[str, Any] | None = None,
) -> bool:
    """
    Draft and send a proactive nudge email to a user.

    Returns True if sent successfully, False otherwise.
    """
    from src.services import email

    try:
        subject, html_content = await draft_agent_nudge_email(
            nudge_type=nudge_type,
            nudge_title=nudge_title,
            nudge_message=nudge_message,
            user_name=user_name or "there",
            action_data=action_data,
        )

        await email.send_bulk_email(
            email=user_email,
            name=user_name,
            subject=subject,
            content=html_content,
        )
        logger.info("Sent agent nudge email (%s) to %s", nudge_type, user_email)
        return True
    except Exception as e:
        logger.error("Failed to send agent nudge email to %s: %s", user_email, e)
        return False
