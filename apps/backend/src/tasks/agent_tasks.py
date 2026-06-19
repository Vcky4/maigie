"""
Proactive Agent Background Tasks (Celery).

Enables autonomous AI behavior:
- Goal deadline nudges
- Study gap detection
- Review due reminders
- Conversation summarization
- Learning insight generation
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.tasks.base import run_async_in_celery, task
from src.tasks.registry import register_task
from src.tasks.schedules import register_periodic_task

logger = logging.getLogger(__name__)

TASK_GOAL_MONITOR = "agent.goal_deadline_monitor"
TASK_STUDY_GAP = "agent.study_gap_detector"
TASK_REVIEW_NUDGE = "agent.review_due_nudge"
TASK_CONVO_SUMMARIZER = "agent.conversation_summarizer"
TASK_INSIGHT_GENERATOR = "agent.learning_insight_generator"
TASK_REENGAGEMENT = "agent.reengagement_nudge"


async def _ensure_db_connected():
    from src.core.database import db

    if not db.is_connected():
        await db.connect()


async def _send_nudge_email_if_enabled(
    user,
    nudge_type: str,
    title: str,
    message: str,
    action_data: dict | None = None,
):
    """
    Send a proactive nudge email if the user has notifications enabled.
    The user object must include .preferences and .email.
    """
    try:
        prefs = getattr(user, "preferences", None)
        # Respect user notification preferences
        if prefs and not getattr(prefs, "notifications", True):
            return

        if not user.email:
            return

        from src.services.ai_email_service import send_agent_nudge_email

        await send_agent_nudge_email(
            user_email=user.email,
            user_name=user.name,
            nudge_type=nudge_type,
            nudge_title=title,
            nudge_message=message,
            action_data=action_data,
        )
    except Exception as e:
        logger.warning("Nudge email failed for user %s: %s", user.id, e)


# ---------------------------------------------------------------------------
#  Goal Deadline Monitor
# ---------------------------------------------------------------------------


async def _goal_deadline_monitor_impl():
    """
    Check for goals approaching deadline with low progress.
    Creates AIAgentTask nudges for users who need encouragement.
    """
    await _ensure_db_connected()
    from src.core.database import db

    now = datetime.now(UTC)
    fourteen_days = now + timedelta(days=14)

    try:
        at_risk_goals = await db.goal.find_many(
            where={
                "status": "ACTIVE",
                "targetDate": {"gte": now, "lte": fourteen_days},
                "progress": {"lt": 70},
            },
            include={"user": {"include": {"preferences": True}}, "course": True},
            take=100,
        )

        nudges_created = 0
        for goal in at_risk_goals:
            days_left = (goal.targetDate - now).days
            progress = goal.progress or 0

            # Skip if we already sent a nudge for this goal recently
            existing = await db.aiagenttask.find_first(
                where={
                    "userId": goal.userId,
                    "taskType": "goal_nudge",
                    "status": {"in": ["pending", "sent"]},
                    "createdAt": {"gte": now - timedelta(days=2)},
                },
            )
            if existing:
                continue

            if days_left <= 3 and progress < 50:
                priority = 9
                title = f"⚠️ '{goal.title}' is due in {days_left} days!"
                message = (
                    f"Your goal '{goal.title}' is due in just {days_left} days and "
                    f"you're at {progress:.0f}% progress. Would you like me to help you "
                    f"create a focused study plan for the remaining time?"
                )
            elif days_left <= 7 and progress < 50:
                priority = 7
                title = f"📋 Check in: '{goal.title}'"
                message = (
                    f"Your goal '{goal.title}' is coming up in {days_left} days "
                    f"({progress:.0f}% complete). Let's make a plan to finish strong!"
                )
            elif days_left <= 14 and progress < 30:
                priority = 5
                title = f"🎯 Goal reminder: '{goal.title}'"
                message = (
                    f"'{goal.title}' is due in {days_left} days and you're at "
                    f"{progress:.0f}%. Starting now will give you plenty of time!"
                )
            else:
                continue

            action = {
                "goalId": goal.id,
                "courseId": goal.courseId,
                "suggestedAction": "create_study_plan",
            }
            await db.aiagenttask.create(
                data={
                    "userId": goal.userId,
                    "taskType": "goal_nudge",
                    "status": "pending",
                    "priority": priority,
                    "title": title,
                    "message": message,
                    "scheduledAt": now,
                    "actionData": action,
                }
            )
            # Also send email for high-priority nudges
            if priority >= 7 and goal.user:
                await _send_nudge_email_if_enabled(goal.user, "goal_nudge", title, message, action)
            nudges_created += 1

        logger.info("Goal monitor: created %d nudges", nudges_created)

    except Exception as e:
        logger.error("Goal deadline monitor failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
#  Study Gap Detector
# ---------------------------------------------------------------------------


async def _study_gap_detector_impl():
    """
    Detect users who haven't studied in 2+ days and send encouragement.
    """
    await _ensure_db_connected()
    from src.core.database import db

    now = datetime.now(UTC)
    two_days_ago = now - timedelta(days=2)

    try:
        streaks = await db.userstreak.find_many(
            where={
                "currentStreak": {"gt": 0},
                "lastStudyDate": {"lt": two_days_ago},
            },
            include={"user": True},
            take=100,
        )

        nudges_created = 0
        for streak in streaks:
            existing = await db.aiagenttask.find_first(
                where={
                    "userId": streak.userId,
                    "taskType": "study_gap",
                    "status": {"in": ["pending", "sent"]},
                    "createdAt": {"gte": now - timedelta(days=2)},
                },
            )
            if existing:
                continue

            days_since = (now - streak.lastStudyDate).days if streak.lastStudyDate else 3
            user_name = (
                (streak.user.name or "").split()[0] if streak.user and streak.user.name else "there"
            )

            title = f"🔥 Don't lose your {streak.currentStreak}-day streak!"
            message = (
                f"Hey {user_name}! It's been {days_since} days since your last study session. "
                f"You have a {streak.currentStreak}-day streak going — even 15 minutes today "
                f"will keep it alive! 💪"
            )
            action = {
                "currentStreak": streak.currentStreak,
                "daysSinceLastStudy": days_since,
            }
            await db.aiagenttask.create(
                data={
                    "userId": streak.userId,
                    "taskType": "study_gap",
                    "status": "pending",
                    "priority": 4,
                    "title": title,
                    "message": message,
                    "scheduledAt": now,
                    "actionData": action,
                }
            )
            # Send email for streaks about to break
            if days_since >= 3 and streak.user:
                await _send_nudge_email_if_enabled(streak.user, "study_gap", title, message, action)

            # Send push notification for streak-at-risk
            try:
                from src.services.push_notification_service import send_push_notification

                await send_push_notification(
                    user_id=streak.userId,
                    title=title,
                    body=message,
                    data={"type": "streak_at_risk", "streak": str(streak.currentStreak)},
                )
            except Exception as push_err:
                logger.debug("Streak push notification failed for %s: %s", streak.userId, push_err)

            nudges_created += 1

        logger.info("Study gap detector: created %d nudges", nudges_created)

    except Exception as e:
        logger.error("Study gap detector failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
#  Review Due Reminder
# ---------------------------------------------------------------------------


async def _review_due_nudge_impl():
    """
    Check for overdue spaced repetition reviews and create reminders.
    """
    await _ensure_db_connected()
    from src.core.database import db

    now = datetime.now(UTC)

    try:
        overdue_reviews = await db.reviewitem.find_many(
            where={"nextReviewAt": {"lte": now}},
            include={"user": True, "topic": True},
            take=200,
        )

        user_reviews: dict[str, list] = {}
        for review in overdue_reviews:
            user_reviews.setdefault(review.userId, []).append(review)

        nudges_created = 0
        for user_id, reviews in user_reviews.items():
            existing = await db.aiagenttask.find_first(
                where={
                    "userId": user_id,
                    "taskType": "review_reminder",
                    "status": {"in": ["pending", "sent"]},
                    "createdAt": {"gte": now - timedelta(hours=12)},
                },
            )
            if existing:
                continue

            count = len(reviews)
            topic_names = [r.topic.title for r in reviews[:3] if r.topic]
            topics_str = ", ".join(topic_names)
            if count > 3:
                topics_str += f" and {count - 3} more"

            title = f"📝 {count} review{'s' if count != 1 else ''} waiting for you"
            message = (
                f"You have {count} topic{'s' if count != 1 else ''} ready for review: "
                f"{topics_str}. Quick reviews now will strengthen your long-term memory!"
            )
            action = {
                "reviewCount": count,
                "reviewIds": [r.id for r in reviews[:5]],
            }
            await db.aiagenttask.create(
                data={
                    "userId": user_id,
                    "taskType": "review_reminder",
                    "status": "pending",
                    "priority": 6,
                    "title": title,
                    "message": message,
                    "scheduledAt": now,
                    "actionData": action,
                }
            )
            # Send email if 3+ reviews overdue
            if count >= 3 and reviews[0].user:
                await _send_nudge_email_if_enabled(
                    reviews[0].user, "review_reminder", title, message, action
                )
            nudges_created += 1

        logger.info("Review nudge: created %d nudges", nudges_created)

    except Exception as e:
        logger.error("Review due nudge failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
#  Conversation Summarizer
# ---------------------------------------------------------------------------


async def _conversation_summarizer_impl():
    """
    Summarize recent conversations that haven't been summarized yet.
    """
    await _ensure_db_connected()
    from src.core.database import db
    from src.services.memory_service import summarize_conversation

    now = datetime.now(UTC)
    two_hours_ago = now - timedelta(hours=2)

    try:
        sessions = await db.chatsession.find_many(
            where={
                "updatedAt": {"gte": now - timedelta(hours=24), "lte": two_hours_ago},
                "isActive": True,
                "conversationSummaries": {"none": {}},
            },
            take=50,
        )

        summarized = 0
        for session in sessions:
            result = await summarize_conversation(session.id, session.userId)
            if result:
                summarized += 1

        logger.info("Conversation summarizer: summarized %d sessions", summarized)

    except Exception as e:
        logger.error("Conversation summarizer failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
#  Learning Insight Generator
# ---------------------------------------------------------------------------


async def _learning_insight_generator_impl():
    """
    Generate/update learning insights for active users.
    """
    await _ensure_db_connected()
    from src.core.database import db
    from src.services.memory_service import generate_learning_insights

    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)

    try:
        active_users = await db.user.find_many(
            where={
                "chatMessages": {"some": {"createdAt": {"gte": thirty_days_ago}}},
            },
            take=100,
        )

        generated_count = 0
        for user in active_users:
            insights = await generate_learning_insights(user.id)
            generated_count += len(insights)

        logger.info(
            "Insight generator: generated %d insights for %d users",
            generated_count,
            len(active_users),
        )

    except Exception as e:
        logger.error("Learning insight generator failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
#  Celery Task Registrations (decorator pattern)
# ---------------------------------------------------------------------------


@register_task(
    name=TASK_GOAL_MONITOR,
    description="Monitor goal deadlines and create nudges for at-risk goals",
    category="agent",
    tags=["agent", "goals", "nudge"],
)
@task(name=TASK_GOAL_MONITOR, bind=True, max_retries=2)
def goal_deadline_monitor_task(self: Any):
    """Run goal deadline monitor."""
    return run_async_in_celery(_goal_deadline_monitor_impl())


@register_task(
    name=TASK_STUDY_GAP,
    description="Detect study gaps and send streak encouragement",
    category="agent",
    tags=["agent", "streak", "nudge"],
)
@task(name=TASK_STUDY_GAP, bind=True, max_retries=2)
def study_gap_detector_task(self: Any):
    """Run study gap detector."""
    return run_async_in_celery(_study_gap_detector_impl())


@register_task(
    name=TASK_REVIEW_NUDGE,
    description="Remind users about overdue spaced repetition reviews",
    category="agent",
    tags=["agent", "review", "nudge"],
)
@task(name=TASK_REVIEW_NUDGE, bind=True, max_retries=2)
def review_due_nudge_task(self: Any):
    """Run review due nudge."""
    return run_async_in_celery(_review_due_nudge_impl())


@register_task(
    name=TASK_CONVO_SUMMARIZER,
    description="Summarize recent conversations for long-term memory",
    category="agent",
    tags=["agent", "memory", "summarization"],
)
@task(name=TASK_CONVO_SUMMARIZER, bind=True, max_retries=2)
def conversation_summarizer_task(self: Any):
    """Run conversation summarizer."""
    return run_async_in_celery(_conversation_summarizer_impl())


@register_task(
    name=TASK_INSIGHT_GENERATOR,
    description="Analyze user behavior and generate learning insights",
    category="agent",
    tags=["agent", "insights", "analytics"],
)
@task(name=TASK_INSIGHT_GENERATOR, bind=True, max_retries=1)
def learning_insight_generator_task(self: Any):
    """Run learning insight generator."""
    return run_async_in_celery(_learning_insight_generator_impl())


# ---------------------------------------------------------------------------
#  Re-engagement Nudge (Win-back for inactive users)
# ---------------------------------------------------------------------------


async def _reengagement_nudge_impl():
    """
    Send re-engagement push notifications to users inactive for 3+ days.
    Highlights what they've missed (circle activity, upcoming exams, review items due).
    """
    await _ensure_db_connected()
    from src.core.database import db
    from src.services.push_notification_service import send_push_notification

    now = datetime.now(UTC)
    three_days_ago = now - timedelta(days=3)
    seven_days_ago = now - timedelta(days=7)

    try:
        # Find users who haven't had a study session in 3-7 days
        # (beyond 7 days, they've already received study_gap nudges)
        inactive_users = await db.user.find_many(
            where={
                "isActive": True,
                "isOnboarded": True,
                "updatedAt": {"lt": three_days_ago, "gt": seven_days_ago},
            },
            include={
                "preferences": True,
                "reviewItems": {
                    "where": {"nextReviewAt": {"lte": now}},
                    "take": 1,
                },
                "goals": {
                    "where": {"status": "ACTIVE", "targetDate": {"lte": now + timedelta(days=7)}},
                    "take": 1,
                },
            },
            take=50,
        )

        nudges_sent = 0
        for user in inactive_users:
            # Skip if we already sent a re-engagement nudge recently
            existing = await db.aiagenttask.find_first(
                where={
                    "userId": user.id,
                    "taskType": "reengagement",
                    "createdAt": {"gte": now - timedelta(days=5)},
                },
            )
            if existing:
                continue

            # Build personalized message
            user_name = (user.name or "").split()[0] if user.name else "there"
            has_reviews = len(user.reviewItems) > 0 if user.reviewItems else False
            has_upcoming_goals = len(user.goals) > 0 if user.goals else False

            if has_reviews and has_upcoming_goals:
                title = f"Hey {user_name}, you have reviews due and goals approaching!"
                body = "Your spaced repetition items are waiting and a goal deadline is near. A quick session will keep you on track."
            elif has_reviews:
                title = f"Hey {user_name}, you have reviews waiting!"
                body = "Your spaced repetition items are piling up. A 10-minute review session will keep your memory fresh."
            elif has_upcoming_goals:
                title = f"Hey {user_name}, a goal deadline is approaching!"
                body = "You have an active goal due soon. Jump back in to make progress before it's too late."
            else:
                title = f"Welcome back, {user_name}!"
                body = "It's been a few days. Even 15 minutes of study today will make a difference. Your AI tutor is ready when you are."

            # Create nudge record
            await db.aiagenttask.create(
                data={
                    "userId": user.id,
                    "taskType": "reengagement",
                    "status": "sent",
                    "priority": 3,
                    "title": title,
                    "message": body,
                    "scheduledAt": now,
                    "actionData": {
                        "hasReviews": has_reviews,
                        "hasUpcomingGoals": has_upcoming_goals,
                    },
                }
            )

            # Send push notification
            try:
                await send_push_notification(
                    user_id=user.id,
                    title=title,
                    body=body,
                    data={"type": "reengagement"},
                )
            except Exception as push_err:
                logger.debug("Re-engagement push failed for %s: %s", user.id, push_err)

            # Send email as well (fills the gap where push-only wasn't enough)
            try:
                from src.services.email import send_bulk_email

                email_html = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; color: #1F2937;">
                  <h2 style="font-size: 20px;">{title}</h2>
                  <p>{body}</p>
                  <div style="text-align: center; margin: 24px 0;">
                    <a href="https://app.maigie.com/dashboard" style="display: inline-block; padding: 12px 28px; background: #4F46E5; color: white; text-decoration: none; border-radius: 8px; font-weight: 600;">
                      Continue Studying
                    </a>
                  </div>
                  <p style="font-size: 13px; color: #9CA3AF; text-align: center;">Manage email preferences in Settings.</p>
                </div>"""
                # Only send email if user has notifications enabled
                prefs = getattr(user, "preferences", None)
                if not prefs or getattr(prefs, "notifications", True):
                    await send_bulk_email(
                        email=user.email,
                        name=user.name,
                        subject=title,
                        content=email_html,
                    )
            except Exception as email_err:
                logger.debug("Re-engagement email failed for %s: %s", user.id, email_err)

            nudges_sent += 1

        logger.info("Re-engagement nudge: sent %d notifications", nudges_sent)

    except Exception as e:
        logger.error("Re-engagement nudge failed: %s", e, exc_info=True)


