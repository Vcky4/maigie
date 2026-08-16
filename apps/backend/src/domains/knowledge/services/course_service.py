"""
Course lifecycle — create, update, delete, list, progress calculation.

Delegates AI generation to the Intelligence domain (via background tasks).
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.domains.identity.db_models import User
from src.shared.events import emit
from src.shared.exceptions import ForbiddenError, NotFoundError, ValidationError

from ..events import emit_course_created, emit_topic_completed, emit_topic_uncompleted
from ..repository import knowledge_repo

logger = logging.getLogger(__name__)

#: Who a generated course is taught by. A constant rather than a literal at each write site, because
#: the name appears on the course page and the two places that set it must not drift apart.
AI_INSTRUCTOR_NAME = "Maigie"
AI_INSTRUCTOR_ROLE = "AI learning designer"

#: The role given to a learner who takes a course into a space they own. They are not credited with
#: writing it — Maigie may have generated the material — but they are the person answering for it in
#: that space, which is what the panel is stating.
SPACE_OWNER_INSTRUCTOR_ROLE = "Space lead"


# ---------------------------------------------------------------------------
# Ownership checks
# ---------------------------------------------------------------------------


async def check_course_ownership(course_id: str, user_id: str):
    """Verify course belongs to user. Returns course or raises."""
    course = await knowledge_repo.find_course(course_id, user_id)
    if not course:
        raise NotFoundError("Course", course_id)
    return course


async def check_module_ownership(module_id: str, user_id: str):
    """Verify module belongs to a course owned by user. Returns (module, course)."""
    module = await knowledge_repo.find_module(module_id)
    if not module or not module.course:
        raise NotFoundError("Module", module_id)
    # `user_id`, not `userId`: the column is camelCase but the mapped attribute is not.
    # Written the wrong way, this raised `AttributeError` before it could compare — so
    # the check neither allowed nor forbade, it answered `500`. It failed closed by
    # accident rather than by design, which is why it was not a security hole and also
    # why no module route has ever worked.
    if module.course.user_id != user_id:
        raise ForbiddenError("You do not own this module")
    return module, module.course


async def check_topic_ownership(topic_id: str, user_id: str):
    """Verify topic belongs to a course owned by user. Returns (topic, module, course)."""
    topic = await knowledge_repo.find_topic(topic_id)
    if not topic or not topic.module or not topic.module.course:
        raise NotFoundError("Topic", topic_id)
    if topic.module.course.user_id != user_id:
        raise ForbiddenError("You do not own this topic")
    return topic, topic.module, topic.module.course


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


async def calculate_course_progress(course_id: str) -> tuple[float, int, int]:
    """Calculate (progress%, total_topics, completed_topics) for a course."""
    modules = await knowledge_repo.list_modules(course_id)
    total = 0
    completed = 0
    for module in modules:
        topics = getattr(module, "topics", []) or []
        total += len(topics)
        completed += sum(1 for t in topics if t.completed)
    progress = (completed / total * 100) if total > 0 else 0.0
    return round(progress, 1), total, completed


def calculate_module_progress(module) -> dict[str, Any]:
    """Calculate progress for a single module (with topics loaded)."""
    topics = getattr(module, "topics", []) or []
    total = len(topics)
    completed = sum(1 for t in topics if t.completed)
    progress = (completed / total * 100) if total > 0 else 0.0
    # Keys are camelCase because this dict feeds a Pydantic response model; the reads
    # are snake_case because that is what the ORM exposes. Mixing them up is what made
    # every course route answer `500`.
    return {
        "id": module.id,
        "courseId": module.course_id,
        "title": module.title,
        "order": module.order,
        "description": module.description,
        "completed": total > 0 and completed == total,
        "progress": round(progress, 1),
        "topicCount": total,
        "completedTopicCount": completed,
        "topics": topics,
        "createdAt": module.created_at,
        "updatedAt": module.updated_at,
    }


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


def _streak_from_dates(active_dates: set[date], today: date) -> int:
    """Consecutive days ending today, or ending yesterday if today is unused.

    Same rule as the flashcard and study-plan streaks: a learner who studied six days straight and has
    not started this morning is on a six-day run, not a broken one. Duplicated rather than shared,
    deliberately — this counts completed topics, the others count graded cards and finished plan tasks,
    and a future change to one must not silently move the others.
    """
    if not active_dates:
        return 0
    cursor = today
    if cursor not in active_dates:
        cursor = today - timedelta(days=1)
        if cursor not in active_dates:
            return 0
    length = 0
    while cursor in active_dates:
        length += 1
        cursor -= timedelta(days=1)
    return length


async def get_dashboard(*, user_id: str) -> dict[str, Any]:
    """Everything the course library shows above its grid, in one request.

    The week runs from Monday in the learner's own timezone, and `timezoneKnown` says so when that
    zone was never captured — `UserPreferences.timezone` is `NOT NULL` defaulting to `"UTC"`, so
    reading it without checking the source makes every learner look like they are in London.

    The featured course is the one whose most recent topic completion is newest, which is what
    "resume" means. Not the most recently *updated* course: renaming a course would promote it above
    the one the learner is actually working through.
    """
    from src.shared.time.learner_timezone import resolve_learner_timezone, to_learner_local

    learner_timezone = await resolve_learner_timezone(user_id)
    now_local = to_learner_local(datetime.now(UTC), learner_timezone)
    week_start_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Back to instants for the query: the columns are `timestamptz`, and comparing them to a local
    # wall clock would shift the window by the learner's offset.
    week_start = week_start_local.astimezone(UTC)
    week_end = (week_start_local + timedelta(days=7)).astimezone(UTC)

    active, archived, topic_totals, completion_dates, weekly_hours, recent = await asyncio.gather(
        # `take=1` because only the count is wanted; the rows are discarded. A dedicated count query
        # would be one more method for the same number the list already returns.
        knowledge_repo.list_courses(user_id, where={"archived": False}, skip=0, take=1),
        knowledge_repo.list_courses(user_id, where={"archived": True}, skip=0, take=1),
        knowledge_repo.library_topic_totals(user_id),
        knowledge_repo.completed_topic_dates(user_id),
        knowledge_repo.completed_hours_between(user_id, week_start, week_end),
        knowledge_repo.recently_completed_topics(user_id, limit=5),
    )

    _, active_count = active
    _, archived_count = archived
    total_topics, completed_topics = topic_totals

    # Converted to the learner's local dates before being made a set, so two completions on the same
    # local evening count as one day even when they straddle UTC midnight.
    local_dates = {to_learner_local(when, learner_timezone).date() for when in completion_dates}
    weekly_completed = sum(1 for when in completion_dates if week_start <= when < week_end)

    return {
        "activeCourses": active_count,
        "archivedCourses": archived_count,
        "totalTopics": total_topics,
        "completedTopics": completed_topics,
        "weeklyHours": round(weekly_hours, 1),
        "weeklyTopicsCompleted": weekly_completed,
        "currentStreakDays": _streak_from_dates(local_dates, now_local.date()),
        "recent": recent,
        "timezoneKnown": learner_timezone.is_known,
    }


async def create_course(*, user: User, data: dict[str, Any]) -> Any:
    """Create a course manually."""
    # Free tier limit: 2 courses per month
    if str(user.tier) == "FREE":
        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
        count = await knowledge_repo.count_courses(
            {"userId": user.id, "createdAt": {"gte": thirty_days_ago}}
        )
        if count >= 2:
            raise ForbiddenError(
                "You can only create 2 courses per month on the free plan. "
                "Start a free trial for unlimited courses."
            )

    create_data = {
        "userId": user.id,
        "title": data["title"],
        "description": data.get("description"),
        "difficulty": data.get("difficulty"),
        "targetDate": data.get("targetDate"),
        "isAIGenerated": data.get("isAIGenerated", False),
    }
    if data.get("spaceId"):
        create_data["spaceId"] = data["spaceId"]
    # Named one by one rather than by spreading `data`, because this dict is the allowlist that keeps
    # a client from setting `progress` or `userId` directly. The cost of that safety is that a field
    # added to `CourseCreate` and not added here is accepted and silently discarded, which is how
    # `search` was lost on the list endpoint — so each of these is listed explicitly.
    for optional in ("category", "tags", "outcomes", "instructorName", "instructorRole"):
        if data.get(optional) is not None:
            create_data[optional] = data[optional]

    # A generated course is taught by Maigie, and the credit is written at creation rather than
    # inferred at read time. Inferring it would mean every reader repeating the rule "if AI-generated
    # and nobody named, say Maigie", and the moment one reader forgot, the same course would show an
    # instructor on one page and none on another.
    #
    # Only when the caller named nobody: an explicit instructor always wins, which is what lets a
    # course authored for a space keep its author after being generated from a syllabus.
    if create_data["isAIGenerated"] and not create_data.get("instructorName"):
        create_data["instructorName"] = AI_INSTRUCTOR_NAME
        create_data["instructorRole"] = AI_INSTRUCTOR_ROLE

    course = await knowledge_repo.create_course(create_data)
    await emit_course_created(user.id, course.id, is_ai_generated=create_data["isAIGenerated"])
    return course


async def update_course(*, course_id: str, user_id: str, data: dict[str, Any]) -> Any:
    """Update course metadata."""
    await check_course_ownership(course_id, user_id)
    update_data = {k: v for k, v in data.items() if v is not None}
    if not update_data:
        return await knowledge_repo.find_course_with_modules(course_id, user_id)
    await knowledge_repo.update_course(course_id, update_data)
    return await knowledge_repo.find_course_with_modules(course_id, user_id)


async def archive_course(*, course_id: str, user_id: str) -> Any:
    """Archive a course (soft delete)."""
    await check_course_ownership(course_id, user_id)
    await knowledge_repo.update_course(course_id, {"archived": True})
    return await knowledge_repo.find_course_with_modules(course_id, user_id)


async def delete_course(*, course_id: str, user_id: str) -> None:
    """Delete a course with cascade."""
    await check_course_ownership(course_id, user_id)
    await knowledge_repo.delete_course(course_id)


# ---------------------------------------------------------------------------
# Topic completion
# ---------------------------------------------------------------------------


async def toggle_topic_completion(
    *, topic_id: str, module_id: str, course_id: str, user_id: str, completed: bool
) -> Any:
    """Mark/unmark a topic as completed. Emits domain events."""
    topic, module, course = await check_topic_ownership(topic_id, user_id)

    if topic.module_id != module_id or module.course_id != course_id:
        raise ValidationError("Topic does not belong to the specified module/course")

    # `completedAt` is set here and cleared on reopen, rather than left to a database default. A
    # pending topic carrying a completion time is a row that contradicts itself, and the activity feed
    # and streak both read this column — they would count a reopened topic as still done.
    updated = await knowledge_repo.update_topic(
        topic_id,
        {"completed": completed, "completedAt": datetime.now(UTC) if completed else None},
    )

    # `Course.progress` is stored as well as computed, and this is what keeps it true. It used to be
    # written by nothing, so it read `0` for every course — and two readers outside this domain took
    # that at face value: the classroom's assigned-course list, and the course summary given to the
    # model as memory context. Both told their reader that nothing had been completed.
    await knowledge_repo.recount_course_progress(course_id)

    if completed:
        await emit_topic_completed(user_id, topic_id, course_id)
        # Spaced repetition: Progress domain listens to topic.completed event
        # and creates ReviewSchedule automatically via the event bus
    else:
        await emit_topic_uncompleted(user_id, topic_id, course_id)

    return updated
