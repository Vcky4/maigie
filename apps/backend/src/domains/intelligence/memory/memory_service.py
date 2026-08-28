"""
Memory service — long-term context for Intelligence.

Manages conversation summaries, user facts, and interaction history.
Memory allows Intelligence to understand learners over time rather
than starting from zero in every conversation.
"""

import logging
from typing import Any

from ..repository import intelligence_repo

logger = logging.getLogger(__name__)


async def get_memory_context(user_id: str) -> str:
    """The learner's long-term memory, rendered as a prompt block.

    Used by the reasoning layer before generating responses. Includes user facts, recent conversation
    summaries, and learning insights. Returns ``""`` when there is nothing remembered yet.

    **This returns a string, and used to claim it returned a `dict`.** The annotation said
    ``dict[str, Any]`` while `memory_impl.get_memory_context` has always returned formatted prose, and
    two callers believed the annotation. `GET /intelligence/memory/context` declared a structured
    response model and would have `500`d on the first request had the router been mounted;
    `knowledge`'s resource recommender called ``.update()`` on the result and raised ``AttributeError``
    on any request that supplied extra context. Both are fixed. If you want the memory as data rather
    than as prose, use `get_memory_snapshot`.
    """
    from src.domains.intelligence.memory.memory_impl import get_memory_context as _get_context

    return await _get_context(user_id)


async def get_memory_snapshot(user_id: str) -> dict[str, Any]:
    """The learner's memory as data, for the transparency endpoint.

    The prompt-block form (`get_memory_context`) is for the model. This is for the learner and for
    debugging, so it returns the rows themselves rather than a rendering of them.

    Deliberately narrower than the response model it used to promise: `learningGoals`, `strengths` and
    `weaknesses` were declared and never computed by anything, so publishing them as empty lists would
    have said "this learner has no weaknesses" where the truth is "nothing has measured them".
    """
    return {
        "userFacts": await get_user_facts(user_id),
        "recentSummaries": await get_conversation_summaries(user_id, limit=10),
    }


async def get_user_facts(user_id: str) -> list[dict[str, Any]]:
    """Get all learned facts about a user."""
    facts = await intelligence_repo.list_user_facts(user_id, active_only=True, take=50)
    return [
        {
            "id": f.id,
            "fact": f.content,
            "category": f.category,
            "importance": f.confidence,
            "createdAt": f.created_at,
        }
        for f in facts
    ]


async def get_conversation_summaries(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Get recent conversation summaries for context."""
    summaries = await intelligence_repo.list_summaries(user_id, take=limit)
    return [
        {
            "id": s.id,
            "sessionId": s.session_id,
            "summary": s.summary,
            "keyTopics": s.key_topics or [],
            "createdAt": s.created_at,
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
    from src.domains.intelligence.memory.user_memory_impl import user_memory_service

    await user_memory_service.record_interaction(
        user_id=user_id,
        interaction_type=interaction_type,
        entity_type=entity_type,
        entity_id=entity_id,
        importance=importance,
        metadata=metadata,
    )


async def get_pending_nudges(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Get pending AI nudges for a user."""
    return []  # TODO: migrate implementation from services/memory_service
