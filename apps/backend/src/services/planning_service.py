"""
Multi-Step Planning Service.

Enables the AI to decompose complex requests into multi-step plans
with courses, goals, schedules, and milestones.

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.database import db
from src.services.action_service import action_service

logger = logging.getLogger(__name__)


async def _call_gemini_for_plan(prompt: str, max_tokens: int = 1200) -> dict | None:
    """Call Gemini for plan generation. Returns parsed JSON or None."""
    try:
        import google.generativeai as genai
        import os

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "models/gemini-2.0-flash",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.5,
            ),
        )
        response = await model.generate_content_async(prompt)
        text = (response.text or "").strip()
        if not text:
            return None

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as e:
        logger.error("Plan generation LLM call failed: %s", e)
        return None


async def create_study_plan(
    user_id: str,
    goal: str,
    duration_weeks: int = 4,
    context: dict | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """
    Decompose a study goal into a multi-step plan.

    Creates:
    - A course (if the topic doesn't exist)
    - A goal with target date
    - Schedule blocks distributed across the duration
    - Milestones as sub-goals

    Returns:
        dict with plan details, created entity IDs, and summary.
    """
    now = datetime.now(UTC)
    target_date = now + timedelta(weeks=duration_weeks)

    # 1. Check for existing courses to avoid duplicates
    existing_courses = await db.course.find_many(
        where={"userId": user_id, "archived": False},
        take=10,
    )
    existing_titles = [c.title for c in existing_courses]

    # 2. Fetch user preferences for scheduling
    user = await db.user.find_unique(
        where={"id": user_id},
        include={"preferences": True},
    )
    user_name = (user.name or "").split()[0] if user and user.name else "there"
    timezone = "UTC"
    if user and user.preferences:
        timezone = user.preferences.timezone or "UTC"

    # 3. Generate plan using LLM
    if progress_callback:
        await progress_callback(
            10, "planning", "Analyzing your goal and generating a study plan..."
        )

    prompt = f"""You are Maigie, an AI study companion. Create a detailed study plan for the following goal.

Goal: {goal}
Duration: {duration_weeks} weeks (from {now.strftime('%B %d, %Y')} to {target_date.strftime('%B %d, %Y')})
User's existing courses: {', '.join(existing_titles) if existing_titles else 'None'}
Timezone: {timezone}

Return a JSON object with:
- "plan_title": Short title for this plan (e.g., "Organic Chemistry Mastery Plan")
- "course": {{
    "title": "Course title (use existing course if relevant)",
    "use_existing": true/false (true if matches an existing course title exactly),
    "description": "Course description",
    "modules": [
      {{
        "title": "Module title",
        "topics": ["Topic 1", "Topic 2", ...]
      }}
    ]
  }}
- "goal": {{
    "title": "Goal title",
    "description": "What achieving this goal means"
  }}
- "milestones": [
    {{
      "title": "Milestone title",
      "week": 1,
      "description": "What to accomplish by this point"
    }}
  ]
- "schedule": {{
    "sessions_per_week": 3-5,
    "hours_per_session": 1-2,
    "preferred_days": ["Monday", "Wednesday", "Friday"]
  }}
- "study_tips": ["Tip 1", "Tip 2", "Tip 3"]

Output only valid JSON. Make it realistic and achievable."""

    plan = await _call_gemini_for_plan(prompt)
    if not plan:
        return {
            "status": "error",
            "message": "Failed to generate study plan. Please try again.",
        }

    if progress_callback:
        await progress_callback(30, "creating", "Creating your course and materials...")

    created_ids: dict[str, str] = {}
    results = []

    # 4. Create or find course
    course_data = plan.get("course", {})
    course_id = None

    if course_data.get("use_existing"):
        # Find matching existing course
        for c in existing_courses:
            if c.title.lower() == course_data.get("title", "").lower():
                course_id = c.id
                break

    if not course_id:
        # Create new course
        try:
            course_result = await action_service.create_course(
                data={
                    "title": course_data.get("title", plan.get("plan_title", goal)),
                    "description": course_data.get("description", f"Study plan for: {goal}"),
                    "modules": course_data.get("modules", []),
                },
                user_id=user_id,
                progress_callback=progress_callback,
            )
            if course_result.get("status") == "success":
                course_id = course_result.get("course_id")
                created_ids["course_id"] = course_id
                results.append({"type": "course_created", "id": course_id})
        except Exception as e:
            logger.warning("Failed to create course for plan: %s", e)

    if progress_callback:
        await progress_callback(50, "goals", "Setting up goals and milestones...")

    # 5. Create main goal
    goal_data = plan.get("goal", {})
    try:
        goal_result = await action_service.create_goal(
            data={
                "title": goal_data.get("title", goal),
                "description": goal_data.get("description", f"Study plan goal: {goal}"),
                "targetDate": target_date.isoformat(),
                "courseId": course_id,
            },
            user_id=user_id,
        )
        if goal_result.get("status") == "success":
            created_ids["goal_id"] = goal_result.get("goal_id")
            results.append({"type": "goal_created", "id": goal_result.get("goal_id")})
    except Exception as e:
        logger.warning("Failed to create goal for plan: %s", e)

    # 6. Create milestone sub-goals
    milestones = plan.get("milestones", [])
    for ms in milestones:
        week = ms.get("week", 1)
        ms_date = now + timedelta(weeks=week)
        try:
            ms_result = await action_service.create_goal(
                data={
                    "title": f"Milestone: {ms.get('title', f'Week {week} checkpoint')}",
                    "description": ms.get("description", ""),
                    "targetDate": ms_date.isoformat(),
                    "courseId": course_id,
                },
                user_id=user_id,
            )
            if ms_result.get("status") == "success":
                results.append({"type": "milestone_created", "id": ms_result.get("goal_id")})
        except Exception as e:
            logger.warning("Failed to create milestone: %s", e)

    if progress_callback:
        await progress_callback(75, "scheduling", "Building your study schedule...")

    # 7. Create schedule blocks
    schedule_config = plan.get("schedule", {})
    sessions_per_week = schedule_config.get("sessions_per_week", 3)
    hours_per_session = schedule_config.get("hours_per_session", 1)
    preferred_days = schedule_config.get("preferred_days", ["Monday", "Wednesday", "Friday"])

    day_map = {
        "Monday": 0,
        "Tuesday": 1,
        "Wednesday": 2,
        "Thursday": 3,
        "Friday": 4,
        "Saturday": 5,
        "Sunday": 6,
    }
    target_weekdays = [day_map.get(d, 0) for d in preferred_days[:sessions_per_week]]

    schedules_created = 0
    for week_num in range(duration_weeks):
        week_start = now + timedelta(weeks=week_num)
        for target_day in target_weekdays:
            # Calculate the date for this day of the week
            days_ahead = target_day - week_start.weekday()
            if days_ahead < 0:
                days_ahead += 7
            session_date = week_start + timedelta(days=days_ahead)

            if session_date < now:
                continue
            if session_date > target_date:
                break

            # Set session at 9 AM (adjustable)
            start_at = session_date.replace(hour=9, minute=0, second=0, microsecond=0)
            end_at = start_at + timedelta(hours=hours_per_session)

            try:
                sched_result = await action_service.create_schedule(
                    data={
                        "title": f"Study: {course_data.get('title', goal)[:40]}",
                        "description": f"Study session for plan: {plan.get('plan_title', goal)}",
                        "startAt": start_at.isoformat(),
                        "endAt": end_at.isoformat(),
                        "courseId": course_id,
                        "goalId": created_ids.get("goal_id"),
                    },
                    user_id=user_id,
                )
                if sched_result.get("status") == "success":
                    schedules_created += 1
            except Exception as e:
                logger.warning("Failed to create schedule block: %s", e)

            if schedules_created >= sessions_per_week * duration_weeks:
                break

    if progress_callback:
        await progress_callback(100, "done", "Your study plan is ready!")

    return {
        "status": "success",
        "plan_title": plan.get("plan_title", goal),
        "created": {
            "course_id": created_ids.get("course_id"),
            "goal_id": created_ids.get("goal_id"),
            "milestones": len(milestones),
            "schedule_blocks": schedules_created,
        },
        "study_tips": plan.get("study_tips", []),
        "summary": (
            f"Created a {duration_weeks}-week study plan for '{goal}' with "
            f"{len(milestones)} milestones and {schedules_created} scheduled sessions."
        ),
    }


async def check_plan_progress(user_id: str, course_id: str | None = None) -> dict:
    """
    Evaluate progress on a study plan and suggest adjustments.
    """
    try:
        now = datetime.now(UTC)
        results = {"overall": "on_track", "suggestions": []}

        # Check goals approaching deadline with low progress
        goals = await db.goal.find_many(
            where={
                "userId": user_id,
                "status": "ACTIVE",
                **({"courseId": course_id} if course_id else {}),
            },
            take=10,
        )

        for goal in goals:
            if goal.targetDate:
                days_left = (goal.targetDate - now).days
                progress = goal.progress or 0

                if days_left <= 7 and progress < 50:
                    results["overall"] = "behind"
                    results["suggestions"].append(
                        f"'{goal.title}' is due in {days_left} days but only {progress:.0f}% complete. "
                        "Consider adding extra study sessions."
                    )
                elif days_left <= 14 and progress < 30:
                    results["overall"] = "at_risk"
                    results["suggestions"].append(
                        f"'{goal.title}' needs attention — {progress:.0f}% done with {days_left} days left."
                    )

        # Check schedule adherence
        week_ago = now - timedelta(days=7)
        scheduled = await db.scheduleblock.count(
            where={
                "userId": user_id,
                "startAt": {"gte": week_ago, "lte": now},
                **({"courseId": course_id} if course_id else {}),
            },
        )

        completed_logs = await db.schedulebehaviourlog.count(
            where={
                "userId": user_id,
                "behaviourType": "COMPLETED",
                "createdAt": {"gte": week_ago},
            },
        )

        if scheduled > 0:
            adherence = round((completed_logs / scheduled) * 100)
            results["schedule_adherence"] = adherence
            if adherence < 50:
                results["suggestions"].append(
                    f"Schedule adherence is low ({adherence}%). Consider adjusting session times."
                )

        return results

    except Exception as e:
        logger.error("Failed to check plan progress: %s", e)
        return {"overall": "unknown", "error": str(e)}
