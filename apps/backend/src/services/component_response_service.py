"""
Component Response Service.
Maps action types to frontend component types and formats component responses.
"""

from typing import Dict, Any, Optional


def map_action_to_component(action_type: str) -> Optional[str]:
    """
    Map backend action type to frontend component type.

    Args:
        action_type: Backend action type (e.g., "create_course", "create_goal")

    Returns:
        Frontend component type (e.g., "CourseCardMessage", "GoalCardMessage") or None
    """
    action_to_component = {
        "create_course": "CourseCardMessage",
        "create_goal": "GoalCardMessage",
        "create_note": "NoteCardMessage",
        "create_schedule": "ScheduleBlockMessage",
        "recommend_resources": "ResourceListMessage",
    }
    return action_to_component.get(action_type)


def format_component_response(
    component_type: str, data: Dict[str, Any], text: Optional[str] = None
) -> Dict[str, Any]:
    """
    Format a component response for the frontend.

    Args:
        component_type: Frontend component type (e.g., "CourseCardMessage")
        data: Component data dictionary
        text: Optional text to display alongside component

    Returns:
        Formatted component response dictionary
    """
    return {
        "type": "component",
        "component": component_type,
        "data": data,
        "text": text,
    }


async def format_action_component_response(
    action_type: str,
    action_result: Dict[str, Any],
    action_data: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    db=None,
) -> Optional[Dict[str, Any]]:
    """
    Format an action result as a component response.
    Fetches full data from database to include in component.

    Args:
        action_type: Backend action type (e.g., "create_course")
        action_result: Result from action_service.execute_action
        action_data: Original action data (optional, for additional context)
        user_id: User ID for database queries (optional)

    Returns:
        Formatted component response or None if action failed or no component mapping
    """
    if not action_result or action_result.get("status") != "success":
        return None

    component_type = map_action_to_component(action_type)
    if not component_type:
        return None

    # Extract component data from action result
    component_data = {}
    text = action_result.get("message", "")

    if not db:
        # If db not provided, return None (fallback to basic data will be handled by caller)
        return None

    if action_type == "create_course":
        # Fetch full course data
        course_id = action_result.get("courseId") or action_result.get("course_id")
        if course_id and user_id:
            try:
                course = await db.course.find_unique(
                    where={"id": course_id, "userId": user_id},
                    include={"modules": {"include": {"topics": True}}},
                )
                if course:
                    # Calculate progress
                    total_topics = sum(len(m.topics) for m in course.modules)
                    completed_topics = sum(
                        sum(1 for t in m.topics if t.completed) for m in course.modules
                    )
                    progress = (completed_topics / total_topics * 100) if total_topics > 0 else 0.0

                    component_data = {
                        "courseId": course.id,
                        "id": course.id,
                        "title": course.title,
                        "description": course.description or "",
                        "progress": progress,
                        "difficulty": course.difficulty,
                    }
            except Exception as e:
                print(f"Error fetching course data: {e}")
                # Fallback to basic data
                component_data = {
                    "courseId": course_id,
                    "id": course_id,
                    "title": action_data.get("title") if action_data else "Course",
                    "description": action_data.get("description") if action_data else "",
                    "progress": 0.0,
                }

    elif action_type == "create_goal":
        goal_id = action_result.get("goalId") or action_result.get("goal_id")
        if goal_id and user_id:
            try:
                goal = await db.goal.find_unique(where={"id": goal_id, "userId": user_id})
                if goal:
                    component_data = {
                        "goalId": goal.id,
                        "id": goal.id,
                        "title": goal.title,
                        "description": goal.description or "",
                        "targetDate": goal.targetDate.isoformat() if goal.targetDate else None,
                        "progress": goal.progress,
                    }
            except Exception as e:
                print(f"Error fetching goal data: {e}")
                # Fallback to basic data
                component_data = {
                    "goalId": goal_id,
                    "id": goal_id,
                    "title": action_data.get("title") if action_data else "Goal",
                    "description": action_data.get("description") if action_data else "",
                    "targetDate": action_data.get("targetDate") if action_data else None,
                    "progress": 0.0,
                }

    elif action_type == "create_note":
        note_id = action_result.get("noteId") or action_result.get("note_id")
        if note_id and user_id:
            try:
                note = await db.note.find_unique(where={"id": note_id, "userId": user_id})
                if note:
                    component_data = {
                        "noteId": note.id,
                        "id": note.id,
                        "title": note.title,
                        "content": note.content or "",
                        "createdAt": note.createdAt.isoformat() if note.createdAt else None,
                    }
            except Exception as e:
                print(f"Error fetching note data: {e}")
                # Fallback to basic data
                component_data = {
                    "noteId": note_id,
                    "id": note_id,
                    "title": action_data.get("title") if action_data else "Note",
                    "content": action_data.get("content") if action_data else "",
                }

    elif action_type == "create_schedule":
        schedule = action_result.get("schedule") or {}
        schedule_id = (
            schedule.get("id")
            or action_result.get("scheduleId")
            or action_result.get("schedule_id")
        )
        if schedule_id and user_id:
            try:
                schedule_obj = await db.scheduleblock.find_unique(
                    where={"id": schedule_id, "userId": user_id}
                )
                if schedule_obj:
                    component_data = {
                        "scheduleId": schedule_obj.id,
                        "id": schedule_obj.id,
                        "title": schedule_obj.title,
                        "startAt": (
                            schedule_obj.startAt.isoformat() if schedule_obj.startAt else None
                        ),
                        "endAt": schedule_obj.endAt.isoformat() if schedule_obj.endAt else None,
                        "description": schedule_obj.description or "",
                    }
            except Exception as e:
                print(f"Error fetching schedule data: {e}")
                # Fallback to basic data
                component_data = {
                    "scheduleId": schedule_id,
                    "id": schedule_id,
                    "title": (
                        schedule.get("title") or action_data.get("title")
                        if action_data
                        else "Schedule"
                    ),
                    "startAt": (
                        schedule.get("startAt") or action_data.get("startAt")
                        if action_data
                        else None
                    ),
                    "endAt": (
                        schedule.get("endAt") or action_data.get("endAt") if action_data else None
                    ),
                    "description": (
                        schedule.get("description") or action_data.get("description")
                        if action_data
                        else None
                    ),
                }

    elif action_type == "recommend_resources":
        resources = action_result.get("resources", [])
        if resources:
            # Format resources for ResourceListMessage component
            formatted_resources = []
            for resource in resources:
                formatted_resources.append(
                    {
                        "resourceId": resource.get("id"),
                        "id": resource.get("id"),
                        "title": resource.get("title", "Untitled"),
                        "url": resource.get("url", ""),
                        "description": resource.get("description", ""),
                        "type": resource.get("type", "OTHER"),
                        "score": resource.get("score", 0.0),
                    }
                )
            component_data = {
                "resources": formatted_resources,
            }
        else:
            return None  # No resources to display

    if not component_data:
        return None

    return format_component_response(component_type, component_data, text)


def format_list_component_response(
    component_type: str, items: list, text: Optional[str] = None
) -> Dict[str, Any]:
    """
    Format a list component response (e.g., CourseListMessage, GoalListMessage).

    Args:
        component_type: Frontend component type (e.g., "CourseListMessage")
        items: List of items to display
        text: Optional text to display alongside component

    Returns:
        Formatted component response dictionary
    """
    # Map component type to data key
    data_key_map = {
        "CourseListMessage": "courses",
        "GoalListMessage": "goals",
        "NoteListMessage": "notes",
        "ScheduleViewMessage": "schedules",
        "ResourceListMessage": "resources",
    }

    data_key = data_key_map.get(component_type, "items")
    component_data = {data_key: items}

    return format_component_response(component_type, component_data, text)
