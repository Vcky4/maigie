"""
Prompt/context helpers for chat-style LLM calls.

Keeps `llm_service.py` smaller and gives a stable place for adapter code to reuse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def map_tool_to_action_type(tool_name: str) -> str:
    """Map tool name to action type for logging / executed_actions."""
    mapping = {
        "create_course": "create_course",
        "create_note": "create_note",
        "create_goal": "create_goal",
        "create_schedule": "create_schedule",
        "check_schedule_conflicts": "check_schedule_conflicts",
        "recommend_resources": "recommend_resources",
        "retake_note": "retake_note",
        "add_summary_to_note": "add_summary",
        "add_tags_to_note": "add_tags",
        "complete_review": "complete_review",
        "update_course_outline": "update_course_outline",
        "delete_course": "delete_course",
    }
    return mapping.get(tool_name, tool_name)


def build_enhanced_chat_user_message(
    user_message: str, context: dict[str, Any] | None = None
) -> str:
    """Prefix the user message with structured context (page, course, topic, memory, etc.)."""
    enhanced_message = user_message

    current_datetime = datetime.now(UTC)
    current_date_str = current_datetime.strftime("%A, %B %d, %Y at %H:%M UTC")

    context_parts = [f"Current Date & Time: {current_date_str}"]

    if context:
        if context.get("pageContext"):
            context_parts.append(f"Current Page Context: {context['pageContext']}")

        if context.get("courseTitle"):
            context_parts.append(f"Current Course: {context['courseTitle']}")
            if context.get("courseDescription"):
                context_parts.append(f"Course Description: {context['courseDescription']}")
        elif context.get("courseId"):
            context_parts.append(f"Current Course ID: {context['courseId']}")

        if context.get("topicTitle"):
            context_parts.append(f"Current Topic: {context['topicTitle']}")
            if context.get("moduleTitle"):
                context_parts.append(f"Module: {context['moduleTitle']}")
            if context.get("topicContent"):
                topic_content = context["topicContent"]
                if len(topic_content) > 300:
                    topic_content = topic_content[:300] + "..."
                context_parts.append(f"Topic Content: {topic_content}")
            if context.get("topicUploadedResources"):
                context_parts.append(
                    "Topic Uploaded/Manual Resources (highest priority references):"
                )
                context_parts.append(str(context["topicUploadedResources"]))
            if context.get("topicResources"):
                context_parts.append("Topic Resources:")
                context_parts.append(str(context["topicResources"]))
        elif context.get("topicId"):
            context_parts.append(f"Current Topic ID: {context['topicId']}")

        if context.get("noteTitle"):
            context_parts.append(f"Current Note: {context['noteTitle']}")
            if context.get("noteContent"):
                note_content = context["noteContent"]
                if len(note_content) > 300:
                    note_content = note_content[:300] + "..."
                context_parts.append(f"Note Content: {note_content}")
            if context.get("noteSummary"):
                context_parts.append(f"Note Summary: {context['noteSummary']}")
        elif context.get("noteId"):
            context_parts.append(f"Current Note ID: {context['noteId']}")

        if context.get("circleName"):
            context_parts.append(f"Circle Group: {context['circleName']}")
            if context.get("chatGroupName"):
                context_parts.append(f"Chat Group: {context['chatGroupName']}")
            if context.get("circleId"):
                context_parts.append(f"Circle ID: {context['circleId']}")
            if context.get("memberCount"):
                context_parts.append(f"Circle Members: {context['memberCount']}")

        if context.get("replyContext"):
            reply_context = context["replyContext"]
            reply_content = (reply_context.get("content") or "").strip()
            if len(reply_content) > 280:
                reply_content = reply_content[:280] + "..."

            reply_role = reply_context.get("role") or "user"
            reply_author = reply_context.get("userName") or (
                "Maigie" if reply_role == "assistant" else "Member"
            )
            context_parts.append("Reply Context:")
            context_parts.append(
                f"Replying to {reply_author} ({reply_role}): {reply_content or '[no content]'}"
            )
            context_parts.append(
                "Interpret the user's message as a direct reply to this message first."
            )

        if context.get("retrieved_items"):
            context_parts.append("\nPossibly Relevant Items found in Database:")
            for item in context["retrieved_items"]:
                context_parts.append(str(item))
            context_parts.append("(Use these IDs if the user refers to these items)")

        if context.get("memory_context"):
            context_parts.append(f"\n{context['memory_context']}")

    if context_parts:
        context_str = "\n".join(context_parts)
        enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

    return enhanced_message
