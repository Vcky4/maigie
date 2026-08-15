"""
Study Plan service — AI-generated day-by-day study plans.

Distributes topics across available days, respects behaviour patterns,
interleaves spaced repetition reviews, and adapts when learners fall behind.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from src.shared.exceptions import NotFoundError
from src.shared.time.learner_timezone import to_learner_local

from ..repository import personal_learning_repo as repo
from . import prep_intent, prep_plan_adaptive

logger = logging.getLogger(__name__)


async def generate_plan(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Generate a day-by-day study plan.

    Req 7.1: Distribute topics across available study days.
    Req 7.2: Respect learner's observed study patterns from behaviour tracker.
    Req 7.3: Interleave spaced repetition reviews among new material.

    FREE: Basic timeline distribution.
    PLUS: Adaptive scheduling that adjusts based on quiz performance and behaviour.
    """
    from . import feature_tier_service, trial_service

    title = data["title"]
    goal_description = data.get("goalDescription")
    deadline_raw = data.get("deadline")
    prep_id = data.get("prepId")

    # Determine quality tier for plan generation
    quality_tier = await feature_tier_service.get_quality_tier(user_id)
    is_adaptive = quality_tier == "plus"

    if is_adaptive:
        await trial_service.record_plus_feature_used(user_id, "study_plan")

    # Parse deadline — fall back to 30 days from now if not provided.
    if isinstance(deadline_raw, datetime):
        deadline = deadline_raw
    elif isinstance(deadline_raw, str) and deadline_raw.strip():
        deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
    else:
        deadline = datetime.now(UTC).replace(hour=23, minute=59) + timedelta(days=30)

    # Ensure timezone-aware for consistent math downstream
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)

    # Get behaviour profile for sustainable scheduling
    profile = await repo.get_profile_by_user(user_id)
    avg_session_minutes = 60  # default
    if profile and profile.avg_session_minutes:
        avg_session_minutes = profile.avg_session_minutes

    # When the plan comes from a preparation, respect the pace the learner chose in
    # the create wizard. `daily_minute_budget` takes the smaller of stated intent
    # and observed behaviour, so an ambitious pace cannot produce a plan the
    # learner has never sustained. With no pace stored this yields the previous
    # behaviour unchanged.
    prep_pace: str | None = None
    # The preparation's stated readiness target, used by adaptive scheduling to size
    # how far each topic has to travel. Read here rather than re-fetched, and left
    # None when the learner set no target so nothing invents a goal for them.
    prep_target_mastery: float | None = None
    if prep_id:
        prep_for_pace = await repo.find_exam_prep(prep_id, user_id)
        prep_pace = getattr(prep_for_pace, "pace", None) if prep_for_pace else None
        target = getattr(prep_for_pace, "target_readiness", None) if prep_for_pace else None
        prep_target_mastery = float(target) if target else None

    if prep_pace:
        max_daily_minutes = prep_intent.daily_minute_budget(
            prep_pace, behaviour_minutes=avg_session_minutes
        )
    else:
        # Sustainable daily limit: avg * 1.5 or max 120 min
        max_daily_minutes = min(avg_session_minutes * 1.5, 120)

    # Get topics to distribute (from prep if provided)
    topics_to_plan: list[dict[str, Any]] = []
    # The ORM rows are kept as well as the dicts: adaptive scheduling ranks topics
    # by measured competence and needs the rows, not a flattened copy.
    prep_topic_rows: list[Any] = []
    if prep_id:
        prep_topics = await repo.list_prep_topics(prep_id)
        for t in prep_topics:
            if t.status != "MASTERED":
                prep_topic_rows.append(t)
                topics_to_plan.append(
                    {
                        "title": t.title,
                        "estimatedMinutes": t.estimated_minutes or 30,
                        "topicId": None,
                        "prepTopicId": t.id,
                        "type": "STUDY",
                    }
                )

    # Validated before generation, not after. Rejecting a course the learner does not own
    # only after an LLM round trip would waste the call and leave them staring at an
    # error for a plan that was nearly built.
    requested_course_ids = list(dict.fromkeys(data.get("courseIds") or []))
    if requested_course_ids:
        owned = await repo.find_courses_owned_by(user_id, requested_course_ids)
        missing = [cid for cid in requested_course_ids if cid not in owned]
        if missing:
            raise NotFoundError("Course", missing[0])

    skills: list[str] = []
    if not topics_to_plan:
        # Generate generic plan items from goal description via LLM
        topics_to_plan, skills = await _generate_topics_from_goal(
            title, goal_description, user_id=user_id
        )

    # Calculate available days
    now = datetime.now(UTC)
    days_available = max(1, (deadline - now).days)

    # Adaptive scheduling is what the Plus tier is sold on, and until now nothing
    # branched on `is_adaptive` — a Plus plan was byte-for-byte a Free plan. It
    # needs the learner's own topic rows to rank them by need, so it applies to
    # preparation-scoped plans; a goal-only plan has no measured topics to rank.
    strategy = prep_plan_adaptive.STRATEGY_EVEN
    all_items: list[dict[str, Any]] = []
    if is_adaptive and prep_topic_rows:
        scheduled = await prep_plan_adaptive.load_and_schedule(
            user_id=user_id,
            topics=prep_topic_rows,
            days_available=days_available,
            start=now,
            max_daily_minutes=max_daily_minutes,
            target_mastery=prep_target_mastery,
        )
        if scheduled:
            strategy = prep_plan_adaptive.STRATEGY_ADAPTIVE
            all_items = [
                {
                    "title": item.title,
                    "description": item.description,
                    "scheduledDate": item.scheduled_date,
                    "estimatedMinutes": item.estimated_minutes,
                    "type": item.item_type,
                    "topicId": item.topic_id,
                    "prepTopicId": item.prep_topic_id,
                }
                for item in scheduled
            ]

    if not all_items:
        # The even walk, unchanged. This is what Free gets, and what a goal-only
        # plan gets whether or not the learner is on Plus.
        plan_items = _distribute_items(topics_to_plan, days_available, now, max_daily_minutes)
        review_items = _add_review_items(plan_items, days_available, now)
        all_items = sorted(plan_items + review_items, key=lambda x: x["scheduledDate"])

    # Create plan
    plan = await repo.create_study_plan(
        {
            "userId": user_id,
            "title": title,
            "goalDescription": goal_description,
            "deadline": deadline,
            "prepId": prep_id,
            "status": "ACTIVE",
            # The learner's stated weekly intent, from the create wizard. Left null
            # when they set none, so a surface shows minutes planned without a target
            # rather than inventing one.
            "weeklyGoalMinutes": data.get("weeklyGoalMinutes"),
            # The wizard's toggles, stored because they are acted on: see
            # `set_item_status` for review cards and the daily check-in task.
            "generateReviewCards": bool(data.get("generateReviewCards")),
            "weeklyCheckIn": bool(data.get("weeklyCheckIn")),
            # Empty rather than null-vs-missing games: a prep-scoped plan derives its
            # items from topics and names no skills, and an empty list says that.
            "skills": skills or None,
            "totalItems": len(all_items),
            "completedItems": 0,
            # Which scheduler produced this plan. Recorded so "adaptive" is a
            # checkable property of a stored row rather than a claim in a docstring
            # — which is exactly how it went unnoticed that nothing branched on
            # `is_adaptive` for as long as it did.
            "strategy": strategy,
        }
    )

    # Create plan items
    for item in all_items:
        await repo.create_plan_item(
            {
                "planId": plan.id,
                "title": item["title"],
                "description": item.get("description"),
                "scheduledDate": item["scheduledDate"],
                "estimatedMinutes": item.get("estimatedMinutes", 30),
                "itemType": item.get("type", "STUDY"),
                "phase": item.get("phase"),
                "topicId": item.get("topicId"),
                "prepTopicId": item.get("prepTopicId"),
                "status": "PENDING",
            }
        )

    if requested_course_ids:
        await repo.link_plan_courses(plan.id, requested_course_ids)

    # Counts come from the rows that were actually written, not from the length of the
    # list we meant to write. The two can differ if an item insert fails.
    await repo.recount_plan_progress(plan.id)
    return await _detail_by_id(plan.id, user_id)


