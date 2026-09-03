"""
Schedule Regeneration Service.

Deletes future AI-generated schedule blocks and creates a new optimized
study schedule based on the user's active courses, goals, reviews, and
past study behavior patterns.
Uses the LLM to intelligently allocate time blocks.
"""

import logging
from datetime import UTC, datetime, timedelta

from src.domains.intelligence.action.skills.handlers import handle_create_schedule
from src.domains.knowledge.repository import knowledge_repo
from src.domains.progress.repository import progress_repo
from src.shared.database import get_session_factory

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
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select

        from src.domains.progress.db_models import ScheduleBlock

        factory = get_session_factory()
        async with factory() as session:
            del_stmt = sa_delete(ScheduleBlock).where(
                ScheduleBlock.user_id == user_id,
                ScheduleBlock.start_at >= now,
                ScheduleBlock.google_calendar_event_id.is_(None),
            )
            result = await session.execute(del_stmt)
            await session.commit()
            deleted = result.rowcount
        logger.info(f"Deleted {deleted} future schedule blocks for user {user_id}")

        # 2. Gather context
        courses, _ = await knowledge_repo.list_courses(
            user_id, where={"archived": False}, skip=0, take=20
        )

        goals, _ = await progress_repo.list_goals(
            user_id, where={"status": "ACTIVE"}, skip=0, take=10
        )

        # Existing events that we must not overlap
        where_existing: dict = {
            "endAt": {"gte": now},
            "startAt": {"lte": future_cutoff},
        }
        existing_events, _ = await progress_repo.list_blocks(
            user_id, where=where_existing, skip=0, take=200
        )

        # 3. Analyze past study behavior
        where_past: dict = {
            "endAt": {"gte": past_window},
            "startAt": {"lte": now},
        }
        past_blocks, _ = await progress_repo.list_blocks(
            user_id, where=where_past, skip=0, take=100
        )

        hour_counts: dict[int, int] = {}
        day_counts: dict[int, int] = {}  # 0=Mon, 6=Sun
        avg_duration_minutes = DEFAULT_BLOCK_MINUTES

        if past_blocks:
            durations = []
            for b in past_blocks:
                start_hour = b.start_at.hour
                hour_counts[start_hour] = hour_counts.get(start_hour, 0) + 1
                weekday = b.start_at.weekday()
                day_counts[weekday] = day_counts.get(weekday, 0) + 1
                dur = (b.end_at - b.start_at).total_seconds() / 60
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
            from src.domains.intelligence.memory.user_memory_impl import (
                user_memory_service,
            )

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
            "\n".join(f"- {c.title} (progress: {int(c.progress or 0)}%)" for c in courses)
            or "No active courses."
        )

        goal_info = (
            "\n".join(
                f"- {g.title} (deadline: {g.target_date.strftime('%Y-%m-%d') if g.target_date else 'none'})"
                for g in goals
            )
            or "No active goals."
        )

        busy_slots = (
            "\n".join(
                f"- {e.title}: {e.start_at.strftime('%Y-%m-%d %H:%M')} to {e.end_at.strftime('%H:%M')}"
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

        # 6. Call the LLM through the chokepoint.
        #
        # **This was the last of Decision L's stragglers, and it was a hand-rolled copy of the thing
        # it now calls.** It built a Gemini client, fell back to OpenAI on any exception, and then
        # stripped markdown fences and parsed JSON — which is `generate_content_json`, minus the
        # meter, the headroom gate, the retry, the tier, the thinking budget and the truncated-JSON
        # repair. About thirty-five lines of duplicated plumbing, and the duplication is what let the
        # cost hide: nothing charged for this call and nothing could.
        #
        # It also hardcoded `gemini-3.5-flash` — the *Plus* model — for every learner, which is drift
        # 23 in miniature. The chokepoint resolves the model from the `operation` itself, so nothing
        # is passed here: at ~225 units this sits below Decision P's threshold and both tiers get the
        # standard model, and the point is that **one place decides** rather than that the answer
        # happens to be the same.
        #
        # `GEMINI_SCHEDULE_AI_MODELS` is no longer read. A per-feature model override cannot coexist
        # with a tier-aware model choice without one of them silently winning, and the tier is the one
        # carrying a commercial promise.
        from src.domains.intelligence.reasoning.llm import THINKING_BOUNDED
        from src.domains.personal_learning.services.llm_resilient import (
            generate_content_json,
        )

        blocks = await generate_content_json(
            prompt,
            temperature=0.7,
            # Bounded: the schedule is assembled from supplied availability, dates and plan items into
            # a fixed JSON shape. Phase 0's middle class.
            thinking=THINKING_BOUNDED,
            user_id=user_id,
            operation="schedule_regeneration",
            # `{}` rather than `None`: this is a background regeneration and the caller's contract is
            # to leave the existing schedule alone on failure, not to raise into a worker.
            fallback={},
        )
        if not blocks:
            logger.error("Schedule regeneration produced nothing for user %s", user_id)
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


async def regenerate_schedule(user_id: str, preferences: dict | None = None) -> None:
    """Alias for regenerate_user_schedule (used by workers)."""
    await regenerate_user_schedule(user_id)
