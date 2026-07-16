"""
Handlers for tool calls (provider-agnostic).

Executes DB queries or calls action_service methods based on tool calls.
These handlers are registered with the skill registry and also accessible
directly via the legacy `handle_tool_call` dispatch function.

NOTE: The canonical tool definitions now live in src/services/skills/skill_*.py.
This module contains the handler implementations that skills reference.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.domains.intelligence.repository import intelligence_repo
from src.domains.knowledge.repository import knowledge_repo
from src.domains.progress.repository import progress_repo
from src.shared.database import get_session_factory
from src.services.action_service import action_service

logger = logging.getLogger(__name__)


async def handle_tool_call(
    tool_name: str,
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Route tool call to appropriate handler.

    Args:
        tool_name: Name of the tool to execute
        args: Tool arguments
        user_id: User ID
        context: Additional context (courseId, topicId, etc.)
        progress_callback: Optional async callback for progress updates
                          Signature: async def callback(progress: int, stage: str, message: str, **kwargs)
    """
    # Handlers that support progress callbacks
    handlers_with_progress = {"create_course", "create_study_plan"}

    handlers = {
        # Query handlers
        "get_user_courses": handle_get_user_courses,
        "get_user_goals": handle_get_user_goals,
        "get_user_schedule": handle_get_user_schedule,
        "get_user_notes": handle_get_user_notes,
        "get_user_resources": handle_get_user_resources,
        "get_my_profile": handle_get_my_profile,
        # Action handlers
        "create_course": handle_create_course,
        "create_note": handle_create_note,
        "create_goal": handle_create_goal,
        "create_schedule": handle_create_schedule,
        "check_schedule_conflicts": handle_check_schedule_conflicts,
        "recommend_resources": handle_recommend_resources,
        "retake_note": handle_retake_note,
        "add_summary_to_note": handle_add_summary_to_note,
        "add_tags_to_note": handle_add_tags_to_note,
        "complete_review": handle_complete_review,
        "update_course_outline": handle_update_course_outline,
        "delete_course": handle_delete_course,
        "save_user_fact": handle_save_user_fact,
        "generate_document": handle_generate_document,
        # Agentic handlers
        "create_study_plan": handle_create_study_plan,
        "get_learning_insights": handle_get_learning_insights,
        "get_pending_nudges": handle_get_pending_nudges,
        "email_user": handle_email_user,
        "complete_topic_and_continue": handle_complete_topic_and_continue,
        "study_show_visual": handle_study_show_visual,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}

    # Enrich args with context (IDs normally come from the client on the WebSocket message)
    if context:
        if "courseId" in context and "course_id" not in args:
            args["course_id"] = context["courseId"]
        if "topicId" in context and "topic_id" not in args:
            args["topic_id"] = context["topicId"]
        if "noteId" in context and "note_id" not in args:
            args["note_id"] = context["noteId"]
        if "reviewItemId" in context and "review_item_id" not in args:
            args["review_item_id"] = context["reviewItemId"]
        if "spaceId" in context and "space_id" not in args:
            args["space_id"] = context["spaceId"]

    try:
        # Pass progress_callback to handlers that support it
        if tool_name in handlers_with_progress and progress_callback:
            result = await handler(args, user_id, context, progress_callback=progress_callback)
        else:
            result = await handler(args, user_id, context)
        return result
    except Exception as e:
        logger.error(f"Tool execution error for {tool_name}: {e}", exc_info=True)
        return {
            "error": str(e),
            "error_type": type(e).__name__,
        }


# Query Handlers