#: Statuses a plan may be set to by a learner. `SUPERSEDED` is deliberately absent: it
#: is written by plan regeneration, not chosen.
SETTABLE_PLAN_STATUSES = frozenset({"ACTIVE", "PAUSED", "COMPLETED"})

#: Statuses an item may be set to.
SETTABLE_ITEM_STATUSES = frozenset({"PENDING", "COMPLETED", "SKIPPED"})


async def list_plans(*, user_id: str) -> list[Any]:
    """
    List active study plans.

    Req 7.6: Return with completion %, today's tasks, days remaining.

    Kept for the callers that want active plans with their items loaded — the home and
    dashboard compositions. Paged surfaces use `list_plans_page`, which omits items.
    """
    return await repo.list_active_plans(user_id)


async def list_plans_page(
    *,
    user_id: str,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """A page of plans without their items, for a plan library view.

    Items are omitted because the card only needs the counts, which live on the plan.
    Embedding them meant an "all plans" page loaded every item of every plan.
    """
    return await repo.list_plans_paginated(
        user_id,
        status=status,
        search=search,
        skip=(page - 1) * page_size,
        take=page_size,
    )


async def _detail(plan: Any, user_id: str) -> dict[str, Any]:
    """Compose the detail payload: the plan, its items, its links and its files.

    Built as a mapping rather than returned as the ORM row, because two of the fields do
    not exist on it. A linked course's title lives on `Course`, and reading it through a
    lazy relationship outside the session that loaded it raises — so both are fetched
    explicitly. Every route that returns `StudyPlanResponse` goes through here; letting
    one return the bare row would serialize empty lists for a plan that has links, which
    reads as "no courses linked" rather than "not loaded".
    """
    linked_courses, materials = await asyncio.gather(
        repo.list_plan_courses(plan.id),
        repo.list_plan_materials(plan.id),
    )
    return {
        "id": plan.id,
        "userId": plan.user_id,
        "title": plan.title,
        "goalDescription": plan.goal_description,
        "deadline": plan.deadline,
        "prepId": plan.prep_id,
        "status": plan.status,
        "strategy": plan.strategy,
        "weeklyGoalMinutes": plan.weekly_goal_minutes,
        "skills": plan.skills,
        "generateReviewCards": plan.generate_review_cards,
        "weeklyCheckIn": plan.weekly_check_in,
        "reviewDeckId": plan.review_deck_id,
        "lastCheckInAt": plan.last_check_in_at,
        "totalItems": plan.total_items,
        "completedItems": plan.completed_items,
        "items": plan.items,
        "linkedCourses": [
            {
                "courseId": row["course_id"],
                "title": row["title"],
                "difficulty": row["difficulty"],
                "linkedAt": row["linked_at"],
            }
            for row in linked_courses
        ],
        "materials": materials,
        "createdAt": plan.created_at,
        "updatedAt": plan.updated_at,
    }


async def _detail_by_id(plan_id: str, user_id: str) -> dict[str, Any]:
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)
    return await _detail(plan, user_id)


