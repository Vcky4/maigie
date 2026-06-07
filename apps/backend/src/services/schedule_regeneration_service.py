"""
Schedule Regeneration Service.

Deletes future AI-generated schedule blocks and creates a new optimized
study schedule based on the user's active courses, goals, reviews, and
past study behavior patterns.
Uses the LLM to intelligently allocate time blocks.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from src.core.database import db
from src.services.llm import route_request
from src.services.skills.handlers import handle_create_schedule

logger = logging.getLogger(__name__)

# How far ahead to plan
PLAN_DAYS_AHEAD = 14
# Max study blocks per day
MAX_BLOCKS_PER_DAY = 4
# Default block duration in minutes
DEFAULT_BLOCK_MINUTES = 60


async def regenerate_user_schedule(user_id: str) -> None:
    """
    Regenerate the study schedule for a user.

    1. Analyzes past study behavior (preferred hours, session lengths, active days)
    2. Fetches the user's active courses, goals, and pending reviews
    3. Deletes future AI-generated schedule blocks (preserves manually created ones)
    4. Uses LLM to generate an optimized study plan personalized to behavior
    5. Creates new schedule blocks
    """
    try:
        now = datetime.now(UTC)
        future_cutoff = now + timedelta(days=PLAN_DAYS_AHEAD)
        past_window = now - timedelta(days=30)

        # 1. Delete future AI-generated blocks (those without a Google Calendar link)
        deleted = await db.scheduleblock.delete_many(
            where={
                "userId": user_id,
                "startAt": {"gte": now},
                "googleCalendarEventId": None,
            }
        )
        logger.info(f"Deleted {deleted} future schedule blocks for user {user_id}")

        # 2. Gather context
        courses = await db.course.find_many(
            where={"userId": user_id, "status": "ACTIVE"},
            take=20,
            order={"updatedAt": "desc"},
        )

        goals = await db.goal.find_many(
            where={"userId": user_id, "status": "ACTIVE"},
            take=10,
            order={"deadline": "asc"},
        )

        # Existing events that we must not overlap
        existing_events = await db.scheduleblock.find_many(
            where={
                "userId": user_id,
                "startAt": {"gte": now, "lte": future_cutoff},
            },
            order={"startAt": "asc"},
        )

        # 3. Analyze past study behavior
        past_blocks = await db.scheduleblock.find_many(
            where={
                "userId": user_id,
                "startAt": {"gte": past_window, "lt": now},
            },
            order={"startAt": "desc"},
            take=100,
        )

        hour_counts: dict[int, int] = {}
        day_counts: dict[int, int] = {}  # 0=Mon, 6=Sun
        avg_duration_minutes = DEFAULT_BLOCK_MINUTES

        if past_blocks:
            durations = []
            for b in past_blocks:
                start_hour = b.startAt.hour
                hour_counts[start_hour] = hour_counts.get(start_hour, 0) + 1
                weekday = b.startAt.weekday()
                day_counts[weekday] = day_counts.get(weekday, 0) + 1
                dur = (b.endAt - b.startAt).total_seconds() / 60
                if 15 < dur < 300:
                    durations.append(dur)

            if durations:
                avg_duration_minutes = int(sum(durations) / len(durations))

        # Format behavioral insight for the LLM
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if hour_counts:
            sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
            preferred_hours = [f"{h}:00" for h, _ in sorted_hours[:5]]
            behavior_insight = (
                f"Based on the past 30 days, this user typically studies around: "
                f"{', '.join(preferred_hours)}. "
                f"Average session length: {avg_duration_minutes} minutes."
            )
            if day_counts:
                sorted_days = sorted(day_counts.items(), key=lambda x: x[1], reverse=True)
                active_days = [day_names[d] for d, _ in sorted_days[:5]]
                behavior_insight += f" Most active days: {', '.join(active_days)}."
        else:
            behavior_insight = (
                "No past study history available. Use sensible defaults "
                "(mornings 9-12 and early afternoons 14-17, weekdays preferred)."
            )

        # 4. Get user preferences from memory (if available)
        facts_text = ""
        try:
            from src.services.user_memory_service import user_memory_service

            user_facts = await user_memory_service.get_user_facts(
                user_id, category="schedule", limit=10
            )
            if user_facts:
                facts_text = "\n\nUSER STATED PREFERENCES:\n" + "\n".join(
                    f"- {f.get('content', '')}" for f in user_facts if f.get("content")
                )
        except Exception:
            pass  # Non-critical

        # 5. Build LLM prompt
        course_info = (
            "\n".join(
                f"- {c.title} (progress: {c.completedTopics or 0}/{c.totalTopics or 0} topics)"
                for c in courses
            )
            or "No active courses."
        )

        goal_info = (
            "\n".join(
                f"- {g.title} (deadline: {g.deadline.strftime('%Y-%m-%d') if g.deadline else 'none'})"
                for g in goals
            )
            or "No active goals."
        )

        busy_slots = (
            "\n".join(
                f"- {e.title}: {e.startAt.strftime('%Y-%m-%d %H:%M')} to {e.endAt.strftime('%H:%M')}"
                for e in existing_events
            )
            or "No existing commitments."
        )

        today_str = now.strftime("%Y-%m-%d")
        prompt = f"""Generate an optimized study schedule for the next {PLAN_DAYS_AHEAD} days starting from {today_str}.

