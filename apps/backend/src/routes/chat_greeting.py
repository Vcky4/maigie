"""
Personalized greeting context + prompt + UI components for new chat (WebSocket).

Extracted from chat.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


async def _build_greeting_context(db_client, user) -> dict:
    """Fetch user data for a personalized AI greeting on new chat."""
    now = datetime.now(UTC)
    ctx: dict = {
        "name": getattr(user, "name", "") or "",
        "current_time": now.strftime("%A, %B %d, %Y at %H:%M UTC"),
    }

    try:
        courses = await db_client.course.find_many(
            where={"userId": user.id, "archived": False},
            order={"updatedAt": "desc"},
            take=5,
            include={"modules": {"include": {"topics": True}}},
        )
        ctx["courses"] = []
        ctx["courses_for_component"] = []
        for c in courses:
            total = sum(len(m.topics) for m in c.modules)
            completed = sum(1 for m in c.modules for t in m.topics if t.completed)
            progress = round((completed / total * 100) if total > 0 else 0)
            ctx["courses"].append(
                {
                    "title": c.title,
                    "progress": progress,
                    "totalTopics": total,
                    "completedTopics": completed,
                }
            )
            card = {
                "courseId": c.id,
                "id": c.id,
                "title": c.title,
                "description": (c.description or "")[:500],
                "progress": float(progress),
                "difficulty": getattr(c, "difficulty", None),
                "completedTopics": completed,
                "totalTopics": total,
            }
            next_topic = None
            if total > 0 and completed < total:
                for mod in c.modules or []:
                    for t in mod.topics or []:
                        if not getattr(t, "completed", False):
                            next_topic = {
                                "topicId": t.id,
                                "topicTitle": getattr(t, "title", "Topic"),
                                "moduleId": mod.id,
                                "moduleTitle": getattr(mod, "title", "Module"),
                            }
                            break
                    if next_topic:
                        break
            if next_topic:
                card["nextTopic"] = next_topic
            ctx["courses_for_component"].append(card)
    except Exception:
        ctx["courses"] = []
        ctx["courses_for_component"] = []

    try:
        goals = await db_client.goal.find_many(
            where={"userId": user.id, "status": "ACTIVE"},
            take=5,
        )
        ctx["goals"] = [
            {
                "title": g.title,
                "progress": g.progress or 0,
                "targetDate": (g.targetDate.isoformat() if g.targetDate else None),
            }
            for g in goals
        ]
        ctx["goals_for_component"] = [
            {
                "goalId": g.id,
                "id": g.id,
                "title": g.title,
                "description": g.description or "",
                "targetDate": g.targetDate.isoformat() if g.targetDate else None,
                "progress": g.progress or 0,
                "status": g.status,
                "courseId": g.courseId,
                "topicId": g.topicId,
            }
            for g in goals
        ]
    except Exception:
        ctx["goals"] = []
        ctx["goals_for_component"] = []

    try:
        schedules = await db_client.scheduleblock.find_many(
            where={
                "userId": user.id,
                "startAt": {"gte": now, "lte": now + timedelta(days=3)},
            },
            order={"startAt": "asc"},
            take=5,
        )
        ctx["schedules"] = [{"title": s.title, "startAt": s.startAt.isoformat()} for s in schedules]
        ctx["schedules_for_component"] = [
            {
                "scheduleId": s.id,
                "id": s.id,
                "title": s.title,
                "startAt": s.startAt.isoformat() if s.startAt else "",
                "endAt": s.endAt.isoformat() if s.endAt else "",
                "description": s.description or "",
                "courseId": getattr(s, "courseId", None),
                "topicId": getattr(s, "topicId", None),
                "goalId": getattr(s, "goalId", None),
                "reviewItemId": getattr(s, "reviewItemId", None),
            }
            for s in schedules
        ]
    except Exception:
        ctx["schedules"] = []
        ctx["schedules_for_component"] = []

    try:
        streak = await db_client.userstreak.find_unique(where={"userId": user.id})
        ctx["streak"] = streak.currentStreak if streak else 0
    except Exception:
        ctx["streak"] = 0

    try:
        pending = await db_client.reviewitem.find_many(
            where={"userId": user.id, "nextReviewAt": {"lte": now}},
            order={"nextReviewAt": "asc"},
            include={"course": True, "topic": True},
            take=3,
        )
        ctx["pendingReviews"] = await db_client.reviewitem.count(
            where={"userId": user.id, "nextReviewAt": {"lte": now}}
        )
        ctx["reviews_for_component"] = [
            {
                "id": r.id,
                "reviewItemId": r.id,
                "topicId": r.topicId,
                "topicTitle": (
                    r.topic.title
                    if getattr(r, "topic", None)
                    else getattr(r, "topicTitle", "Topic")
                ),
                "courseId": r.courseId,
                "courseTitle": (
                    r.course.title
                    if getattr(r, "course", None)
                    else getattr(r, "courseTitle", "Course")
                ),
                "nextReviewAt": r.nextReviewAt.isoformat(),
                "strength": getattr(r, "strength", "moderate"),
            }
            for r in pending
        ]
    except Exception:
        ctx["pendingReviews"] = 0
        ctx["reviews_for_component"] = []

    return ctx


def _build_greeting_prompt(context: dict) -> str:
    """Build a personalized greeting prompt for the LLM."""
    name = context.get("name", "").split()[0] if context.get("name") else "there"
    current_time = context.get("current_time", "")

    parts = [
        f"Current Date & Time: {current_time}",
        f"User's first name: {name}",
    ]

    courses = context.get("courses", [])
    if courses:
        lines = [
            f"  - {c['title']}: {c['progress']}% complete "
            f"({c['completedTopics']}/{c['totalTopics']} topics)"
            for c in courses
        ]
        parts.append("User's courses:\n" + "\n".join(lines))
    else:
        parts.append("User has no courses yet.")

    goals = context.get("goals", [])
    if goals:
        lines = [
            f"  - {g['title']}: {g['progress']}% progress"
            + (f" (target: {g['targetDate']})" if g.get("targetDate") else "")
            for g in goals
        ]
        parts.append("Active goals:\n" + "\n".join(lines))

    schedules = context.get("schedules", [])
    if schedules:
        lines = [f"  - {s['title']} at {s['startAt']}" for s in schedules]
        parts.append("Upcoming schedule (next 3 days):\n" + "\n".join(lines))

    streak = context.get("streak", 0)
    if streak > 0:
        parts.append(f"Current study streak: {streak} days")

    pending_reviews = context.get("pendingReviews", 0)
    if pending_reviews > 0:
        parts.append(f"Pending spaced repetition reviews: {pending_reviews}")

    data_section = "\n".join(parts)

    return (
        "You are starting a new conversation with the user. Generate a warm, "
        "hyper-contextual, encouraging, and highly dynamic greeting as Maigie, their academic operating system.\n\n"
        f"User Context:\n{data_section}\n\n"
        "Guidelines:\n"
        "- Keep it concise (2-4 sentences max)\n"
        "- Address the user by their first name\n"
        "- Celebrate any recent achievements (streaks, finished topics)\n"
        "- Offer a brief, powerful piece of encouragement (e.g. 'You showed up today, that matters', or 'Glad to see you!')\n"
        "- Reference specific things they're working on if available\n"
        "- Suggest ONE specific thing they could do next (continue a course, "
        "work on a goal, check their schedule, do their reviews, etc.)\n"
        "- Be encouraging but natural, not over-the-top\n"
        "- Consider the time of day (morning/afternoon/evening) for appropriate greetings\n"
        "- If they have a study streak going, briefly mention it to motivate them\n"
        "- If they have pending reviews, suggest they tackle those\n"
        "- If they have upcoming schedules, give them a heads up\n"
        "- If they have no courses/goals yet, encourage them to encourage them to create their first course\n"
        "- Vary your style — don't always structure the greeting the same way\n"
        "- Do NOT use any tools — just respond with the greeting text directly\n"
    )


def _build_greeting_components(greeting_ctx: dict) -> list[dict]:
    """
    Build at most **1** focused component payload for the greeting message.
    Priority: Reviews > Next session > Course pick-up > Goals (capped at 2)
    """
    from src.services.component_response_service import (
        format_component_response,
        format_list_component_response,
    )

    reviews = greeting_ctx.get("reviews_for_component") or []
    schedules = greeting_ctx.get("schedules_for_component") or []
    courses = greeting_ctx.get("courses_for_component") or []
    goals = greeting_ctx.get("goals_for_component") or []

    if reviews:
        return [
            format_component_response(
                "ReviewListMessage",
                {"reviews": reviews[:3]},
                text=None,
            )
        ]

    if schedules:
        next_session = schedules[0]
        return [
            format_component_response(
                "ScheduleBlockMessage",
                next_session,
                text=None,
            )
        ]

    if courses:
        pick_up = next((c for c in courses if c.get("nextTopic")), None)
        if pick_up:
            return [
                format_component_response(
                    "CourseCardMessage",
                    pick_up,
                    text=None,
                )
            ]

    if goals:
        return [
            format_list_component_response(
                "GoalListMessage",
                goals[:2],
                text=None,
            )
        ]

    return []