async def get_plan(*, user_id: str, plan_id: str) -> Any:
    """Get a study plan with its items, linked courses and reference files."""
    return await _detail_by_id(plan_id, user_id)


async def get_plan_metrics(*, user_id: str, plan_id: str) -> dict[str, Any]:
    """Plan-scoped progress figures, for the detail page's metrics panel.

    Everything here is about this plan and derived from its items. The panel it feeds
    also showed a "retention" figure, which is deliberately absent: retention is
    flashcard recall, a different domain, and a plan with no flashcards has none — see
    the open decision in the integration plan. Reporting library-wide recall under a
    plan heading would be a real number answering a question nobody asked.
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    from src.shared.time.learner_timezone import resolve_learner_timezone

    metrics = await repo.get_plan_metrics(plan_id)
    learner_timezone = await resolve_learner_timezone(user_id)
    local_today = to_learner_local(datetime.now(UTC), learner_timezone).date()

    return {
        "completedMinutes": metrics["completed_minutes"],
        "plannedMinutes": metrics["planned_minutes"],
        "practiceCompleted": metrics["practice_completed"],
        "skippedItems": metrics["skipped_items"],
        "currentStreakDays": _streak_from_dates(set(metrics["active_dates"]), local_today),
        "activeDays": len(metrics["active_dates"]),
    }


def _streak_from_dates(active_dates: set[date], today: date) -> int:
    """Consecutive days ending today, or ending yesterday if today is unused.

    Same rule as the flashcard streak, and the same reason: a learner who worked six
    days straight and has not started this morning is on a six-day run, not a broken
    one. Duplicated deliberately rather than shared — this counts completed plan items
    and that counts graded cards, and a future change to one should not silently move
    the other.
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