STUDY BEHAVIOR ANALYSIS:
{behavior_insight}
{facts_text}

USER'S ACTIVE COURSES:
{course_info}

USER'S GOALS:
{goal_info}

EXISTING COMMITMENTS (do NOT overlap):
{busy_slots}

RULES:
- Create up to {MAX_BLOCKS_PER_DAY} study blocks per day
- Each block should be around {avg_duration_minutes} minutes (matching the user's typical session length)
- Schedule blocks at the user's preferred study times (from behavior analysis above)
- Respect the user's active days pattern — lighter schedule on less active days
- Prioritize courses with upcoming deadlines and low completion
- Include review sessions for courses with high progress
- Do NOT create blocks that overlap existing commitments
- Use the user's stated preferences if available

Return a JSON array of objects with these fields:
- title: string (e.g. "Study: Course Name - Topic")
- start_at: ISO datetime string (YYYY-MM-DDTHH:MM:SSZ)
- end_at: ISO datetime string (YYYY-MM-DDTHH:MM:SSZ)
- course_id: string or null
- goal_id: string or null
- description: string (brief what to focus on)

Return ONLY the JSON array, no other text."""

        # 6. Call LLM
        response_text = ""
        async for chunk in route_request(
            messages=[{"role": "user", "content": prompt}],
            user_id=user_id,
            stream=False,
        ):
            if hasattr(chunk, "content") and chunk.content:
                response_text += chunk.content
            elif isinstance(chunk, str):
                response_text += chunk

        # 7. Parse response and create blocks
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

        try:
            blocks = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse LLM schedule response: {e}\nResponse: {response_text[:500]}"
            )
            return

        if not isinstance(blocks, list):
            logger.error(f"LLM returned non-list for schedule: {type(blocks)}")
            return

        # 8. Create schedule blocks
        created_count = 0
        for block in blocks:
            try:
                await handle_create_schedule(
                    args={
                        "title": block.get("title", "Study session"),
                        "description": block.get("description"),
                        "start_at": block.get("start_at"),
                        "end_at": block.get("end_at"),
                        "course_id": block.get("course_id"),
                        "goal_id": block.get("goal_id"),
                    },
                    user_id=user_id,
                )
                created_count += 1
            except Exception as e:
                logger.warning(f"Failed to create schedule block: {e}")
                continue

        logger.info(
            f"Regenerated schedule for user {user_id}: "
            f"created {created_count}/{len(blocks)} blocks for next {PLAN_DAYS_AHEAD} days"
        )

    except Exception as e:
        logger.error(f"Schedule regeneration failed for user {user_id}: {e}", exc_info=True)