@register_task(
    name=TASK_REENGAGEMENT,
    description="Send re-engagement notifications to inactive users",
    category="agent",
    tags=["agent", "retention", "nudge"],
)
@task(name=TASK_REENGAGEMENT, bind=True, max_retries=2)
def reengagement_nudge_task(self: Any):
    """Run re-engagement nudge for inactive users."""
    return run_async_in_celery(_reengagement_nudge_impl())


# ---------------------------------------------------------------------------
#  Deep Wake — Automated full re-engagement for 7-30 day inactive users
# ---------------------------------------------------------------------------

TASK_DEEP_WAKE = "agent.deep_wake"


async def _deep_wake_impl():
    """
    Automated "wake" for users inactive 7-30 days.

    This fills the gap where:
    - Study gap detector only catches users who HAD a streak
    - Re-engagement nudge only covers 3-7 days and only sends push (no email)
    - After 7 days, users previously fell into a dead zone

    Actions per user:
    1. Send personalized email (weekly summary if available, else generic)
    2. Send push notification
    3. Regenerate their study schedule (gives them fresh content to return to)
    4. Create nudge record for tracking

    Runs every 2 days. Cooldown: 7 days between deep wakes per user.
    Limits: max 30 users per run to avoid email flooding.
    """
    await _ensure_db_connected()
    from src.core.database import db
    from src.services.push_notification_service import send_push_notification
    from src.services.schedule_regeneration_service import regenerate_user_schedule
    from src.services.weekly_summary_email_service import (
        generate_weekly_summary_for_user,
        render_weekly_summary_html,
    )
    from src.services.email import send_bulk_email

    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    try:
        # Find users inactive 7-30 days who are onboarded and have some history
        inactive_users = await db.user.find_many(
            where={
                "isActive": True,
                "isOnboarded": True,
                "role": "USER",
                "updatedAt": {"lt": seven_days_ago, "gt": thirty_days_ago},
                # Must have engaged at least once (have chat messages)
                "chatMessages": {"some": {}},
            },
            include={"preferences": True, "userStreak": True},
            take=60,
        )

        woken = 0
        skipped = 0

        for user in inactive_users:
            if woken >= 30:
                break

            # Check notification preferences
            prefs = user.preferences
            if prefs and not getattr(prefs, "notifications", True):
                skipped += 1
                continue

            # Cooldown: don't deep-wake if we already did in the last 7 days
            existing = await db.aiagenttask.find_first(
                where={
                    "userId": user.id,
                    "taskType": "deep_wake",
                    "createdAt": {"gte": now - timedelta(days=7)},
                },
            )
            if existing:
                skipped += 1
                continue

            # Also skip if they got a regular reengagement nudge in the last 3 days
            recent_nudge = await db.aiagenttask.find_first(
                where={
                    "userId": user.id,
                    "taskType": {"in": ["reengagement", "study_gap"]},
                    "createdAt": {"gte": now - timedelta(days=3)},
                },
            )
            if recent_nudge:
                skipped += 1
                continue

            user_name = (user.name or "").split()[0] if user.name else "there"
            days_inactive = (now - user.updatedAt).days

            # 1. Send email
            try:
                summary = await generate_weekly_summary_for_user(user.id)
                if summary:
                    subject, html_content = render_weekly_summary_html(summary)
                else:
                    # Generic wake email
                    streak_line = ""
                    if user.userStreak and user.userStreak.longestStreak > 3:
                        streak_line = (
                            f"<p>You once had a <strong>{user.userStreak.longestStreak}-day streak"
                            f"</strong> — let's build that back up!</p>"
                        )

                    subject = f"Hey {user_name}, your AI tutor is waiting for you"
                    html_content = f"""
                    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; color: #1F2937;">
                      <h2 style="font-size: 22px;">Hey {user_name} 👋</h2>
                      <p>It's been {days_inactive} days since your last session. A lot can happen in {days_inactive} days — but getting back on track only takes 15 minutes.</p>
                      {streak_line}
                      <p>We've refreshed your study schedule with new sessions tailored to where you left off. Here's what you can jump into:</p>
                      <ul style="line-height: 1.8;">
                        <li>Continue your courses where you left off</li>
                        <li>Review topics that need refreshing</li>
                        <li>Chat with your AI tutor about anything new</li>
                      </ul>
                      <div style="text-align: center; margin: 32px 0;">
                        <a href="https://app.maigie.com/dashboard" style="display: inline-block; padding: 14px 32px; background: #4F46E5; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px;">
                          Jump Back In
                        </a>
                      </div>
                      <p style="font-size: 13px; color: #9CA3AF; text-align: center;">
                        You're receiving this because you have an account on Maigie.
                        Manage preferences in Settings.
                      </p>
                    </div>"""

                await send_bulk_email(
                    email=user.email,
                    name=user.name,
                    subject=subject,
                    content=html_content,
                )
            except Exception as email_err:
                logger.debug("Deep wake email failed for %s: %s", user.id, email_err)

            # 2. Send push notification
            try:
                await send_push_notification(
                    user_id=user.id,
                    title=f"Hey {user_name}, we refreshed your study plan!",
                    body=f"It's been {days_inactive} days. Your schedule is updated — jump back in for a quick session.",
                    data={"type": "deep_wake"},
                )
            except Exception as push_err:
                logger.debug("Deep wake push failed for %s: %s", user.id, push_err)

            # 3. Regenerate study schedule (fire and forget)
            try:
                await regenerate_user_schedule(user.id)
            except Exception as sched_err:
                logger.debug("Deep wake schedule regen failed for %s: %s", user.id, sched_err)

            # 4. Record the action
            await db.aiagenttask.create(
                data={
                    "userId": user.id,
                    "taskType": "deep_wake",
                    "status": "sent",
                    "priority": 4,
                    "title": f"Auto wake: {days_inactive} days inactive",
                    "message": f"Sent re-engagement email and regenerated schedule for {user_name} ({days_inactive}d inactive)",
                    "scheduledAt": now,
                    "actionData": {
                        "daysInactive": days_inactive,
                        "hadStreak": bool(user.userStreak and user.userStreak.currentStreak > 0),
                        "automated": True,
                    },
                }
            )

            woken += 1

        logger.info("Deep wake: woken=%d, skipped=%d", woken, skipped)

    except Exception as e:
        logger.error("Deep wake task failed: %s", e, exc_info=True)