def _as_utc(value: Any) -> Any:
    """Read a naive datetime as UTC, leave an aware one alone, pass anything else through.

    Every datetime column here is ``timestamptz`` and every calculation subtracts one
    instant from another, but a client may send `2026-09-01T00:00:00` with no offset and
    Pydantic parses that to a naive value. Mixing the two is not a cosmetic problem:
    subtracting a naive datetime from an aware one raises, so a deadline without an
    offset made `_redistribute_plan` fail with a `TypeError` — a `500` on a request that
    looked valid. Naive input is read as UTC, matching how these columns are written.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def update_plan(*, user_id: str, plan_id: str, data: dict[str, Any]) -> Any:
    """Rename a plan, restate its goal, move its deadline, or pause and resume it.

    Moving the deadline redistributes the pending items, by reusing the same
    redistribution the completion path uses. Leaving the dates alone would produce a
    plan whose items sit past its own deadline — a schedule that contradicts the date
    printed above it.
    """
    if "status" in data and data["status"] not in SETTABLE_PLAN_STATUSES:
        raise ValueError(f"Unsupported plan status: {data['status']}")

    data = {key: _as_utc(value) for key, value in data.items()}

    existing = await repo.get_study_plan(plan_id, user_id)
    if not existing:
        raise NotFoundError("StudyPlan", plan_id)

    deadline_changed = "deadline" in data and data["deadline"] != existing.deadline

    plan = await repo.update_study_plan(plan_id, user_id, data)
    if plan is None:
        raise NotFoundError("StudyPlan", plan_id)

    if deadline_changed:
        await _redistribute_plan(plan_id, user_id)

    return await _detail_by_id(plan_id, user_id)


async def link_courses(*, user_id: str, plan_id: str, course_ids: list[str]) -> Any:
    """Link courses to a plan, ignoring any already linked.

    Ownership of each course is checked before writing. The foreign key only proves a
    course exists, so without this a learner could attach someone else's course and read
    its title off their own detail page — the same hole that let flashcards be filed into
    another learner's deck.
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    requested = list(dict.fromkeys(course_ids))
    if requested:
        owned = await repo.find_courses_owned_by(user_id, requested)
        missing = [cid for cid in requested if cid not in owned]
        if missing:
            raise NotFoundError("Course", missing[0])
        await repo.link_plan_courses(plan_id, requested)

    return await _detail_by_id(plan_id, user_id)


async def unlink_course(*, user_id: str, plan_id: str, course_id: str) -> Any:
    """Remove a course link. The course itself is untouched."""
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)
    if not await repo.unlink_plan_course(plan_id, course_id):
        raise NotFoundError("StudyPlanCourse", course_id)
    return await _detail_by_id(plan_id, user_id)


async def add_material(*, user_id: str, plan_id: str, file: Any) -> Any:
    """Store a reference file against a plan.

    Uploaded through the shared storage service, the same path notes and generated
    documents use, rather than a second upload mechanism. The row is written only after
    the upload succeeds, so a failed upload leaves no material pointing at a URL that
    holds nothing.
    """
    from src.shared.infrastructure.storage import StorageError, storage_service

    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    try:
        # Scoped by learner and plan, so two plans can hold files of the same name and
        # one learner's upload cannot overwrite another's.
        stored = await storage_service.upload_upload_file(
            file, path_prefix=f"study-plans/{user_id}/{plan_id}"
        )
    except StorageError as error:
        raise ValueError(f"Upload failed: {error}") from error

    await repo.create_plan_material(
        {
            "planId": plan_id,
            "filename": stored["filename"],
            "url": stored["url"],
            "fileType": getattr(file, "content_type", None),
            "size": stored.get("size"),
        }
    )
    return await _detail_by_id(plan_id, user_id)


async def delete_material(*, user_id: str, plan_id: str, material_id: str) -> Any:
    """Remove a reference file, from storage as well as from the plan.

    The stored object is deleted first. If that fails the row stays, because a row
    pointing at a file that still exists is recoverable, while a deleted row pointing at
    an orphaned object leaves something nobody can find or clean up.
    """
    from src.shared.infrastructure.storage import storage_service

    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    material = await repo.get_plan_material(material_id, plan_id=plan_id)
    if material is None:
        raise NotFoundError("StudyPlanMaterial", material_id)

    await storage_service.delete(material.url)
    await repo.delete_plan_material(material_id, plan_id=plan_id)
    return await _detail_by_id(plan_id, user_id)


async def delete_plan(*, user_id: str, plan_id: str) -> bool:
    """Delete a plan and its items.

    Unlike a flashcard deck, a plan item is not independently authored content — it is a
    scheduled slot with no meaning outside its plan — so this cascades rather than
    detaching.
    """
    return await repo.delete_study_plan(plan_id, user_id)


