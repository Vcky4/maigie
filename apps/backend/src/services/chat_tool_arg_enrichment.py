"""
Repair Gemini tool arguments using chat context and prior tool results in the same turn.

The client normally sends page scope in the WebSocket ``context`` (courseId, topicId, noteId,
reviewItemId, …), which ``chat_ws`` merges into ``enriched_context`` and passes to
``get_chat_response_with_tools``. That flow is unchanged.

This module handles cases the model still gets wrong: ``$course_id``-style placeholders after
another tool created an entity in the same request, bogus IDs (titles as IDs), and
topic/note ID mix-ups — without requiring extra frontend payloads beyond the usual context.
"""

from __future__ import annotations

import copy
from typing import Any

from src.core.database import db
from src.services import note_service

_ID_ALIASES: tuple[tuple[str, str], ...] = (
    ("course_id", "courseId"),
    ("goal_id", "goalId"),
    ("topic_id", "topicId"),
    ("note_id", "noteId"),
    ("review_item_id", "reviewItemId"),
    ("schedule_id", "scheduleId"),
)


def _lookup_created_id(created_ids: dict[str, Any] | None, key: str) -> Any | None:
    if not created_ids:
        return None
    if key in created_ids:
        return created_ids[key]
    for snake, camel in _ID_ALIASES:
        if key == snake and camel in created_ids:
            return created_ids[camel]
        if key == camel and snake in created_ids:
            return created_ids[snake]
    return None


def _resolve_placeholders(obj: Any, created_ids: dict[str, Any] | None) -> Any:
    if not created_ids:
        return obj
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, created_ids) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_placeholders(v, created_ids) for v in obj]
    if isinstance(obj, str) and obj.startswith("$") and len(obj) > 1:
        resolved = _lookup_created_id(created_ids, obj[1:])
        if resolved is not None:
            return resolved
    return obj


def _is_bad_id(value: Any) -> bool:
    if not value or not isinstance(value, str):
        return True
    if value.startswith("$"):
        return True
    if "placeholder" in value.lower():
        return True
    if " " in value or len(value) > 40:
        return True
    return False


def merge_successful_tool_result_into_created_ids(
    created_ids: dict[str, Any],
    tool_name: str,
    result: Any,
) -> None:
    """Record IDs from a successful tool so later tools in the same turn can resolve ``$...``."""
    if not isinstance(result, dict) or result.get("status") != "success":
        return

    if tool_name == "create_course":
        cid = result.get("course_id") or result.get("courseId")
        if cid:
            created_ids["course_id"] = cid
            created_ids["courseId"] = cid
    elif tool_name == "create_goal":
        gid = result.get("goal_id") or result.get("goalId")
        if gid:
            created_ids["goal_id"] = gid
            created_ids["goalId"] = gid
    elif tool_name == "create_note":
        nid = result.get("note_id") or result.get("noteId")
        if nid:
            created_ids["note_id"] = nid
            created_ids["noteId"] = nid
    elif tool_name == "create_schedule":
        sched = result.get("schedule")
        if isinstance(sched, dict) and sched.get("id"):
            sid = sched["id"]
            created_ids["schedule_id"] = sid
            created_ids["scheduleId"] = sid


async def enrich_tool_args_for_llm(
    tool_name: str,
    args: dict[str, Any],
    *,
    context: dict[str, Any] | None,
    created_ids: dict[str, Any] | None,
    user_id: str | None,
) -> dict[str, Any]:
    """Return a shallow-deep-copied args dict with repairs applied (never mutates the input)."""
    out = copy.deepcopy(args)
    out = _resolve_placeholders(out, created_ids)

    ctx = context or {}

    # Circle-scoped chat: tools accept circle_id
    if ctx.get("circleId") and not out.get("circle_id"):
        out["circle_id"] = ctx["circleId"]

    if tool_name in ("retake_note", "add_summary_to_note", "add_tags_to_note"):
        await _enrich_note_tool_args(out, ctx, user_id)

    elif tool_name == "create_note":
        await _enrich_create_note_args(out, ctx)

    elif tool_name == "create_goal":
        _enrich_create_goal_args(out, ctx)

    elif tool_name == "recommend_resources":
        _enrich_recommend_resources_args(out, ctx)

    elif tool_name == "create_schedule":
        _enrich_create_schedule_args(out, ctx)

    elif tool_name == "complete_review":
        if not out.get("review_item_id") and ctx.get("reviewItemId"):
            out["review_item_id"] = ctx["reviewItemId"]

    return out


async def _enrich_note_tool_args(out: dict, ctx: dict, user_id: str | None) -> None:
    note_id = out.get("note_id")
    if ctx.get("noteId"):
        ai_note = note_id
        ctx_note = ctx["noteId"]
        ctx_topic = ctx.get("topicId")
        if ai_note == ctx_topic and ctx_topic:
            note_id = ctx_note
        elif ai_note and ai_note != ctx_note:
            note_id = ctx_note
        else:
            note_id = note_id or ctx_note
    elif not note_id and ctx.get("topicId"):
        topic = await db.topic.find_unique(where={"id": ctx["topicId"]})
        if topic:
            ln = await note_service.latest_note_for_topic(db, topic.id, user_id)
            if ln:
                note_id = ln.id
    elif not note_id and ctx.get("noteId"):
        note_id = ctx["noteId"]

    if note_id:
        note = await db.note.find_unique(where={"id": note_id})
        if not note:
            topic = await db.topic.find_unique(where={"id": note_id})
            if topic:
                ln = await note_service.latest_note_for_topic(db, topic.id, user_id)
                if ln:
                    note_id = ln.id
        if note_id:
            out["note_id"] = note_id


async def _enrich_create_note_args(out: dict, ctx: dict) -> None:
    tid = out.get("topic_id")
    if _is_bad_id(tid) and ctx.get("topicId"):
        out["topic_id"] = ctx["topicId"]
    cid = out.get("course_id")
    if _is_bad_id(cid) and ctx.get("courseId"):
        out["course_id"] = ctx["courseId"]
    elif _is_bad_id(cid):
        out.pop("course_id", None)


def _enrich_create_goal_args(out: dict, ctx: dict) -> None:
    cid = out.get("course_id")
    if _is_bad_id(cid) and ctx.get("courseId"):
        out["course_id"] = ctx["courseId"]
    elif _is_bad_id(cid):
        out.pop("course_id", None)

    tid = out.get("topic_id")
    if _is_bad_id(tid) and ctx.get("topicId"):
        out["topic_id"] = ctx["topicId"]
    elif _is_bad_id(tid):
        out.pop("topic_id", None)


def _enrich_recommend_resources_args(out: dict, ctx: dict) -> None:
    if not out.get("topic_id") and ctx.get("topicId"):
        out["topic_id"] = ctx["topicId"]
    if not out.get("course_id") and ctx.get("courseId"):
        out["course_id"] = ctx["courseId"]
    if not out.get("circle_id") and ctx.get("circleId"):
        out["circle_id"] = ctx["circleId"]


def _enrich_create_schedule_args(out: dict, ctx: dict) -> None:
    pairs = (
        ("course_id", "courseId"),
        ("topic_id", "topicId"),
        ("goal_id", "goalId"),
    )
    for snake, camel in pairs:
        val = out.get(snake)
        ctx_val = ctx.get(camel)
        if _is_bad_id(val) and ctx_val:
            out[snake] = ctx_val
        elif _is_bad_id(val):
            out.pop(snake, None)