@register_task(
    name=TASK_DEEP_WAKE,
    description="Full re-engagement (email + push + schedule regen) for 7-30 day inactive users",
    category="agent",
    tags=["agent", "retention", "wake", "email"],
)
@task(name=TASK_DEEP_WAKE, bind=True, max_retries=2)
def deep_wake_task(self: Any):
    """Run automated deep wake for long-inactive users."""
    return run_async_in_celery(_deep_wake_impl())


# ---------------------------------------------------------------------------
#  Celery Beat Schedule Registration
# ---------------------------------------------------------------------------


def register_agent_beat_tasks():
    """Register periodic Celery Beat tasks for the proactive agent."""
    from celery.schedules import crontab

    register_periodic_task(
        name="agent.goal_deadline_monitor.6h",
        schedule=crontab(minute=0, hour="*/6"),
        task=TASK_GOAL_MONITOR,
    )
    register_periodic_task(
        name="agent.study_gap_detector.daily",
        schedule=crontab(minute=30, hour=8),
        task=TASK_STUDY_GAP,
    )
    register_periodic_task(
        name="agent.review_due_nudge.4h",
        schedule=crontab(minute=0, hour="*/4"),
        task=TASK_REVIEW_NUDGE,
    )
    register_periodic_task(
        name="agent.conversation_summarizer.2h",
        schedule=crontab(minute=15, hour="*/2"),
        task=TASK_CONVO_SUMMARIZER,
    )
    register_periodic_task(
        name="agent.learning_insight_generator.weekly",
        schedule=crontab(minute=0, hour=3, day_of_week=0),
        task=TASK_INSIGHT_GENERATOR,
    )
    register_periodic_task(
        name="agent.reengagement_nudge.daily",
        schedule=crontab(minute=0, hour=10),  # 10 AM daily
        task=TASK_REENGAGEMENT,
    )
    # Deep wake: every 2 days at 11 AM UTC — catches 7-30 day inactive users
    register_periodic_task(
        name="agent.deep_wake.every_2_days",
        schedule=crontab(minute=0, hour=11, day_of_week="1,3,5"),  # Mon, Wed, Fri
        task=TASK_DEEP_WAKE,
    )

    logger.info("Registered agent beat tasks")


register_agent_beat_tasks()