async def add_item(*, user_id: str, plan_id: str, data: dict[str, Any]) -> Any:
    """Add an item to a plan by hand.

    Generated plans are a starting point; a learner who knows they need an extra session
    had no way to add one, which meant editing the plan meant abandoning it.
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    await repo.create_plan_item(
        {
            "planId": plan_id,
            "title": data["title"],
            "description": data.get("description"),
            "scheduledDate": _as_utc(data["scheduledDate"]),
            "estimatedMinutes": data.get("estimatedMinutes", 30),
            "itemType": data.get("itemType", "STUDY"),
            "phase": data.get("phase"),
            "status": "PENDING",
        }
    )
    await repo.recount_plan_progress(plan_id)
    return await _detail_by_id(plan_id, user_id)


async def update_item(*, user_id: str, plan_id: str, item_id: str, data: dict[str, Any]) -> Any:
    """Reschedule, retitle, resize, regroup, or restatus one item."""
    if "status" in data and data["status"] not in SETTABLE_ITEM_STATUSES:
        raise ValueError(f"Unsupported plan item status: {data['status']}")

    data = {key: _as_utc(value) for key, value in data.items()}

    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    if "status" in data:
        # Status changes go through the one path that also clears `completedAt` and
        # recounts, so a status set here cannot diverge from one set by the complete
        # route.
        status = data.pop("status")
        if data:
            updated = await repo.update_plan_item(item_id, data, plan_id=plan_id)
            if updated is None:
                raise NotFoundError("StudyPlanItem", item_id)
        return await set_item_status(
            user_id=user_id, plan_id=plan_id, item_id=item_id, status=status
        )

    updated = await repo.update_plan_item(item_id, data, plan_id=plan_id)
    if updated is None:
        raise NotFoundError("StudyPlanItem", item_id)
    return await _detail_by_id(plan_id, user_id)


async def delete_item(*, user_id: str, plan_id: str, item_id: str) -> Any:
    """Remove an item from a plan, then recount so progress reflects what is left."""
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    if not await repo.delete_plan_item(item_id, plan_id=plan_id):
        raise NotFoundError("StudyPlanItem", item_id)

    await repo.recount_plan_progress(plan_id)
    return await _detail_by_id(plan_id, user_id)


async def list_items_due_today(*, user_id: str) -> list[dict[str, Any]]:
    """Pending items due today or earlier, across every active plan.

    What a "today" panel needs and what no endpoint provided: plans had to be fetched
    one at a time and their items filtered client-side. Overdue items are included
    rather than shown separately, because work that slipped is work waiting today; the
    caller can tell them apart from `scheduledDate`.

    "Today" ends at the end of the learner's own day, not UTC's.
    """
    from src.shared.time.learner_timezone import resolve_learner_timezone, to_learner_local

    learner_timezone = await resolve_learner_timezone(user_id)
    local_now = to_learner_local(datetime.now(UTC), learner_timezone)
    end_of_local_day = local_now.replace(hour=23, minute=59, second=59, microsecond=999999)

    rows = await repo.list_items_due_by(user_id, until=end_of_local_day.astimezone(UTC))
    return [
        {
            "item": item,
            "planId": plan.id,
            "planTitle": plan.title,
            "planDeadline": plan.deadline,
        }
        for item, plan in rows
    ]


async def complete_item(*, user_id: str, plan_id: str, item_id: str) -> Any:
    """
    Mark a plan item as completed.

    Req 7.4: Mark done and adjust if ahead/behind schedule.
    """
    return await set_item_status(
        user_id=user_id, plan_id=plan_id, item_id=item_id, status="COMPLETED"
    )


async def uncomplete_item(*, user_id: str, plan_id: str, item_id: str) -> Any:
    """Return an item to pending — the inverse of completing it.

    A learner who ticks the wrong task had no way back, and the only recovery was to
    complete the right one too, which left the plan permanently overstating progress.
    """
    return await set_item_status(
        user_id=user_id, plan_id=plan_id, item_id=item_id, status="PENDING"
    )


async def set_item_status(*, user_id: str, plan_id: str, item_id: str, status: str) -> Any:
    """Move one item to ``PENDING``, ``COMPLETED`` or ``SKIPPED``, then recount.

    Two defects are closed here.

    **Ownership.** This used to verify that the *plan* belonged to the learner and then
    update the item by id alone, so an item id belonging to someone else's plan was
    written to. The item update is now scoped to the plan, and a mismatch is a `404`.

    **Counting.** ``completedItems`` used to be incremented on every call, without
    regard for what the item's status already was — so completing the same item twice
    counted twice and progress could pass 100%. It is now recomputed from the items,
    which also makes uncompleting and skipping expressible at all: those cannot be
    represented by an increment.
    """
    if status not in {"PENDING", "COMPLETED", "SKIPPED"}:
        raise ValueError(f"Unsupported plan item status: {status}")

    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        raise NotFoundError("StudyPlan", plan_id)

    now = datetime.now(UTC)
    updated = await repo.update_plan_item(
        item_id,
        {
            "status": status,
            # Cleared when leaving COMPLETED: a pending item with a completion
            # timestamp is a row that contradicts itself, and anything reading
            # `completedAt` to build a history would count it.
            "completedAt": now if status == "COMPLETED" else None,
        },
        plan_id=plan_id,
    )
    if updated is None:
        raise NotFoundError("StudyPlanItem", item_id)

    completed, total = await repo.recount_plan_progress(plan_id)

    # A plan is complete when nothing is left pending, not when a counter reaches a
    # number. Reopening an item therefore reopens the plan, which the increment-based
    # version could not express.
    if total > 0 and completed >= total:
        if plan.status == "ACTIVE":
            await repo.update_plan_status(plan_id, "COMPLETED")
    elif plan.status == "COMPLETED":
        await repo.update_plan_status(plan_id, "ACTIVE")

    if status == "COMPLETED":
        # The wizard's "Generate review cards" option, acted on. Queued rather than
        # awaited: generation is an LLM round trip, and a learner ticking a task off
        # should not wait seconds for it — nor see the completion fail if the model does.
        if plan.generate_review_cards:
            await _queue_review_cards(user_id=user_id, plan=plan, item=updated)

        from . import activity_feed_service

        await activity_feed_service.record(
            user_id=user_id,
            activity_type="plan_item_completed",
            title=f"Completed study task ({completed}/{total})",
            context={"source": "personal", "planId": plan_id, "itemId": item_id},
        )

        from . import milestone_service

        completion_pct = (completed / total * 100) if total else 0
        await milestone_service.check_milestones(
            user_id, {"plan_completion_percentage": completion_pct}
        )

        # Check if behind schedule and redistribute if needed (Req 7.5)
        items = await repo.list_plan_items(plan_id)
        pending_past_due = [i for i in items if i.status == "PENDING" and i.scheduled_date < now]
        if len(pending_past_due) > 2:
            await _redistribute_plan(plan_id, user_id)

    return await _detail_by_id(plan_id, user_id)


async def ensure_review_deck(*, user_id: str, plan: Any) -> str:
    """The deck this plan generates review cards into, created on first use.

    One deck per plan, reused after, so a plan's cards stay together instead of
    scattering through the library. The id is stored on the plan rather than found by
    title, because a learner may rename either and a title match would then either miss
    or collide.
    """
    from . import flashcard_service

    if plan.review_deck_id:
        deck = await repo.get_deck(plan.review_deck_id, user_id)
        if deck is not None:
            return deck.id
        # The deck was deleted. Fall through and make a new one rather than failing:
        # the learner asked for review cards, not for this particular deck.

    created = await flashcard_service.create_deck(
        user_id=user_id,
        data={
            "title": f"{plan.title} — review",
            "description": "Cards generated from tasks you completed in this study plan.",
        },
    )
    await repo.update_study_plan(plan.id, user_id, {"reviewDeckId": created["id"]})
    return created["id"]


async def _queue_review_cards(*, user_id: str, plan: Any, item: Any) -> None:
    """Hand review-card generation to a worker, or do it inline if there is no broker.

    The inline path is not a convenience: without it, a deployment with no Celery broker
    would accept the learner's "generate review cards" choice and quietly never act on
    it, which is the failure mode this whole exercise is removing. Slow is better than
    silent.
    """
    try:
        from src.domains.personal_learning.tasks.review_cards import (
            generate_plan_item_cards_task,
        )

        generate_plan_item_cards_task.delay(user_id, plan.id, item.id)
        return
    except Exception as error:  # pragma: no cover - broker/environmental
        logger.warning(
            "Could not queue review-card generation; generating inline",
            extra={"plan_id": plan.id, "item_id": item.id},
            exc_info=error,
        )

    try:
        await generate_review_cards_for_item(user_id=user_id, plan_id=plan.id, item_id=item.id)
    except Exception:
        # Never let this fail the completion. The learner finished the task; the cards
        # are a side effect they can trigger again by completing another.
        logger.exception(
            "Inline review-card generation failed",
            extra={"plan_id": plan.id, "item_id": item.id},
        )


async def generate_review_cards_for_item(
    *, user_id: str, plan_id: str, item_id: str
) -> list[Any]:
    """Generate review cards for one completed plan item. Entry point for the worker."""
    from . import flashcard_service

    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan or not plan.generate_review_cards:
        return []

    item = next((row for row in (plan.items or []) if row.id == item_id), None)
    if item is None or item.status != "COMPLETED":
        # Re-checked rather than trusted: the task may run after the learner reopened
        # the item, and generating cards for work no longer marked done would be acting
        # on a state that has passed.
        return []

    deck_id = await ensure_review_deck(user_id=user_id, plan=plan)
    return await flashcard_service.generate_from_plan_item(
        user_id=user_id,
        deck_id=deck_id,
        title=item.title,
        description=item.description,
        source_id=item.id,
    )


async def run_weekly_check_ins(*, before: datetime | None = None, limit: int = 500) -> int:
    """Create this week's check-in notification for each plan that asked for one.

    Backs the wizard's "Weekly Maigie check-in". Idempotent through
    `StudyPlan.lastCheckInAt`: a retry inside the same week finds nothing due, and a week
    the scheduler missed produces one notification rather than a backlog of them.

    The message carries the plan's real numbers, so it says something the learner can act
    on rather than a generic nudge.
    """
    from . import notification_service

    cutoff = before or (datetime.now(UTC) - timedelta(days=7))
    plans = await repo.list_plans_due_check_in(before=cutoff, limit=limit)

    sent = 0
    for plan in plans:
        completed = plan.completed_items or 0
        total = plan.total_items or 0
        remaining = max(0, total - completed)
        days_left = max(0, (plan.deadline - datetime.now(UTC)).days)

        notification = await notification_service.create_notification(
            user_id=plan.user_id,
            type="study_plan_check_in",
            title=f"Weekly check-in: {plan.title}",
            body=(
                f"{completed} of {total} tasks done, {remaining} to go, "
                f"{days_left} days until your deadline."
            ),
            priority=4,
            action_data={"planId": plan.id, "route": "study_plan"},
        )
        # Recorded even when the notification was suppressed by quiet hours or the daily
        # limit. Otherwise the plan stays "due" and retries every run, turning a
        # suppressed notification into a queue that all arrives at once.
        await repo.update_study_plan(
            plan.id, plan.user_id, {"lastCheckInAt": datetime.now(UTC)}
        )
        if notification is not None:
            sent += 1

    return sent


async def _redistribute_plan(plan_id: str, user_id: str) -> None:
    """
    Redistribute remaining plan items when learner is behind schedule.

    Req 7.5: Redistribute within deadline respecting sustainable session lengths.
    Ensures no single day exceeds max_daily_minutes (avg_session * 1.5 or 120 min).
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        return

    now = datetime.now(UTC)
    deadline = plan.deadline
    days_remaining = max(1, (deadline - now).days)

    # Get behaviour profile for sustainable session limit
    profile = await repo.get_profile_by_user(user_id)
    avg_session_minutes = 60
    if profile and profile.avg_session_minutes:
        avg_session_minutes = profile.avg_session_minutes
    max_daily_minutes = min(avg_session_minutes * 1.5, 120)

    # Get pending items
    items = await repo.list_plan_items(plan_id)
    pending_items = [i for i in items if i.status == "PENDING"]

    if not pending_items:
        return

    # Redistribute respecting max_daily_minutes per day
    day_index = 0
    daily_minutes_used = 0.0

    for item in pending_items:
        item_minutes = getattr(item, "estimated_minutes", 30) or 30

        # If adding this item would exceed the daily limit, move to next day
        if daily_minutes_used + item_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Cap at deadline — if we run out of days, pack remaining into last day
        actual_day = min(day_index, days_remaining - 1)
        new_date = now + timedelta(days=actual_day + 1)
        await repo.update_plan_item(item.id, {"scheduledDate": new_date}, plan_id=plan_id)

        daily_minutes_used += item_minutes

        # If this single item fills the day, advance
        if daily_minutes_used >= max_daily_minutes:
            day_index += 1
            daily_minutes_used = 0.0


