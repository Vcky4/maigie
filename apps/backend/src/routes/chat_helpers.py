"""
Pure helpers and small DB reads shared by chat routes.

Extracted from chat.py to keep the WebSocket / HTTP route module smaller.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prisma import Prisma

logger = logging.getLogger(__name__)

MAIGIE_MENTION_PATTERN = re.compile(r"@maigie\b", re.IGNORECASE)

STUDIO_TOPIC_OPENER_INSTRUCTION = """You are Maigie. Write ONLY the body of the assistant chat message shown to a learner who just opened the Studio workspace for a course topic (markdown allowed).

Rules:
- Warm and concise: about 2–4 short paragraphs, under 230 words.
- Briefly orient them on how to use this workspace for the topic (infer from the titles below; do not invent detailed facts beyond plausible study guidance).
- Invite them to ask questions in this text thread.
- Tell them they can open **Study** (voice tutor) for a live back-and-forth conversation using Maigie's **Gemini Live** voice tutor—good for talking ideas through hands-free. Do not mention ElevenLabs or other third-party brands.
- Do NOT imply the learner already sent a message. Do NOT use placeholders like [Topic]—use the real titles given below.
- No subject line or meta-commentary—only the message body."""


def _format_resource_context_line(resource) -> str:
    title = (getattr(resource, "title", "") or "Untitled").strip()
    rtype = str(getattr(resource, "type", "OTHER") or "OTHER").upper()
    url = (getattr(resource, "url", "") or "").strip()
    description = (getattr(resource, "description", "") or "").strip()
    if len(description) > 140:
        description = description[:140] + "..."
    line = f"- [{rtype}] {title}"
    if url:
        line += f" ({url})"
    if description:
        line += f" — {description}"
    return line


def _is_ai_generated_resource(resource) -> bool:
    recommendation_source = str(getattr(resource, "recommendationSource", "") or "").lower()
    if recommendation_source == "ai":
        return True
    metadata = getattr(resource, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("studioAiRecommendation") is True:
        return True
    return False


async def _attach_topic_resources_context(
    db_client, user_id: str, topic_id: str, enriched_context: dict
) -> None:
    """
    Attach topic resources into LLM context.
    Includes all topic resources plus a focused list of non-AI uploaded/manual resources.
    """
    try:
        resources = await db_client.resource.find_many(
            where={"userId": user_id, "topicId": topic_id},
            order={"updatedAt": "desc"},
            take=40,
        )
        if not resources:
            enriched_context["topicResourcesCount"] = 0
            enriched_context["topicUploadedResourcesCount"] = 0
            return

        uploaded_resources = [r for r in resources if not _is_ai_generated_resource(r)]

        enriched_context["topicResourcesCount"] = len(resources)
        enriched_context["topicUploadedResourcesCount"] = len(uploaded_resources)

        top_all = resources[:10]
        top_uploaded = uploaded_resources[:10]
        enriched_context["topicResources"] = "\n".join(
            _format_resource_context_line(r) for r in top_all
        )
        if top_uploaded:
            enriched_context["topicUploadedResources"] = "\n".join(
                _format_resource_context_line(r) for r in top_uploaded
            )
    except Exception as e:
        logger.warning("Failed to enrich topic resources in context: %s", e)


def _extract_suggestion(text: str) -> tuple[str, str | None]:
    """
    Extract suggestive follow-up (e.g. "Would you like me to...") from AI response.
    Returns (main_content, suggestion_text). Suggestion is displayed after components.
    """
    if not text or not text.strip():
        return (text, None)
    text = text.strip()
    suggestion_phrases = [
        "How does that look?",
        "How does that look",
        "All of these are now",
        "Would you like me to",
        "Would you like to",
        "Should I ",
        "Or should we ",
    ]
    idx = -1
    for phrase in suggestion_phrases:
        pos = text.lower().find(phrase.lower())
        if pos >= 0 and (idx < 0 or pos < idx):
            idx = pos
    if idx < 0:
        return (text, None)
    para_start = text.rfind("\n\n", 0, idx)
    if para_start >= 0:
        split_at = para_start
    else:
        split_at = idx
    main_content = text[:split_at].strip()
    suggestion_text = text[split_at:].strip()
    if main_content and suggestion_text and len(suggestion_text) > 15:
        return (main_content, suggestion_text)
    return (text, None)


def _map_db_role_to_client(role: str) -> str:
    if role == "USER":
        return "user"
    if role == "ASSISTANT":
        return "assistant"
    return "system"


def _static_studio_topic_opener(*, course_title: str, module_title: str, topic_title: str) -> str:
    mod = f" in **{module_title}**" if module_title else ""
    return (
        f"Welcome! You're in **{topic_title}**{mod} as part of **{course_title}**.\n\n"
        "I'm here to help you build intuition, clear up confusion, and pick concrete next steps. Ask anything in this chat whenever you like.\n\n"
        "When you want to **talk it through** instead of typing, tap **Study** in the workspace header—that starts a **Gemini Live** voice tutor session so you can have a natural spoken back-and-forth while you stay focused on the material.\n\n"
        "What should we focus on first?"
    )


async def _get_circle_group_for_session(db_client: Prisma, session_id: str):
    """Return the circle chat group backing a chat session, if any."""
    return await db_client.circlechatgroup.find_first(
        where={"chatSessionId": session_id},
        include={
            "circle": {
                "include": {
                    "members": {
                        "include": {"user": True},
                    }
                }
            }
        },
    )


def _is_circle_member(circle_group, user_id: str) -> bool:
    """Check whether a user belongs to the circle that owns a chat group."""
    if not circle_group or not getattr(circle_group, "circle", None):
        return False
    return any(member.userId == user_id for member in (circle_group.circle.members or []))


def _strip_maigie_mention(text: str) -> str:
    """Remove direct @maigie mentions before sending text to the model."""
    stripped = MAIGIE_MENTION_PATTERN.sub("", text or "")
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" \t,:")
    return stripped


def _serialize_timestamp(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _serialize_reply_preview(message, fallback_user_name: str | None = None) -> dict | None:
    if not message:
        return None

    reply_user = getattr(message, "user", None)
    return {
        "id": message.id,
        "role": _map_db_role_to_client(str(message.role)),
        "content": getattr(message, "content", "") or "",
        "timestamp": _serialize_timestamp(getattr(message, "createdAt", None)),
        "userId": getattr(message, "userId", None),
        "userName": (
            getattr(reply_user, "name", None) if reply_user is not None else fallback_user_name
        ),
    }


def _extract_course_request(user_text: str) -> tuple[str, str]:
    """
    Best-effort extraction of (topic, difficulty) from a free-form message.
    Kept intentionally cheap to avoid extra LLM calls before replying.
    """
    text = (user_text or "").strip()
    lower = text.lower()

    difficulty = "BEGINNER"
    if "intermediate" in lower:
        difficulty = "INTERMEDIATE"
    elif "advanced" in lower:
        difficulty = "ADVANCED"
    elif "expert" in lower:
        difficulty = "EXPERT"

    patterns = [
        r"(?:create|make|build|generate)\s+(?:me\s+)?(?:a\s+)?course\s+(?:about|on)\s+(?P<topic>.+)",
        r"(?:i\s+want\s+to\s+learn|i\s+would\s+like\s+to\s+learn|help\s+me\s+learn)\s+(?P<topic>.+)",
        r"(?:i\s+want\s+to\s+study|help\s+me\s+study)\s+(?P<topic>.+)",
        r"(?:course\s+on)\s+(?P<topic>.+)",
    ]
    topic = ""
    for pat in patterns:
        m = re.search(pat, lower, re.IGNORECASE)
        if m:
            topic = (m.group("topic") or "").strip()
            break

    if topic:
        topic = re.split(r"[.?!]", topic)[0].strip()
        topic = re.sub(r"\b(for|please|thanks|thank you)\b", "", topic, flags=re.IGNORECASE).strip()

    if not topic:
        words = re.findall(r"[A-Za-z0-9#+\-]+", text)
        topic = " ".join(words[:8]).strip()

    return topic or "a new topic", difficulty


def _looks_like_course_generation_intent(user_text: str) -> bool:
    lower = (user_text or "").lower()
    if not lower.strip():
        return False

    if any(x in lower for x in ["what courses", "my courses", "list courses", "show my courses"]):
        return False

    triggers = [
        "create a course",
        "make a course",
        "generate a course",
        "build a course",
        "course on",
        "course about",
        "i want to learn",
        "help me learn",
        "i want to study",
        "help me study",
    ]
    return any(t in lower for t in triggers)


def _guess_image_media_type(storage_path: str, fallback: str) -> str:
    lower = storage_path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    base = (fallback or "").split(";", 1)[0].strip()
    return base if base.startswith("image/") else "application/octet-stream"
