"""
Memory service — long-term context for Intelligence.

Manages conversation summaries, user facts, and interaction history.
Memory allows Intelligence to understand learners over time rather
than starting from zero in every conversation.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


async def get_memory_context(user_id: str) -> dict[str, Any]:
    """Build the full memory context for a user.

    Used by the reasoning layer before generating responses.
    Includes user facts, recent conversation summaries, and learning context.
    """
    from src.services.memory_service import get_memory_context as _get_context

    return await _get_context(user_id)


async def get_user_facts(user_id: str) -> list[dict[str, Any]]:
    """Get all learned facts about a user."""
    facts = await db.userfact.find_many(
        where={"userId": user_id},
        order={"importance": "desc"},
    )
    return [
        {
            "id": f.id,
            "fact": f.fact,
            "category": getattr(f, "category", None),
            "importance": getattr(f, "importance", 0.5),
            "createdAt": f.createdAt,
        }
        for f in facts
    ]


async def get_conversation_summaries(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Get recent conversation summaries for context."""
    summaries = await db.conversationsummary.find_many(
        where={"userId": user_id},
        order={"createdAt": "desc"},
        take=limit,
    )
    return [
        {
            "id": s.id,
            "sessionId": s.sessionId,
            "summary": s.summary,
            "keyTopics": getattr(s, "keyTopics", []),
            "createdAt": s.createdAt,
        }
        for s in summaries
    ]


async def record_interaction(
    *,
    user_id: str,
    interaction_type: str,
    entity_type: str,
    entity_id: str | None = None,
    importance: float = 0.5,
    metadata: dict | None = None,
) -> None:
    """Record a user interaction for future personalization."""
    from src.services.user_memory_service import user_memory_service

    await user_memory_service.record_interaction(
        user_id=user_id,
        interaction_type=interaction_type,
        entity_type=entity_type,
        entity_id=entity_id,
        importance=importance,
        metadata=metadata,
    )
