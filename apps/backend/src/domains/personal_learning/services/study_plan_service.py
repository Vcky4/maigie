"""
Study Plan service — AI-generated day-by-day study plans.

Distributes topics across available days, respects behaviour patterns,
interleaves spaced repetition reviews, and adapts when learners fall behind.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from src.shared.exceptions import NotFoundError, ValidationError
from src.shared.time.learner_timezone import to_learner_local

from .. import plan_shapes
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

    # The rhythm the create wizard asks for in steps 1 and 2. Until migration 023 all four
    # were collected and discarded, so a learner who chose "35 minutes, 5x a week,
    # Mon/Wed/Fri/Sat" got a plan sized from their observed behaviour and scheduled on
    # consecutive days.
    sessions_per_week = data.get("sessionsPerWeek")
    session_minutes = data.get("sessionMinutes")
    preferred_days = data.get("preferredDays")

    # An unknown shape is refused rather than stored and ignored. The value is not a label:
    # its phases are the structure the generator is told to follow, so accepting an id with
    # no catalogue entry would build an ungrouped plan while the wizard had just shown the
    # learner a four-phase roadmap.
    shape = data.get("shape")
    if shape is not None and shape not in plan_shapes.SHAPE_IDS:
        raise ValidationError(
            "Unknown plan shape",
            detail=f"'{shape}' is not one of: {', '.join(sorted(plan_shapes.SHAPE_IDS))}",
        )

    # Derived here rather than trusted from the client, so the printed weekly goal and the
    # printed session design cannot disagree. An explicitly supplied goal still wins when
    # the pace was not given — a plan can state a weekly target without describing how it
    # is broken up.
    weekly_goal_minutes = data.get("weeklyGoalMinutes")
    if sessions_per_week and session_minutes:
        weekly_goal_minutes = int(sessions_per_week) * int(session_minutes)

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

    if session_minutes:
        # The learner typed a session length in the wizard. See `_daily_minute_budget` for
        # why this is taken at face value rather than clamped by observed behaviour.
        max_daily_minutes = float(session_minutes)
    elif prep_pace:
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
            title,
            goal_description,
            user_id=user_id,
            shape=shape,
            session_minutes=session_minutes,
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
        plan_items = _distribute_items(
            topics_to_plan,
            days_available,
            now,
            max_daily_minutes,
            preferred_days=preferred_days,
        )
        review_items = _add_review_items(
            plan_items, days_available, now, preferred_days=preferred_days
        )
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
            "weeklyGoalMinutes": weekly_goal_minutes,
            # The rhythm itself, kept alongside its product so the plan can be described
            # the way the learner chose it — "35 min, 5x week" — and so a later
            # redistribution schedules onto the same days as the first one.
            "sessionsPerWeek": sessions_per_week,
            "sessionMinutes": session_minutes,
            "preferredDays": preferred_days or None,
            "shape": shape,
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
) -> tuple[list[dict[str, Any]], int]:
    """A page of plans without their items, for a plan library view.

    Items are omitted because the card only needs the counts, which live on the plan.
    Embedding them meant an "all plans" page loaded every item of every plan.

    The card also names the phase in progress and the next thing to do, and both come from
    the items. They are added here as two grouped queries over the page's plans — a handful
    of rows each — rather than by loading the items after all.
    """
    plans, total = await repo.list_plans_paginated(
        user_id,
        status=status,
        search=search,
        skip=(page - 1) * page_size,
        take=page_size,
    )
    if not plans:
        return [], total

    plan_ids = [plan.id for plan in plans]
    phases_by_plan, next_items = await asyncio.gather(
        repo.summarise_plan_phases(plan_ids),
        repo.next_pending_items(plan_ids),
    )

    summaries: list[dict[str, Any]] = []
    for plan in plans:
        phases = phases_by_plan.get(plan.id, [])
        next_item = next_items.get(plan.id)
        summaries.append(
            {
                **_summary_columns(plan),
                "currentPhase": _current_phase(phases, next_item),
                "totalPhases": len(phases),
                "nextItem": next_item,
            }
        )
    return summaries, total


def _current_phase(phases: list[dict[str, Any]], next_item: Any | None) -> dict[str, Any] | None:
    """The phase a plan is in.

    The phase holding the next pending item, because that is where the work is. With
    nothing pending the plan is finished or entirely skipped, and the last phase is the one
    it ended in — reporting the first would say a completed plan is at its beginning.

    Matched by label rather than by position: an item's phase is the only link it has to
    one, and positions are assigned here from the same rows, so comparing them would be
    comparing a derived value to itself.

    An item can be pending and carry no phase, on a plan whose other items do — the label is
    nullable per item. The fallback is then the earliest phase with work left in it, which is
    what "current" means anyway and does not depend on any one item.
    """
    if not phases:
        return None
    label = getattr(next_item, "phase", None) if next_item else None
    if label:
        matched = next((phase for phase in phases if phase["label"] == label), None)
        if matched:
            return matched
    unfinished = next(
        (phase for phase in phases if phase["completed_items"] < phase["total_items"]), None
    )
    return unfinished or phases[-1]


def _summary_columns(plan: Any) -> dict[str, Any]:
    """The plan's own columns, as the summary response names them.

    Spelled out rather than passing the ORM row through, because the two derived fields
    have to be added alongside them and a mapping cannot be part row and part dict.
    """
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
        "sessionsPerWeek": plan.sessions_per_week,
        "sessionMinutes": plan.session_minutes,
        "preferredDays": plan.preferred_days,
        "shape": plan.shape,
        "skills": plan.skills,
        "generateReviewCards": plan.generate_review_cards,
        "weeklyCheckIn": plan.weekly_check_in,
        "reviewDeckId": plan.review_deck_id,
        "lastCheckInAt": plan.last_check_in_at,
        "totalItems": plan.total_items,
        "completedItems": plan.completed_items,
        "createdAt": plan.created_at,
        "updatedAt": plan.updated_at,
    }


async def _detail(plan: Any, user_id: str) -> dict[str, Any]:
    """Compose the detail payload: the plan, its items, its links and its files.

    Built as a mapping rather than returned as the ORM row, because two of the fields do
    not exist on it. A linked course's title lives on `Course`, and reading it through a
    lazy relationship outside the session that loaded it raises — so both are fetched
    explicitly. Every route that returns `StudyPlanResponse` goes through here; letting
    one return the bare row would serialize empty lists for a plan that has links, which
    reads as "no courses linked" rather than "not loaded".
    """
    linked_courses, materials, phases_by_plan, next_items = await asyncio.gather(
        repo.list_plan_courses(plan.id),
        repo.list_plan_materials(plan.id),
        # Derived through the same queries the list uses rather than recomputed from the
        # items loaded here. Two derivations of one figure eventually disagree, and this one
        # would disagree between the card and the page showing the same plan.
        repo.summarise_plan_phases([plan.id]),
        repo.next_pending_items([plan.id]),
    )
    phases = phases_by_plan.get(plan.id, [])
    next_item = next_items.get(plan.id)
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
        "sessionsPerWeek": plan.sessions_per_week,
        "sessionMinutes": plan.session_minutes,
        "preferredDays": plan.preferred_days,
        "shape": plan.shape,
        "skills": plan.skills,
        "generateReviewCards": plan.generate_review_cards,
        "weeklyCheckIn": plan.weekly_check_in,
        "reviewDeckId": plan.review_deck_id,
        "lastCheckInAt": plan.last_check_in_at,
        "totalItems": plan.total_items,
        "completedItems": plan.completed_items,
        # Populated here as well as on the list, so the detail response does not serialize a
        # null phase for a plan that has phases — which reads as "not grouped" rather than
        # "not loaded", the same trap as the linked courses above.
        "currentPhase": _current_phase(phases, next_item),
        "totalPhases": len(phases),
        "nextItem": next_item,
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


async def get_dashboard(*, user_id: str) -> dict[str, Any]:
    """Everything the plan library page shows above its grid, in one request.

    Composed rather than left to the client for the reason recorded on
    `StudyPlansDashboardResponse`: from the endpoints that already existed this page was six
    requests and still could not produce its weekly figure, because the per-plan metrics are
    all-time.

    The week runs from Monday in the learner's own timezone. When that timezone was never
    captured the boundaries fall back to UTC and `timezoneKnown` says so, rather than the
    response presenting a UTC week as the learner's week — `UserPreferences.timezone` is
    `NOT NULL` defaulting to `"UTC"`, so reading it without checking the source makes
    everyone look like they are in London.
    """
    from src.shared.time.learner_timezone import resolve_learner_timezone

    learner_timezone = await resolve_learner_timezone(user_id)
    now_local = to_learner_local(datetime.now(UTC), learner_timezone)
    week_start_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end_local = week_start_local + timedelta(days=7)
    # Back to instants for the query: the columns are `timestamptz` and comparing them to a
    # local wall clock would shift the window by the learner's offset.
    week_start = week_start_local.astimezone(UTC)
    week_end = week_end_local.astimezone(UTC)

    counts, weekly_minutes, due_today, active_page = await asyncio.gather(
        repo.count_plans_by_status(user_id),
        repo.completed_minutes_between(user_id, week_start, week_end),
        list_items_due_today(user_id=user_id),
        # The nearest deadline first, which is what `list_plans_paginated` already orders by,
        # so "featured" is a page of one rather than a sort done here over everything.
        list_plans_page(user_id=user_id, status="ACTIVE", page=1, page_size=1),
    )

    featured_summaries, _ = active_page
    featured = featured_summaries[0] if featured_summaries else None

    featured_streak = 0
    featured_week: list[Any] = []
    if featured:
        plan_id = featured["id"]
        metrics, featured_week = await asyncio.gather(
            repo.get_plan_metrics(plan_id),
            repo.list_plan_items_between(plan_id, week_start, week_end),
        )
        featured_streak = _streak_from_dates(set(metrics["active_dates"]), now_local.date())

    goal_total = counts.get("weeklyGoalTotal") or 0
    return {
        "weeklyMinutes": weekly_minutes,
        "weeklyGoalMinutes": goal_total or None,
        "tasksDue": len(due_today),
        "activeCount": counts.get("ACTIVE", 0),
        "pausedCount": counts.get("PAUSED", 0),
        "completedCount": counts.get("COMPLETED", 0),
        "featured": featured,
        "featuredStreakDays": featured_streak,
        "featuredWeek": featured_week,
        "timezoneKnown": learner_timezone.is_known,
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

    Changing the session length or the available days redistributes for the same reason.
    Both decide which day an item lands on, so a plan that kept its old dates would show a
    rhythm in its header that its own schedule contradicts. The weekly goal is left out of
    that: it is a target the learner is measured against, not an input to placement.
    """
    if "status" in data and data["status"] not in SETTABLE_PLAN_STATUSES:
        raise ValueError(f"Unsupported plan status: {data['status']}")

    data = {key: _as_utc(value) for key, value in data.items()}

    existing = await repo.get_study_plan(plan_id, user_id)
    if not existing:
        raise NotFoundError("StudyPlan", plan_id)

    # Kept in step with `weeklyGoalMinutes` the same way creation does it, so a learner who
    # changes their pace does not end up with a stated weekly total describing the old one.
    sessions = data.get("sessionsPerWeek", existing.sessions_per_week)
    minutes = data.get("sessionMinutes", existing.session_minutes)
    if ("sessionsPerWeek" in data or "sessionMinutes" in data) and sessions and minutes:
        data["weeklyGoalMinutes"] = int(sessions) * int(minutes)

    reschedules = (
        ("deadline", existing.deadline),
        ("sessionMinutes", existing.session_minutes),
        ("preferredDays", existing.preferred_days),
    )
    schedule_changed = any(key in data and data[key] != current for key, current in reschedules)

    plan = await repo.update_study_plan(plan_id, user_id, data)
    if plan is None:
        raise NotFoundError("StudyPlan", plan_id)

    if schedule_changed:
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
            # The plan, not the item: an item has no page of its own, so routing to it would be a
            # link to nowhere. `itemId` stays in the context for anything that wants to highlight it.
            entity_type="study_plan",
            entity_id=plan_id,
            context={"source": "personal", "planId": plan_id, "itemId": item_id},
        )

        from . import milestone_service

        completion_pct = (completed / total * 100) if total else 0
        await milestone_service.check_milestones(
            user_id, {"plan_completion_percentage": completion_pct}
        )

        # Check if behind schedule and redistribute if needed (Req 7.5). Not throttled by
        # `lastRedistributedAt`, unlike the background sweep: this is a response to something the
        # learner just did, and making them wait out a cooldown would look like the app ignoring them.
        items = await repo.list_plan_items(plan_id)
        pending_past_due = [i for i in items if i.status == "PENDING" and i.scheduled_date < now]
        if len(pending_past_due) > MAX_TOLERATED_PAST_DUE:
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


