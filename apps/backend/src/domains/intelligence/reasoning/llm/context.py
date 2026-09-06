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


def tool_has_side_effect(tool_name: str) -> bool:
    """Authoritative classification for retry safety across every provider adapter."""
    return tool_name.startswith("create_") or tool_name in {
        "recommend_resources",
        "retake_note",
        "add_summary_to_note",
        "add_tags_to_note",
        "complete_review",
        "update_course_outline",
        "generate_document",
        "delete_course",
        "save_user_fact",
        "email_user",
        "regenerate_schedule",
        "complete_topic_and_continue",
    }


async def mark_tool_side_effect_intent(tool_names: list[str], progress_callback: Any) -> None:
    """Durably mark every mutating intent before an adapter invokes any handler."""
    if not progress_callback:
        return
    for tool_name in tool_names:
        if tool_has_side_effect(tool_name):
            await progress_callback(
                0,
                "tool_side_effect_intent",
                "A mutating tool is about to run.",
                tool_side_effect=True,
                tool_name=tool_name,
            )


#: Cap for context blocks rendered whole rather than field by field.
#:
#: Four blocks used to be `str(...)`'d with no bound — `topicResources`,
#: `topicUploadedResources`, `knowledgeBaseContext` and `memory_context` — while every field in the
#: learner-profile family beside them is carefully clipped to 120-600 characters. So the careful caps
#: could be walked straight past by a topic with a long resource list, and the size of a prompt became
#: a property of the learner's data rather than of this function.
#:
#: 2 000 characters is roughly 500 tokens per block: generous next to the 300-character topic-content
#: cap, and bounded, which is the property that was missing. Phase 0 Question 2.
_BLOCK_CHAR_LIMIT = 2000


def _bounded(value: Any, limit: int = _BLOCK_CHAR_LIMIT) -> str:
    """Render a context block as a string of at most `limit` characters.

    The truncation is marked. A silently cut block reads to the model as a complete one, and a
    resource list that stops mid-entry is worse than a short list that says it was shortened.
    """
    rendered = value if isinstance(value, str) else str(value)
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"... [truncated at {limit} characters]"


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
                context_parts.append(_bounded(context["topicUploadedResources"]))
            if context.get("topicResources"):
                context_parts.append("Topic Resources:")
                context_parts.append(_bounded(context["topicResources"]))
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

        if context.get("learnerProfile"):
            profile = context["learnerProfile"]
            context_parts.append("Learner Profile (owner-scoped):")
            for label, key in (
                ("Purpose", "purpose"),
                ("Subjects", "subjects"),
                ("Goals", "goals"),
                ("Explanation style", "explanationStyle"),
            ):
                value = profile.get(key)
                if value:
                    rendered = (
                        ", ".join(str(item) for item in value)
                        if isinstance(value, list)
                        else str(value)
                    )
                    context_parts.append(f"{label}: {rendered[:600]}")

        # Both of the next two are set on **every** turn by `_read_learner_context`, so both were
        # rendered on every turn whatever they held. `learningRhythm` is an empty dict for any learner
        # without enough sessions to measure, and the `.get(..., "unknown")` defaults turned that into
        # the literal line "average session unknown; consistency unknown; best day unknown" — three
        # non-facts, in a prompt, on most turns. Emitted now only when a figure exists, and only the
        # figures that do.
        rhythm = context.get("learningRhythm") or {}
        measured = []
        if rhythm.get("avgSessionMinutes") is not None:
            measured.append(f"average session {rhythm['avgSessionMinutes']} minutes")
        if rhythm.get("consistencyScore") is not None:
            measured.append(f"consistency {rhythm['consistencyScore']}")
        if rhythm.get("bestDayOfWeek"):
            measured.append(f"best day {rhythm['bestDayOfWeek']}")
        if measured:
            context_parts.append("Learning rhythm: " + "; ".join(measured) + ".")

        # `is not None` meant "Flashcards due for review: 0" on every turn for every learner with an
        # empty deck. Zero due cards is not context, it is the absence of it, and the model has a tool
        # for asking.
        if context.get("dueReviewCount"):
            context_parts.append(f"Flashcards due for review: {int(context['dueReviewCount'])}")

        for label, key in (
            ("Exam preparation", "examPrep"),
            ("Study plan", "studyPlan"),
            ("Goal", "goal"),
            ("Reflection", "reflection"),
        ):
            item = context.get(key)
            if item:
                fields = [
                    f"{name}: {str(value)[:600]}"
                    for name, value in item.items()
                    if name != "id" and value
                ]
                context_parts.append(
                    f"{label} (owner-scoped, id {item.get('id')}): " + "; ".join(fields)
                )
        space = context.get("space")
        if space and space.get("membershipVerified"):
            context_parts.append(
                "Verified learning space (no classroom or assignment data loaded): "
                f"name: {str(space.get('name') or '')[:160]}; "
                f"description: {str(space.get('description') or '')[:600]}; "
                f"learner role: {str(space.get('role') or '')[:40]}."
            )

        if context.get("knowledgeBaseContext"):
            context_parts.append(f"\n{_bounded(context['knowledgeBaseContext'])}")

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
                # `RETRIEVAL_LIMIT` bounds the *count* at three; nothing bounded the size of one. A
                # retrieved document is whatever the learner wrote, so a single hit could outweigh
                # the page context retrieval is meant to be supplementing.
                context_parts.append(_bounded(item))
            context_parts.append("(Use these IDs if the user refers to these items)")

        if context.get("memory_context"):
            context_parts.append(f"\n{_bounded(context['memory_context'])}")

    if context_parts:
        context_str = "\n".join(context_parts)
        enhanced_message = f"Context:\n{context_str}\n\nUser Message: {user_message}"

    return enhanced_message