def _distribute_items(
    topics: list[dict[str, Any]],
    days_available: int,
    start: datetime,
    max_daily_minutes: float,
) -> list[dict[str, Any]]:
    """Distribute study items across available days respecting daily limit."""
    items: list[dict[str, Any]] = []
    day_index = 0
    daily_minutes_used = 0.0

    for topic in topics:
        est_minutes = topic.get("estimatedMinutes", 30)

        # Check if adding this would exceed daily limit
        if daily_minutes_used + est_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Wrap around if we exceed available days.
        # Start on day 0 (today) so a fresh plan gives the learner
        # something to do the moment it's created.
        actual_day = day_index % days_available
        scheduled = start + timedelta(days=actual_day)

        items.append(
            {
                "title": topic["title"],
                "description": topic.get("description"),
                "scheduledDate": scheduled,
                "estimatedMinutes": est_minutes,
                "type": topic.get("type", "STUDY"),
                "topicId": topic.get("topicId"),
                "prepTopicId": topic.get("prepTopicId"),
            }
        )

        daily_minutes_used += est_minutes
        if daily_minutes_used >= max_daily_minutes:
            day_index += 1
            daily_minutes_used = 0.0

    return items


def _add_review_items(
    plan_items: list[dict[str, Any]],
    days_available: int,
    start: datetime,
) -> list[dict[str, Any]]:
    """Add spaced repetition review items (every 3-4 days after first study)."""
    reviews: list[dict[str, Any]] = []
    # Review first third of topics for spaced repetition
    items_to_review = plan_items[: len(plan_items) // 3]

    for item in items_to_review:
        # Schedule review 3 days after initial study
        study_date = item["scheduledDate"]
        review_date = study_date + timedelta(days=3)

        # Don't schedule reviews past the plan end
        plan_end = start + timedelta(days=days_available)
        if review_date <= plan_end:
            reviews.append(
                {
                    "title": f"Review: {item['title']}",
                    "scheduledDate": review_date,
                    "estimatedMinutes": 15,
                    "type": "REVIEW",
                    "topicId": item.get("topicId"),
                    "prepTopicId": item.get("prepTopicId"),
                }
            )

    return reviews


async def _generate_topics_from_goal(
    title: str, goal_description: str | None, *, user_id: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Generate study topics from a goal description using AI.

    Also asks for a ``phase`` per topic and a list of skills the plan builds, because
    both are things only the generator knows. A phase is the grouping the plan is drawn
    in terms of, and inferring one afterwards from topic titles would be guessing at
    structure the model already had in mind. Skills are the same: derivable from the
    goal, not from the items.

    Returns ``(topics, skills)``. Either can be empty, and the caller stores nothing
    rather than inventing a stand-in.
    """
    from .llm_resilient import generate_content_json

    prompt = (
        "Break this learning goal into a study plan.\n"
        f"Goal: {title}\n"
        f"Description: {goal_description or ''}\n\n"
        "Return ONLY a JSON object with two keys:\n"
        '  "topics": an array of objects with "title", "estimatedMinutes" (15-90), and\n'
        '            "phase" — a short grouping label shared by consecutive topics that\n'
        "            belong together, e.g. \"Foundations\", \"Core patterns\".\n"
        '  "skills": an array of 3-6 short skill names this plan builds.\n'
        "Generate 5-15 topics, ordered so phases progress from basics to application."
    )

    try:
        generated = await generate_content_json(
            prompt, max_tokens=2000, fallback=None, user_id=user_id
        )
        # Tolerate the model returning a bare array, which is what the previous prompt
        # asked for and what it still sometimes produces.
        if isinstance(generated, list):
            raw_topics, raw_skills = generated, []
        else:
            raw_topics = generated.get("topics") or []
            raw_skills = generated.get("skills") or []

        topics = [
            {
                "title": t["title"],
                "estimatedMinutes": t.get("estimatedMinutes", 30),
                "phase": t.get("phase") or None,
                "type": "STUDY",
            }
            for t in raw_topics
            if isinstance(t, dict) and t.get("title")
        ]
        skills = [s for s in raw_skills if isinstance(s, str) and s.strip()][:6]
        if topics:
            return topics, skills
        logger.warning("Goal breakdown returned no usable topics")
    except Exception as e:
        logger.warning(f"Failed to generate topics from goal: {e}")

    # Fallback: a single topic from the title, with no phase and no skills. Both are
    # left empty rather than filled with the title, which would present a placeholder
    # as a structure the learner can act on.
    return [{"title": title, "estimatedMinutes": 60, "phase": None, "type": "STUDY"}], []