async def generate_review_cards_for_item(*, user_id: str, plan_id: str, item_id: str) -> list[Any]:
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
        await repo.update_study_plan(plan.id, plan.user_id, {"lastCheckInAt": datetime.now(UTC)})
        if notification is not None:
            sent += 1

    return sent


#: How many pending items may sit past their date before a plan counts as drifted.
#:
#: "More than two", which is the threshold the completion path has always used as a bare `> 2` with no
#: explanation. Named here so the background sweep and the interactive path cannot disagree about what
#: being behind means — a plan that is drifted when the learner completes something and not drifted
#: overnight would be two definitions of the same word.
MAX_TOLERATED_PAST_DUE = 2

#: How long a plan is left alone after a background repack, in days.
#:
#: Seven, matching `RetentionIntervention`'s `INTERVENTION_COOLDOWN_DAYS` and the weekly check-in's
#: window. The floor is set by what redistribution does rather than by taste: it rewrites the date of
#: every pending item, so running it more often than the learner's own rhythm turns their schedule into
#: something that is never the same twice. A learner who is genuinely stuck is stuck next week too, and
#: one repack a week is enough to keep the plan inside its deadline.
REDISTRIBUTION_COOLDOWN_DAYS = 7


async def redistribute_drifted_plans(
    *, now: datetime | None = None, limit: int = 500
) -> int:
    """Repack the plans of learners who have gone quiet. Returns the number of plans moved.

    **This is the gap.** `_redistribute_plan` could only ever be reached by a learner editing their
    schedule or completing an item, so the learners whose plans had drifted furthest — the ones
    completing nothing — were the only ones who never got redistributed. A fortnight of overdue items
    would sit there, and the plan's own progress figures would keep measuring against dates that had
    stopped meaning anything.

    Nothing here decides *whether* a plan has drifted or *where* items should go. The drift test is the
    same `MAX_TOLERATED_PAST_DUE` the completion path uses and the placement is the same
    `_redistribute_plan`, so a plan repacked overnight lands exactly where it would have landed had the
    learner opened the app and ticked something off. This function only makes that reachable.

    Idempotent through `StudyPlan.lastRedistributedAt`, and the stamp is written **even when nothing
    moved**. Counting only successful repacks would leave a plan permanently due — swept every night,
    moving nothing, notifying nobody — which is the trap `run_weekly_check_ins` documents for suppressed
    notifications and `mark_preparations_awaiting_review` for suppressed asks.

    One plan's failure does not end the run. `run_weekly_check_ins` has no such guard and one bad row
    aborts it; `check_declining_engagement` gets this right, and this follows that.
    """
    from . import notification_service

    moment = _as_utc(now) or datetime.now(UTC)
    plans = await repo.list_plans_with_drift(
        now=moment,
        min_past_due=MAX_TOLERATED_PAST_DUE,
        not_swept_since=moment - timedelta(days=REDISTRIBUTION_COOLDOWN_DAYS),
        limit=limit,
    )

    redistributed = 0
    for plan in plans:
        try:
            moved = await _redistribute_plan(plan.id, plan.user_id)
            # Stamped before the notification, and regardless of what it returned. A plan whose items
            # are all pinned to accepted calendar blocks moves nothing, and it must still go on
            # cooldown rather than being reconsidered every night forever.
            await repo.update_study_plan(
                plan.id, plan.user_id, {"lastRedistributedAt": datetime.now(UTC)}
            )
            if not moved:
                continue
            redistributed += 1

            # **Told, not done silently.** The learner did not ask for this, and a plan whose every
            # remaining date changed overnight with no word is the system rewriting their commitments
            # behind their back — the phase boundaries they accepted in the wizard move with it, since a
            # phase's week range is just the span of its items' dates. Delivery may still be dropped by
            # quiet hours or the daily cap; that is the notification path's own defect and the stamp
            # above does not depend on it.
            days_left = max(0, (_as_utc(plan.deadline) - moment).days)
            await notification_service.create_notification(
                user_id=plan.user_id,
                type="study_plan_redistributed",
                title=f"Rescheduled: {plan.title}",
                body=(
                    f"{moved} task{'s' if moved != 1 else ''} moved to fit the "
                    f"{days_left} days left before your deadline."
                ),
                priority=4,
                action_data={"planId": plan.id, "route": "study_plan"},
            )
        except Exception:
            logger.exception(
                "Failed to redistribute drifted study plan",
                extra={"plan_id": plan.id, "user_id": plan.user_id},
            )

    return redistributed


