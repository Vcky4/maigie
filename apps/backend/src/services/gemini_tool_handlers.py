"""
Handlers for Gemini tool calls.
Executes DB queries or calls action_service methods based on tool calls.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.core.database import db
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
    handlers_with_progress = {"create_course"}

    handlers = {
        # Query handlers
        "get_user_courses": handle_get_user_courses,
        "get_user_goals": handle_get_user_goals,
        "get_user_schedule": handle_get_user_schedule,
        "get_user_notes": handle_get_user_notes,
        "get_user_resources": handle_get_user_resources,
        # Action handlers
        "create_course": handle_create_course,
        "create_note": handle_create_note,
        "create_goal": handle_create_goal,
        "create_schedule": handle_create_schedule,
        "recommend_resources": handle_recommend_resources,
        "retake_note": handle_retake_note,
        "add_summary_to_note": handle_add_summary_to_note,
        "add_tags_to_note": handle_add_tags_to_note,
        "complete_review": handle_complete_review,
        "update_course_outline": handle_update_course_outline,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}

    # Enrich args with context
    if context:
        if "courseId" in context and "course_id" not in args:
            args["course_id"] = context["courseId"]
        if "topicId" in context and "topic_id" not in args:
            args["topic_id"] = context["topicId"]
        if "noteId" in context and "note_id" not in args:
            args["note_id"] = context["noteId"]
        if "reviewItemId" in context and "review_item_id" not in args:
            args["review_item_id"] = context["reviewItemId"]

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
    if not isinstance(limit, (int, float)) or limit < 1 or limit > 100:
        limit = 20

    include_archived = args.get("include_archived", False)

    # Fetch from DB
    courses = await db.course.find_many(
        where={"userId": user_id, "archived": include_archived},
        include={"modules": {"include": {"topics": True}}},
        order={"updatedAt": "desc"},
        take=int(limit),
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

    # Return formatted result for component response
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
    if not isinstance(limit, (int, float)) or limit < 1 or limit > 100:
        limit = 20

    course_id = args.get("course_id")

    # Build where clause
    where_clause = {"userId": user_id, "status": status}
    if course_id:
        where_clause["courseId"] = course_id

    # Fetch from DB
    goals = await db.goal.find_many(
        where=where_clause,
        order={"updatedAt": "desc"},
        take=int(limit),
    )

    # Format data
    goals_data = []
    for goal in goals:
        goals_data.append(
            {
                "goalId": goal.id,
                "id": goal.id,
                "title": goal.title,
                "description": goal.description or "",
                "targetDate": goal.targetDate.isoformat() if goal.targetDate else None,
                "progress": goal.progress or 0,
                "status": goal.status,
                "courseId": goal.courseId,
                "topicId": goal.topicId,
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
    start_date_str = args.get("start_date", "today")
    end_date_str = args.get("end_date", "+30days")
    limit = args.get("limit", 50)
    if not isinstance(limit, (int, float)) or limit < 1 or limit > 200:
        limit = 50

    course_id = args.get("course_id")

    # Parse start_date
    now = datetime.now(UTC)
    if start_date_str == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif start_date_str == "tomorrow":
        start_date = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif start_date_str == "this_week":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        except Exception:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Parse end_date
    if end_date_str == "today":
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif end_date_str == "tomorrow":
        end_date = (now + timedelta(days=1)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    elif end_date_str == "next_week":
        end_date = now + timedelta(days=7)
    elif end_date_str.startswith("+") and end_date_str[1:].rstrip("days").isdigit():
        days = int(end_date_str[1:].rstrip("days"))
        end_date = now + timedelta(days=days)
    else:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except Exception:
            end_date = now + timedelta(days=30)

    # Build where clause
    where_clause = {
        "userId": user_id,
        "startAt": {"gte": start_date, "lte": end_date},
    }
    if course_id:
        where_clause["courseId"] = course_id

    # Fetch from DB
    schedules = await db.scheduleblock.find_many(
        where=where_clause,
        order={"startAt": "asc"},
        take=int(limit),
    )

    # Format data
    schedules_data = []
    for schedule in schedules:
        schedules_data.append(
            {
                "scheduleId": schedule.id,
                "id": schedule.id,
                "title": schedule.title,
                "startAt": schedule.startAt.isoformat() if schedule.startAt else None,
                "endAt": schedule.endAt.isoformat() if schedule.endAt else None,
                "description": schedule.description or "",
                "courseId": schedule.courseId,
                "topicId": schedule.topicId,
                "goalId": schedule.goalId,
                "reviewItemId": getattr(schedule, "reviewItemId", None),
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
    if not isinstance(limit, (int, float)) or limit < 1 or limit > 100:
        limit = 20

    include_archived = args.get("include_archived", False)
    topic_id = args.get("topic_id")
    course_id = args.get("course_id")

    # Build where clause
    where_clause = {"userId": user_id, "archived": include_archived}
    if topic_id:
        where_clause["topicId"] = topic_id
    if course_id:
        where_clause["courseId"] = course_id

    # Fetch from DB
    notes = await db.note.find_many(
        where=where_clause,
        order={"updatedAt": "desc"},
        take=int(limit),
    )

    # Format data
    notes_data = []
    for note in notes:
        notes_data.append(
            {
                "noteId": note.id,
                "id": note.id,
                "title": note.title,
                "content": note.content or "",
                "summary": note.summary,
                "createdAt": note.createdAt.isoformat() if note.createdAt else None,
                "updatedAt": note.updatedAt.isoformat() if note.updatedAt else None,
                "courseId": note.courseId,
                "topicId": note.topicId,
            }
        )

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
    if not isinstance(limit, (int, float)) or limit < 1 or limit > 100:
        limit = 20

    topic_id = args.get("topic_id")
    course_id = args.get("course_id")
    resource_type = args.get("resource_type")

    # Build where clause
    where_clause = {"userId": user_id}
    if topic_id:
        where_clause["topicId"] = topic_id
    if course_id:
        where_clause["courseId"] = course_id
    if resource_type:
        where_clause["type"] = resource_type

    # Fetch from DB
    resources = await db.resource.find_many(
        where=where_clause,
        order={"createdAt": "desc"},
        take=int(limit),
    )

    # Format data
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
                "courseId": resource.courseId,
                "topicId": resource.topicId,
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
    """Handle complete_review tool call. Marks the spaced-repetition review as done."""
    from src.services.spaced_repetition_service import advance_review

    review_item_id = args.get("review_item_id") or (context or {}).get("reviewItemId")
    if not review_item_id:
        return {"status": "error", "message": "No review item in context."}
    try:
        await advance_review(
            db,
            review_item_id=review_item_id,
            user_id=user_id,
            completed_on_time=True,
        )
        return {"status": "success", "message": "Review completed! Great job."}
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
    Replaces (or creates) modules and topics for an existing course
    based on an outline the user provided in chat (text or image).
    """
    course_id = args.get("course_id") or (context or {}).get("courseId")
    modules_data = args.get("modules", [])

    if not course_id:
        return {"status": "error", "message": "No course_id provided."}

    if not modules_data:
        return {"status": "error", "message": "No modules provided in the outline."}

    # Verify ownership
    course = await db.course.find_first(
        where={"id": course_id, "userId": user_id},
        include={"modules": True},
    )
    if not course:
        return {"status": "error", "message": "Course not found or you don't have access."}

    try:
        # Delete existing modules + topics (cascade deletes topics)
        if course.modules:
            for existing_mod in course.modules:
                await db.module.delete(where={"id": existing_mod.id})

        # Create new modules and topics from the outline
        total_topics = 0
        for i, mod_data in enumerate(modules_data):
            mod_title = mod_data.get("title", f"Module {i + 1}")
            topics = mod_data.get("topics", [])

            module = await db.module.create(
                data={
                    "courseId": course_id,
                    "title": mod_title,
                    "order": float(i),
                }
            )

            for j, topic_title in enumerate(topics):
                title = topic_title if isinstance(topic_title, str) else str(topic_title)
                await db.topic.create(
                    data={
                        "moduleId": module.id,
                        "title": title,
                        "order": float(j),
                    }
                )
                total_topics += 1

        # Update course description if it was the default placeholder
        desc = course.description or ""
        if "outline pending" in desc.lower() or not desc.strip():
            await db.course.update(
                where={"id": course_id},
                data={
                    "description": f"Course with {len(modules_data)} modules and {total_topics} topics."
                },
            )

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