async def handle_get_user_courses(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_user_courses tool call."""
    limit = args.get("limit", 20)
    if not isinstance(limit, int | float) or limit < 1 or limit > 100:
        limit = 20

    include_archived = args.get("include_archived", False)

    where: dict[str, Any] = {}
    if not include_archived:
        where["archived"] = False

    courses, _ = await knowledge_repo.list_courses(
        user_id, where=where, skip=0, take=int(limit)
    )

    # Format data
    courses_data = []
    for course in courses:
        total_topics = sum(len(m.topics) for m in course.modules)
        completed_topics = sum(sum(1 for t in m.topics if t.completed) for m in course.modules)
        progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0
        courses_data.append(
            {
                "courseId": course.id,
                "id": course.id,
                "title": course.title,
                "description": course.description or "",
                "progress": progress,
                "difficulty": course.difficulty,
                "completedTopics": completed_topics,
                "totalTopics": total_topics,
            }
        )

    return {
        "_component_type": "CourseListMessage",
        "_query_type": "courses",
        "courses": courses_data,
        "count": len(courses_data),
        "message": f"Found {len(courses_data)} course(s)",
    }


async def handle_get_user_goals(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_user_goals tool call."""
    status = args.get("status", "ACTIVE")
    if status not in ["ACTIVE", "COMPLETED", "ARCHIVED"]:
        status = "ACTIVE"

    limit = args.get("limit", 20)
    if not isinstance(limit, int | float) or limit < 1 or limit > 100:
        limit = 20

    course_id = args.get("course_id")

    where: dict[str, Any] = {"status": status}
    if course_id:
        where["courseId"] = course_id

    goals, _ = await progress_repo.list_goals(
        user_id, where=where, skip=0, take=int(limit), order={"createdAt": "desc"}
    )

    goals_data = []
    for goal in goals:
        goals_data.append(
            {
                "goalId": goal.id,
                "id": goal.id,
                "title": goal.title,
                "description": goal.description or "",
                "targetDate": goal.target_date.isoformat() if goal.target_date else None,
                "progress": goal.progress or 0,
                "status": goal.status,
                "courseId": goal.course_id,
                "topicId": goal.topic_id,
            }
        )

    return {
        "_component_type": "GoalListMessage",
        "_query_type": "goals",
        "goals": goals_data,
        "count": len(goals_data),
        "message": f"Found {len(goals_data)} goal(s)",
    }


async def handle_get_user_schedule(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_user_schedule tool call."""
    limit = args.get("limit", 50)
    if not isinstance(limit, int | float) or limit < 1 or limit > 200:
        limit = 50

    course_id = args.get("course_id")

    where: dict[str, Any] = {}
    if course_id:
        where["courseId"] = course_id

    blocks, _ = await progress_repo.list_blocks(
        user_id, where=where, skip=0, take=int(limit)
    )

    schedules_data = []
    for schedule in blocks:
        schedules_data.append(
            {
                "scheduleId": schedule.id,
                "id": schedule.id,
                "title": schedule.title,
                "startAt": schedule.start_at.isoformat() if schedule.start_at else None,
                "endAt": schedule.end_at.isoformat() if schedule.end_at else None,
                "description": schedule.description or "",
                "courseId": schedule.course_id,
                "topicId": schedule.topic_id,
                "goalId": schedule.goal_id,
                "reviewItemId": schedule.review_item_id,
            }
        )

    return {
        "_component_type": "ScheduleViewMessage",
        "_query_type": "schedule",
        "schedules": schedules_data,
        "count": len(schedules_data),
        "message": f"Found {len(schedules_data)} schedule block(s)",
    }


async def handle_get_user_notes(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_user_notes tool call."""
    limit = args.get("limit", 20)
    if not isinstance(limit, int | float) or limit < 1 or limit > 100:
        limit = 20

    # Notes not yet migrated to SQLAlchemy — query directly via session
    from sqlalchemy import select, text
    try:
        factory = get_session_factory()
        async with factory() as session:
            query = text(
                'SELECT id, title, content, summary, "courseId", "topicId", '
                '"createdAt", "updatedAt" FROM "Note" '
                'WHERE "userId" = :uid AND archived = false '
                'ORDER BY "updatedAt" DESC LIMIT :lim'
            )
            result = await session.execute(query, {"uid": user_id, "lim": int(limit)})
            rows = result.mappings().all()

        notes_data = [
            {
                "noteId": r["id"],
                "id": r["id"],
                "title": r["title"],
                "content": (r["content"] or "")[:200],
                "summary": r["summary"],
                "courseId": r["courseId"],
                "topicId": r["topicId"],
                "createdAt": r["createdAt"].isoformat() if r["createdAt"] else None,
                "updatedAt": r["updatedAt"].isoformat() if r["updatedAt"] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("handle_get_user_notes error: %s", e)
        notes_data = []

    return {
        "_component_type": "NoteListMessage",
        "_query_type": "notes",
        "notes": notes_data,
        "count": len(notes_data),
        "message": f"Found {len(notes_data)} note(s)",
    }


async def handle_get_user_resources(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_user_resources tool call."""
    limit = args.get("limit", 20)
    if not isinstance(limit, int | float) or limit < 1 or limit > 100:
        limit = 20

    where: dict[str, Any] = {}
    topic_id = args.get("topic_id")
    course_id = args.get("course_id")
    resource_type = args.get("resource_type")
    if topic_id:
        where["topicId"] = topic_id
    if course_id:
        where["courseId"] = course_id
    if resource_type:
        where["type"] = resource_type

    resources, _ = await knowledge_repo.list_resources(
        where={"userId": user_id, **where}, skip=0, take=int(limit)
    )

    resources_data = []
    for resource in resources:
        resources_data.append(
            {
                "resourceId": resource.id,
                "id": resource.id,
                "title": resource.title,
                "url": resource.url or "",
                "description": resource.description or "",
                "type": resource.type,
                "courseId": resource.course_id,
                "topicId": resource.topic_id,
            }
        )

    return {
        "_component_type": "ResourceListMessage",
        "_query_type": "resources",
        "resources": resources_data,
        "count": len(resources_data),
        "message": f"Found {len(resources_data)} resource(s)",
    }


# Action Handlers


async def handle_create_course(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Handle create_course tool call.

    Args:
        args: Tool arguments (title, description, difficulty, modules)
        user_id: User ID
        context: Additional context
        progress_callback: Optional async callback for progress updates
    """
    # Map tool args to action_service format
    action_data = {
        "title": args["title"],
        "description": args.get("description", ""),
        "difficulty": args.get("difficulty", "BEGINNER"),
        "modules": args.get("modules", []),
    }

    # Send initial progress if callback provided
    if progress_callback:
        await progress_callback(
            10, "generating_outline", f"Generating course outline for {action_data['title']}..."
        )

    # Call existing action service with progress callback
    result = await action_service.create_course(
        action_data, user_id, progress_callback=progress_callback
    )
    return result


async def handle_delete_course(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle delete_course tool call."""
    course_id = args.get("course_id") or (context or {}).get("courseId")
    if not course_id:
        return {"status": "error", "message": "No course_id provided."}

    try:
        # Verify ownership
        course = await knowledge_repo.find_course(course_id, user_id)
        if not course:
            return {"status": "error", "message": "Course not found or access denied."}
        await knowledge_repo.delete_course(course_id)
        return {
            "status": "success",
            "action": "delete_course",
            "courseId": course_id,
            "message": "Course deleted successfully.",
        }
    except Exception as e:
        logger.exception("delete_course error: %s", e)
        return {"status": "error", "message": str(e)}


async def handle_create_note(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle create_note tool call."""
    # Map tool args to action_service format
    action_data = {
        "title": args["title"],
        "content": args["content"],
        "topicId": args.get("topic_id"),
        "courseId": args.get("course_id"),
        "summary": args.get("summary"),
    }

    # Call existing action service
    result = await action_service.create_note(action_data, user_id)
    return result


async def handle_create_goal(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle create_goal tool call."""
    # Map tool args to action_service format
    action_data = {
        "title": args["title"],
        "description": args.get("description"),
        "targetDate": args.get("target_date"),
        "courseId": args.get("course_id"),
        "topicId": args.get("topic_id"),
    }

    # Call existing action service
    result = await action_service.create_goal(action_data, user_id)
    return result


async def handle_create_schedule(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle create_schedule tool call."""
    # Map tool args to action_service format
    action_data = {
        "title": args["title"],
        "description": args.get("description"),
        "startAt": args["start_at"],
        "endAt": args["end_at"],
        "recurringRule": args.get("recurring_rule"),
        "courseId": args.get("course_id"),
        "topicId": args.get("topic_id"),
        "goalId": args.get("goal_id"),
    }

    # Call existing action service
    result = await action_service.create_schedule(action_data, user_id)
    return result


async def handle_regenerate_schedule(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle regenerate_schedule tool call — regenerates the user's study plan."""
    import asyncio

    from src.services.schedule_regeneration_service import regenerate_user_schedule

    # Run regeneration (await it so the AI can report the result)
    try:
        await regenerate_user_schedule(user_id)
        return {
            "status": "success",
            "message": (
                "Schedule regenerated successfully. New study blocks have been created "
                "for the next 2 weeks based on your active courses and goals."
            ),
        }
    except Exception as e:
        logger.error(f"Regenerate schedule failed for user {user_id}: {e}")
        return {
            "status": "error",
            "message": f"Failed to regenerate schedule: {str(e)}",
        }


async def handle_check_schedule_conflicts(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle check_schedule_conflicts: overlap against ScheduleBlock (calendar), not StudySession."""
    start_at_str = args.get("start_at")
    end_at_str = args.get("end_at")

    if not start_at_str or not end_at_str:
        return {"status": "error", "message": "Missing start_at or end_at"}

    try:
        from sqlalchemy import select, text

        start_at = datetime.fromisoformat(start_at_str.replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end_at_str.replace("Z", "+00:00"))

        # Query overlapping blocks via raw SQL for complex AND logic
        factory = get_session_factory()
        async with factory() as session:
            from src.domains.progress.db_models import ScheduleBlock
            stmt = (
                select(ScheduleBlock)
                .where(
                    ScheduleBlock.user_id == user_id,
                    ScheduleBlock.start_at < end_at,
                    ScheduleBlock.end_at > start_at,
                )
                .order_by(ScheduleBlock.start_at.asc())
                .limit(20)
            )
            result = await session.execute(stmt)
            conflicting_blocks = list(result.scalars().all())

        if not conflicting_blocks:
            return {
                "status": "success",
                "has_conflicts": False,
                "message": "No conflicts found. Time slot is free.",
            }

        conflicts = [
            f"'{b.title}' from {b.start_at.isoformat()} to {b.end_at.isoformat()}"
            for b in conflicting_blocks
        ]

        return {
            "status": "success",
            "has_conflicts": True,
            "conflicting_schedule_blocks": conflicts,
            "conflicting_sessions": conflicts,
            "message": "Double-booking detected with existing calendar blocks. Suggest alternative times.",
        }
    except Exception as e:
        logger.error("Error checking schedule conflicts: %s", e)
        return {"status": "error", "message": str(e)}


async def handle_recommend_resources(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle recommend_resources tool call."""
    # Map tool args to action_service format
    action_data = {
        "query": args["query"],
        "limit": args.get("limit", 10),
        "topicId": args.get("topic_id"),
        "courseId": args.get("course_id"),
    }
    if args.get("space_id"):
        action_data["spaceId"] = args["space_id"]

    # Call existing action service
    result = await action_service.recommend_resources(action_data, user_id)
    return result


async def handle_retake_note(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle retake_note tool call."""
    # Map tool args to action_service format
    action_data = {
        "noteId": args["note_id"],
    }

    # Call existing action service
    result = await action_service.retake_note(action_data, user_id)
    return result


async def handle_add_summary_to_note(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle add_summary_to_note tool call."""
    # Map tool args to action_service format (note: action_service uses "add_summary")
    action_data = {
        "noteId": args["note_id"],
    }

    # Call existing action service
    result = await action_service.add_summary(action_data, user_id)
    return result


async def handle_add_tags_to_note(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle add_tags_to_note tool call."""
    # Map tool args to action_service format
    action_data = {
        "noteId": args["note_id"],
        "tags": args["tags"],
    }

    # Call existing action service
    result = await action_service.add_tags(action_data, user_id)
    return result


async def handle_complete_review(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle complete_review tool call. Marks the spaced-repetition review as done with SM-2 quality rating."""
    from src.domains.progress.services.spaced_repetition_impl import advance_review_sqlalchemy

    review_item_id = args.get("review_item_id") or (context or {}).get("reviewItemId")
    if not review_item_id:
        return {"status": "error", "message": "No review item in context."}

    quality = args.get("quality", 4)
    try:
        quality = max(0, min(5, int(quality)))
    except (TypeError, ValueError):
        quality = 4

    score_summary = args.get("score_summary", "")

    try:
        result = await advance_review_sqlalchemy(user_id, review_item_id, quality)
        is_lapse = quality < 3
        if is_lapse:
            message = (
                "Review recorded. It looks like this topic needs more practice — "
                "it's been rescheduled for tomorrow so you can reinforce it soon."
            )
        elif quality == 5:
            message = "Perfect recall! This topic is well-mastered. Next review pushed further out."
        elif quality == 4:
            message = "Good job! Review completed. See you next time."
        else:
            message = "Review completed. This one was tough — the next review will come a bit sooner to help reinforce it."

        return {
            "status": "success",
            "message": message,
            "quality": quality,
            "scoreSummary": score_summary,
            "nextReviewAt": result.get("nextReviewAt", ""),
            "intervalDays": result.get("intervalDays", 0),
            "easeFactor": result.get("easeFactor", 2.5),
        }
    except ValueError as e:
        logger.warning("complete_review failed: %s", e)
        return {"status": "error", "message": str(e)}


async def handle_update_course_outline(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Handle update_course_outline tool call.
    Replaces (or creates) modules and topics for an existing course.
    """
    course_id = args.get("course_id") or (context or {}).get("courseId")
    modules_data = args.get("modules", [])

    if not course_id:
        return {"status": "error", "message": "No course_id provided."}
    if not modules_data:
        return {"status": "error", "message": "No modules provided in the outline."}

    course = await knowledge_repo.find_course_with_modules(course_id, user_id)
    if not course:
        return {"status": "error", "message": "Course not found or you don't have access."}

    try:
        # Delete existing modules (cascade deletes topics)
        if course.modules:
            for existing_mod in course.modules:
                await knowledge_repo.delete_module(existing_mod.id)

        # Create new modules and topics
        total_topics = 0
        for i, mod_data in enumerate(modules_data):
            mod_title = mod_data.get("title", f"Module {i + 1}")
            topics = mod_data.get("topics", [])

            module = await knowledge_repo.create_module({
                "courseId": course_id,
                "title": mod_title,
                "order": float(i),
            })

            for j, topic_title in enumerate(topics):
                title = topic_title if isinstance(topic_title, str) else str(topic_title)
                await knowledge_repo.create_topic({
                    "moduleId": module.id,
                    "title": title,
                    "order": float(j),
                })
                total_topics += 1

        # Update description if placeholder
        desc = course.description or ""
        if "outline pending" in desc.lower() or not desc.strip():
            await knowledge_repo.update_course(course_id, {
                "description": f"Course with {len(modules_data)} modules and {total_topics} topics."
            })

        return {
            "status": "success",
            "action": "update_course_outline",
            "courseId": course_id,
            "course_id": course_id,
            "message": f"Outline updated: {len(modules_data)} modules, {total_topics} topics created for {course.title}.",
        }
    except Exception as e:
        logger.error(f"update_course_outline error: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to update outline: {e}"}


async def handle_study_show_visual(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate study overlay visual payload (client renders from WebSocket `study_visual`)."""
    mermaid = (args.get("mermaid") or "").strip()
    display_math = (args.get("display_math") or "").strip()
    if not mermaid and not display_math:
        return {
            "status": "error",
            "message": "Provide mermaid and/or display_math with non-empty content.",
        }
    return {
        "status": "success",
        "shown": True,
        "message": "Visual will appear in Study Mode.",
    }


async def handle_complete_topic_and_continue(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle complete_topic_and_continue tool call."""
    topic_id = (context or {}).get("topicId")
    if topic_id:
        import asyncio

        async def mark_complete() -> None:
            try:
                await knowledge_repo.update_topic(topic_id, {"completed": True})
            except Exception as e:
                logger.warning(f"Failed to mark topic {topic_id} complete: {e}")

        asyncio.create_task(mark_complete())

    return {
        "status": "success",
        "action": "navigate_next",
        "message": "System has marked the topic as completed, notifying client to navigate to the next topic.",
    }


# ==========================================
#  Personalization Handlers
# ==========================================


async def handle_get_my_profile(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_my_profile tool call. Returns comprehensive user profile with remembered facts."""
    from src.domains.identity.repository import IdentityRepository

    identity_repo = IdentityRepository()
    profile: dict[str, Any] = {}

    # Basic user info
    try:
        user = await identity_repo.find_by_id(user_id)
        if user:
            profile["name"] = user.name or "Unknown"
            profile["email"] = user.email
            profile["tier"] = user.tier
            profile["memberSince"] = user.created_at.strftime("%B %Y") if user.created_at else None
            if user.preferences:
                profile["timezone"] = user.preferences.timezone
                profile["language"] = user.preferences.language
                profile["studyGoals"] = user.preferences.study_goals
    except Exception as e:
        logger.warning(f"Failed to fetch user info for profile: {e}")

    # Course summary
    try:
        courses, _ = await knowledge_repo.list_courses(
            user_id, where={"archived": False}, skip=0, take=10
        )
        course_summaries = []
        total_topics_all = 0
        completed_topics_all = 0
        for c in courses:
            total = sum(len(m.topics) for m in c.modules)
            completed = sum(1 for m in c.modules for t in m.topics if t.completed)
            total_topics_all += total
            completed_topics_all += completed
            progress = round((completed / total * 100) if total > 0 else 0)
            course_summaries.append(
                {
                    "title": c.title,
                    "progress": progress,
                    "completedTopics": completed,
                    "totalTopics": total,
                    "difficulty": c.difficulty,
                }
            )
        profile["courses"] = course_summaries
        profile["totalCourses"] = len(course_summaries)
        profile["overallProgress"] = round(
            (completed_topics_all / total_topics_all * 100) if total_topics_all > 0 else 0
        )
    except Exception as e:
        logger.warning(f"Failed to fetch courses for profile: {e}")
        profile["courses"] = []

    # Active goals
    try:
        goals, _ = await progress_repo.list_goals(
            user_id, where={"status": "ACTIVE"}, skip=0, take=10
        )
        profile["activeGoals"] = [
            {
                "title": g.title,
                "progress": g.progress or 0,
                "targetDate": g.target_date.isoformat() if g.target_date else None,
            }
            for g in goals
        ]
    except Exception as e:
        logger.warning(f"Failed to fetch goals for profile: {e}")
        profile["activeGoals"] = []

    # Study streak
    try:
        streak = await progress_repo.get_streak(user_id)
        profile["studyStreak"] = {
            "currentStreak": streak.current_streak if streak else 0,
            "longestStreak": streak.longest_streak if streak else 0,
            "lastStudyDate": (
                streak.last_study_date.isoformat() if streak and streak.last_study_date else None
            ),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch streak for profile: {e}")
        profile["studyStreak"] = {"currentStreak": 0, "longestStreak": 0}

    # Upcoming schedule (next 3 days)
    try:
        now = datetime.now(UTC)
        where_sched: dict[str, Any] = {
            "endAt": {"gte": now},
            "startAt": {"lte": now + timedelta(days=3)},
        }
        blocks, _ = await progress_repo.list_blocks(user_id, where=where_sched, skip=0, take=8)
        profile["upcomingSchedule"] = [
            {"title": s.title, "startAt": s.start_at.isoformat()} for s in blocks
        ]
    except Exception as e:
        logger.warning(f"Failed to fetch schedule for profile: {e}")
        profile["upcomingSchedule"] = []

    # Pending reviews
    try:
        now = datetime.now(UTC)
        due_reviews = await progress_repo.list_due_reviews(user_id, before=now)
        profile["pendingReviews"] = len(due_reviews)
    except Exception as e:
        logger.warning(f"Failed to fetch reviews for profile: {e}")
        profile["pendingReviews"] = 0

    # Remembered facts about the user
    try:
        facts = await intelligence_repo.list_user_facts(user_id, active_only=True, take=30)
        profile["rememberedFacts"] = [{"category": f.category, "content": f.content} for f in facts]
    except Exception as e:
        logger.warning(f"Failed to fetch user facts for profile: {e}")
        profile["rememberedFacts"] = []

    # Achievements
    try:
        achievements = await progress_repo.list_achievements(user_id)
        profile["recentAchievements"] = [
            {"title": a.title, "description": a.description} for a in achievements[:5]
        ]
    except Exception as e:
        logger.warning(f"Failed to fetch achievements for profile: {e}")
        profile["recentAchievements"] = []

    return {
        "_query_type": "profile",
        "profile": profile,
        "message": "User profile retrieved successfully",
    }


async def handle_save_user_fact(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle save_user_fact tool call. Stores a fact the user shared about themselves."""
    category = args.get("category", "other")
    content = args.get("content", "").strip()

    if not content:
        return {"status": "error", "message": "No fact content provided."}

    valid_categories = [
        "preference", "personal", "academic", "goal", "struggle", "strength", "other",
    ]
    if category not in valid_categories:
        category = "other"

    try:
        # Check for duplicate/similar existing facts
        existing_facts = await intelligence_repo.list_user_facts(
            user_id, category=category, active_only=True, take=20
        )

        content_lower = content.lower()
        for existing in existing_facts:
            existing_lower = existing.content.lower()
            new_words = set(content_lower.split())
            existing_words = set(existing_lower.split())
            if new_words and existing_words:
                overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
                if overlap > 0.7:
                    await intelligence_repo.update_user_fact(existing.id, {
                        "content": content, "confidence": 0.9,
                    })
                    return {
                        "status": "success",
                        "action": "update_user_fact",
                        "message": f"Updated remembered fact: {content}",
                    }

        await intelligence_repo.create_user_fact({
            "userId": user_id,
            "category": category,
            "content": content,
            "source": "conversation",
            "confidence": 0.85,
        })

        return {
            "status": "success",
            "action": "save_user_fact",
            "message": f"I'll remember that: {content}",
        }
    except Exception as e:
        logger.error(f"save_user_fact error: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to save fact: {e}"}


# ==========================================
#  Agentic AI Handlers
# ==========================================


async def handle_create_study_plan(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Handle create_study_plan tool call. Creates a multi-step study plan."""
    from src.services.planning_service import create_study_plan

    goal = args.get("goal", "")
    if not goal:
        return {"status": "error", "message": "No study goal specified."}

    duration_weeks = args.get("duration_weeks", 4)
    try:
        duration_weeks = max(1, min(16, int(duration_weeks)))
    except (TypeError, ValueError):
        duration_weeks = 4

    try:
        result = await create_study_plan(
            user_id=user_id,
            goal=goal,
            duration_weeks=duration_weeks,
            context=context,
            progress_callback=progress_callback,
        )
        return result
    except Exception as e:
        logger.error("create_study_plan error: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to create study plan: {e}"}


async def handle_get_learning_insights(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_learning_insights tool call. Returns accumulated learning patterns."""
    try:
        insights = await intelligence_repo.list_insights(user_id, active_only=True, take=15)

        if not insights:
            return {
                "insights": [],
                "count": 0,
                "message": (
                    "No learning insights generated yet. I'll start building a profile "
                    "as you study more — tracking your optimal study times, strengths, "
                    "weaknesses, and what strategies work best for you."
                ),
            }

        insights_data = [
            {
                "type": ins.insight_type,
                "content": ins.content,
                "confidence": round(ins.confidence * 100),
                "dataPoints": ins.data_points,
                "lastUpdated": ins.updated_at.isoformat() if ins.updated_at else None,
            }
            for ins in insights
        ]

        return {
            "insights": insights_data,
            "count": len(insights_data),
            "message": f"Found {len(insights_data)} learning insight(s) about this user.",
        }
    except Exception as e:
        logger.error("get_learning_insights error: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to get learning insights: {e}"}


async def handle_get_pending_nudges(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle get_pending_nudges tool call. Returns proactive AI suggestions."""
    from src.services.memory_service import get_pending_nudges

    limit = args.get("limit", 5)
    try:
        limit = max(1, min(10, int(limit)))
    except (TypeError, ValueError):
        limit = 5

    try:
        nudges = await get_pending_nudges(user_id, limit=limit)

        if not nudges:
            return {
                "nudges": [],
                "count": 0,
                "message": "No pending suggestions right now. You're all caught up!",
            }

        return {
            "nudges": nudges,
            "count": len(nudges),
            "message": f"Found {len(nudges)} suggestion(s) for the user.",
        }
    except Exception as e:
        logger.error("get_pending_nudges error: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to get pending nudges: {e}"}


async def handle_email_user(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle email_user tool call. Sends an email to the user."""
    from src.services import email
    from src.domains.identity.repository import IdentityRepository

    subject = args.get("subject")
    content = args.get("content")

    if not subject or not content:
        return {"status": "error", "message": "Subject and content are required."}

    try:
        identity_repo = IdentityRepository()
        user = await identity_repo.find_by_id(user_id)
        if not user or not user.email:
            return {"status": "error", "message": "User or user email not found."}

        await email.send_bulk_email(
            email=user.email,
            name=user.name,
            subject=subject,
            content=content,
        )

        return {
            "status": "success",
            "action": "email_user",
            "message": f"Email sent successfully to {user.email}",
        }
    except Exception as e:
        logger.error("email_user error: %s", e, exc_info=True)
        return {"status": "error", "message": f"Failed to send email: {e}"}


# ==========================================
#  Document Generation Handlers
# ==========================================


async def handle_generate_document(
    args: dict[str, Any],
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle generate_document tool call. Generates a PDF or DOCX document."""
    from src.services.document_generation_service import document_generation_service

    doc_format = args.get("format", "pdf")
    title = args.get("title")
    content = args.get("content")
    style = args.get("style", "academic")

    if not title:
        return {"status": "error", "message": "Document title is required."}
    if not content:
        return {"status": "error", "message": "Document content is required."}

    if doc_format == "pptx" and style not in ("academic", "report", "minimal"):
        style = "report"

    try:
        result = await document_generation_service.generate_document(
            format=doc_format,
            title=title,
            content=content,
            style=style,
            user_id=user_id,
        )

        # Save document to database via raw SQL (GeneratedDocument not yet in SQLAlchemy)
        share_id = None
        try:
            from sqlalchemy import text
            factory = get_session_factory()
            doc_id = __import__("uuid").uuid4().hex[:25]
            async with factory() as session:
                await session.execute(
                    text(
                        'INSERT INTO "GeneratedDocument" (id, "userId", title, format, style, filename, "fileUrl", "previewUrl", size, "contentType", "isPublic", "createdAt", "updatedAt") '
                        "VALUES (:id, :uid, :title, :fmt, :style, :filename, :url, :preview, :size, :ct, true, now(), now()) RETURNING \"shareId\""
                    ),
                    {
                        "id": doc_id, "uid": user_id, "title": result["title"],
                        "fmt": result["format"], "style": style,
                        "filename": result["filename"], "url": result["url"],
                        "preview": result["preview_url"], "size": result["size"],
                        "ct": result["content_type"],
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to save document record: {e}")

        return {
            "status": "success",
            "action": "generate_document",
            "_component_type": "DocumentCardMessage",
            "message": f"Your {doc_format.upper()} document '{title}' is ready.",
            "document": {
                "title": result["title"],
                "filename": result["filename"],
                "url": result["url"],
                "size": result["size"],
                "format": result["format"],
                "contentType": result["content_type"],
                "previewUrl": result["preview_url"],
                "shareId": share_id,
            },
        }
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Document generation failed for user {user_id}: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to generate document: {str(e)}"}