async def _redistribute_plan(plan_id: str, user_id: str) -> int:
    """
    Redistribute remaining plan items when learner is behind schedule.

    Req 7.5: Redistribute within deadline respecting sustainable session lengths.
    Ensures no single day exceeds max_daily_minutes (avg_session * 1.5 or 120 min).

    Returns the number of items moved, so a caller that did not ask on the learner's behalf can say what
    it did. It previously returned nothing, which is fine for a path the learner triggered and useless
    for a sweep that has to decide whether anything is worth reporting.

    **Items with an accepted calendar block are left where they are.** `StudyPlanItem.scheduleBlockId` is
    set when the learner accepted a suggested hour for that item, so a real `ScheduleBlock` sits on that
    day. Moving `scheduledDate` while leaving the block behind gives the learner a calendar entry on
    Tuesday and a plan item on Friday, and the day they turn up is the one in their calendar. This is a
    change to the existing behaviour of both learner-triggered paths, and it is the right way round: the
    same argument that stops redistribution rewriting the rhythm the learner chose stops it moving a time
    they explicitly accepted.
    """
    plan = await repo.get_study_plan(plan_id, user_id)
    if not plan:
        return 0

    now = datetime.now(UTC)
    deadline = plan.deadline
    days_remaining = max(1, (deadline - now).days)

    # The learner's own stated session length is the daily budget when they gave one;
    # otherwise fall back to what they have been observed to sustain. Same order of
    # preference as the first distribution, so a redistribution cannot quietly rewrite
    # the plan to a rhythm the learner never chose.
    max_daily_minutes = await _daily_minute_budget(user_id, plan.session_minutes)

    # Get pending items. Ones the learner has already given a time to are excluded rather than packed
    # around: they are not part of the flexible pool, and their minutes are committed on a day that is
    # not this walk's to allocate.
    items = await repo.list_plan_items(plan_id)
    pending_items = [
        i
        for i in items
        if i.status == "PENDING" and getattr(i, "schedule_block_id", None) is None
    ]

    if not pending_items:
        return 0

    # Redistribute respecting max_daily_minutes per day, onto the days the learner
    # studies. Starts tomorrow rather than today: redistribution runs in response to
    # something that just happened, and moving pending work onto the day already in
    # progress would put it behind before the learner saw it.
    day_index = 0
    daily_minutes_used = 0.0
    candidates = _available_dates(now + timedelta(days=1), days_remaining, plan.preferred_days)
    moved = 0

    for item in pending_items:
        item_minutes = getattr(item, "estimated_minutes", 30) or 30

        # If adding this item would exceed the daily limit, move to next day
        if daily_minutes_used + item_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Cap at the last available day — if we run out, pack the remainder into it
        # rather than scheduling past the deadline.
        new_date = candidates[min(day_index, len(candidates) - 1)]
        await repo.update_plan_item(item.id, {"scheduledDate": new_date}, plan_id=plan_id)
        moved += 1

        daily_minutes_used += item_minutes

        # If this single item fills the day, advance
        if daily_minutes_used >= max_daily_minutes:
            day_index += 1
            daily_minutes_used = 0.0

    return moved


#: ISO weekday numbers, 1 = Monday ... 7 = Sunday. Every day, which is what a plan whose
#: learner was never asked about availability gets.
_ALL_WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)


async def _daily_minute_budget(user_id: str, session_minutes: int | None) -> float:
    """How many minutes of work one day of this plan may hold.

    The learner's stated session length wins when they gave one, and is **not** clamped
    by observed behaviour. That differs from `prep_intent.daily_minute_budget`, which
    takes the smaller of intent and behaviour, and the difference is deliberate: a
    preparation's pace is a word — "Intensive" — that has to be interpreted into minutes,
    and behaviour is the honest way to interpret it. A session length is a number the
    learner typed in answer to "focused time per session". Overriding that with an
    inference would discard the answer while appearing to honour it.

    With no stated length this is the previous behaviour unchanged: one and a half average
    sessions, capped at two hours.
    """
    if session_minutes and session_minutes > 0:
        return float(session_minutes)
    profile = await repo.get_profile_by_user(user_id)
    avg_session_minutes = 60
    if profile and profile.avg_session_minutes:
        avg_session_minutes = profile.avg_session_minutes
    return min(avg_session_minutes * 1.5, 120)


def _normalise_preferred_days(preferred_days: Any) -> tuple[int, ...]:
    """The learner's available weekdays as a sorted tuple of ISO weekday numbers.

    Anything unusable — null, an empty list, a list of values outside 1-7 — becomes every
    day. The contract rejects an empty list, so reaching here with one means a plan
    predates the column or a caller bypassed the route, and refusing to schedule would
    turn that into a plan with no items rather than a plan with a forgotten preference.
    """
    if not preferred_days:
        return _ALL_WEEKDAYS
    days = sorted({int(d) for d in preferred_days if isinstance(d, int | float) and 1 <= d <= 7})
    return tuple(days) if days else _ALL_WEEKDAYS


def _available_dates(start: datetime, days_available: int, preferred_days: Any) -> list[datetime]:
    """The dates inside the window that the learner said they study on, in order.

    The two schedulers used to index days by offset from today — day 0, day 1, day 2 —
    which is why a learner who chose Monday, Wednesday, Friday and Saturday got work on
    the Tuesday. They now index into this list instead, so an excluded weekday is not a
    date either of them can produce.

    Falls back to the whole window when no preferred day falls inside it. A four-day
    deadline and a Saturday-only learner is a real combination, and honouring it exactly
    would mean a plan with nowhere to put its items; the deadline is the harder
    constraint, so availability yields to it rather than the reverse.
    """
    allowed = _normalise_preferred_days(preferred_days)
    window = [start + timedelta(days=offset) for offset in range(max(1, days_available))]
    if allowed == _ALL_WEEKDAYS:
        return window
    matching = [day for day in window if day.isoweekday() in allowed]
    return matching or window


def _distribute_items(
    topics: list[dict[str, Any]],
    days_available: int,
    start: datetime,
    max_daily_minutes: float,
    preferred_days: Any = None,
) -> list[dict[str, Any]]:
    """Distribute study items across the learner's available days, respecting the daily
    limit."""
    items: list[dict[str, Any]] = []
    day_index = 0
    daily_minutes_used = 0.0
    # Start on the first available day so a fresh plan gives the learner something to do
    # the moment it is created — which is today, unless today is a day they said they do
    # not study.
    candidates = _available_dates(start, days_available, preferred_days)

    for topic in topics:
        est_minutes = topic.get("estimatedMinutes", 30)

        # Check if adding this would exceed daily limit
        if daily_minutes_used + est_minutes > max_daily_minutes and daily_minutes_used > 0:
            day_index += 1
            daily_minutes_used = 0.0

        # Wrap around if we run out of available days rather than overflowing the
        # deadline, unchanged in behaviour from indexing the raw window.
        scheduled = candidates[day_index % len(candidates)]

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
    preferred_days: Any = None,
) -> list[dict[str, Any]]:
    """Add spaced repetition review items (every 3-4 days after first study)."""
    reviews: list[dict[str, Any]] = []
    # Review first third of topics for spaced repetition
    items_to_review = plan_items[: len(plan_items) // 3]
    candidates = _available_dates(start, days_available, preferred_days)

    for item in items_to_review:
        # Three days after the study item, then forward to the next day the learner
        # actually studies. Without the snap a review lands on an excluded weekday even
        # though every study item respects the preference, which is the more confusing
        # half-honoured version of the same bug.
        study_date = item["scheduledDate"]
        target = study_date + timedelta(days=3)
        review_date = next((day for day in candidates if day >= target), None)

        # Don't schedule reviews past the plan end
        plan_end = start + timedelta(days=days_available)
        if review_date is not None and review_date <= plan_end:
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


def _conform_phases(topics: list[dict[str, Any]], shape_phases: list[str]) -> list[dict[str, Any]]:
    """Make the topics' phase labels the ones the learner was shown.

    Asking for the labels in the prompt is not the same as getting them: a model that
    returns "Fundamentals" where the shape says "Close the foundations gap" leaves the plan
    grouped by headings the learner never saw, which is the defect this was meant to fix,
    only harder to spot.

    So a returned label is kept when it is one of the shape's, matched case- and
    space-insensitively, and otherwise every topic is relabelled by its position: the
    ordered topics are cut into as many contiguous runs as the shape has phases. Position
    is a real signal rather than a guess here, because both sequences are ordered the same
    way — the prompt asks for basics through to application, and that is how the shape's
    phases read.

    All-or-nothing per plan, not per topic. Mixing the model's labels with positional ones
    would produce more phases than the shape has, and a roadmap with five headings where
    the preview showed four is worse than one that is uniformly positional.

    No shape means no expectation to meet, and the labels are returned untouched.
    """
    if not shape_phases or not topics:
        return topics

    def canonical(value: Any) -> str:
        return " ".join(str(value or "").lower().split())

    allowed = {canonical(p): p for p in shape_phases}
    if all(canonical(t.get("phase")) in allowed for t in topics):
        for topic in topics:
            topic["phase"] = allowed[canonical(topic["phase"])]
        return topics

    count = len(shape_phases)
    for index, topic in enumerate(topics):
        # Cut into contiguous runs; integer maths keeps the last topic inside the last
        # phase rather than one past it.
        topic["phase"] = shape_phases[min(index * count // len(topics), count - 1)]
    return topics


async def _generate_topics_from_goal(
    title: str,
    goal_description: str | None,
    *,
    user_id: str | None = None,
    shape: str | None = None,
    session_minutes: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Generate study topics from a goal description using AI.

    Also asks for a ``phase`` per topic and a list of skills the plan builds, because
    both are things only the generator knows. A phase is the grouping the plan is drawn
    in terms of, and inferring one afterwards from topic titles would be guessing at
    structure the model already had in mind. Skills are the same: derivable from the
    goal, not from the items.

    ``shape`` names a path shape from the wizard's catalogue. Its phases are given to the
    model as the spine to fill rather than left to invent, because step 4 of the wizard
    shows the learner those exact phase titles before they accept the plan. Unknown or
    absent shape means the model chooses its own phases, which is what every plan did
    before shapes existed.

    ``session_minutes`` bounds each topic's estimate. Without it the model returns 15-90
    minute topics and the distributor then puts one 90-minute topic on a day the learner
    said was 20 minutes long — the plan still fits the deadline, but no single day is
    doable as described.

    Returns ``(topics, skills)``. Either can be empty, and the caller stores nothing
    rather than inventing a stand-in.
    """
    from ..plan_shapes import phase_titles
    from .llm_resilient import generate_content_json

    phases = phase_titles(shape)
    if phases:
        phase_instruction = (
            '  "phase": one of these exact labels, in this order, each used by at least\n'
            "            one consecutive run of topics: " + ", ".join(f'"{p}"' for p in phases)
        )
    else:
        phase_instruction = (
            '  "phase": a short grouping label shared by consecutive topics that belong\n'
            '            together, e.g. "Foundations", "Core patterns".'
        )

    # A ceiling, not a target: a topic may be shorter than one session, but a topic longer
    # than one cannot be done in the session the learner described.
    upper_minutes = max(15, min(90, session_minutes)) if session_minutes else 90

    prompt = (
        "Break this learning goal into a study plan.\n"
        f"Goal: {title}\n"
        f"Description: {goal_description or ''}\n\n"
        "Return ONLY a JSON object with two keys:\n"
        '  "topics": an array of objects with "title", "estimatedMinutes"\n'
        f"            (15-{upper_minutes}), and\n"
        f"{phase_instruction}\n"
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
            return _conform_phases(topics, phases), skills
        logger.warning("Goal breakdown returned no usable topics")
    except Exception as e:
        logger.warning(f"Failed to generate topics from goal: {e}")

    # Fallback: a single topic from the title, with no skills. Skills are left empty rather
    # than filled with the title, which would present a placeholder as a structure the
    # learner can act on.
    #
    # The phase is the shape's first label when there is a shape, and null otherwise. That
    # is not inventing structure: the learner chose that shape and its first phase is where
    # any plan following it starts. Leaving it null would render the one item outside the
    # roadmap the wizard had just shown them.
    fallback_minutes = max(15, min(60, session_minutes)) if session_minutes else 60
    return [
        {
            "title": title,
            "estimatedMinutes": fallback_minutes,
            "phase": phases[0] if phases else None,
            "type": "STUDY",
        }
    ], []
