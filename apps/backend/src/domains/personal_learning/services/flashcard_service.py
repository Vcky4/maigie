"""
Flashcard service — user-created and AI-generated flashcards with SM-2 spaced repetition.

Learners retain knowledge long-term with minimal effort through intelligent
spaced repetition scheduling.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..repository import personal_learning_repo as repo
from .cache import cached as _cached

logger = logging.getLogger(__name__)


async def create_flashcard(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Create a flashcard with SM-2 initialization.

    Req 5.1: Initialize with interval=1, repetition=0, ease_factor=2.5.
    """
    now = datetime.now(UTC)
    flashcard_data = {
        "userId": user_id,
        "front": data["front"],
        "back": data["back"],
        "deckId": data.get("deckId"),
        "sourceType": data.get("sourceType"),
        "sourceId": data.get("sourceId"),
        # SM-2 initialization
        "intervalDays": 1,
        "repetitionCount": 0,
        "easeFactor": 2.5,
        "nextReviewAt": now + timedelta(days=1),
        "lastQuality": -1,
        "lapseCount": 0,
    }
    result = await repo.create_flashcard(flashcard_data)

    # Invalidate stats cache since total count changed
    await _get_statistics_cached.invalidate(user_id=user_id)

    return result


async def generate_from_note(*, user_id: str, note_id: str) -> list[Any]:
    """
    Generate flashcards from a note using AI.

    Req 5.2: Extract key concepts from note content and create flashcards.

    FREE: up to 5 basic Q&A cards.
    PLUS: up to 10 cards with varied types (cloze, multi-choice, image prompts).
    """
    from . import feature_tier_service, trial_service
    from .llm_resilient import generate_content_json

    note = await repo.find_note(note_id, user_id)
    if not note or not note.content:
        return []

    # Determine quality tier for generation
    quality_tier = await feature_tier_service.get_quality_tier(user_id)

    if quality_tier == "plus":
        max_cards = 10
        card_types_instruction = (
            "Generate varied card types including:\n"
            "- Standard Q&A (front: question, back: answer)\n"
            "- Cloze deletion (front: sentence with ___ blank, back: the missing word/phrase)\n"
            "- Multiple choice (front: question with options A/B/C/D, back: correct answer + explanation)\n"
        )
        await trial_service.record_plus_feature_used(user_id, "flashcard_generation")
    else:
        max_cards = 5
        card_types_instruction = (
            "Create standard Q&A flashcards (front: question/term, back: answer/definition)."
        )

    prompt = (
        f"Extract key concepts from this note and create flashcards.\n"
        f"Title: {note.title}\n"
        f"Content:\n{note.content[:3000]}\n\n"
        f"{card_types_instruction}\n"
        f"Return a JSON array of objects with 'front' and 'back' fields.\n"
        f"Generate {max_cards} flashcards covering the most important concepts.\n"
        f"Return ONLY the JSON array, no other text."
    )

    cards_data = await generate_content_json(prompt, max_tokens=2000, fallback=[], user_id=user_id)
    if not cards_data:
        return []

    created_cards = []
    for card in cards_data[:max_cards]:
        if isinstance(card, dict) and "front" in card and "back" in card:
            flashcard = await create_flashcard(
                user_id=user_id,
                data={
                    "front": card["front"],
                    "back": card["back"],
                    "sourceType": "note",
                    "sourceId": note_id,
                },
            )
            created_cards.append(flashcard)

    return created_cards


async def generate_from_topic(*, user_id: str, topic_id: str) -> list[Any]:
    """
    Generate flashcards from a topic using AI.

    Req 5.3: Generate flashcards based on topic content and materials.
    """
    from sqlalchemy import select as sa_select

    from src.domains.knowledge.db_models import Topic
    from src.shared.database import get_session_factory

    from .llm_resilient import generate_content_json

    factory = get_session_factory()
    async with factory() as session:
        stmt = sa_select(Topic).where(Topic.id == topic_id)
        result = await session.execute(stmt)
        topic = result.scalar_one_or_none()

    if not topic:
        return []

    prompt = (
        f"Create flashcards for studying this topic:\n"
        f"Topic: {topic.title}\n"
        f"Description: {getattr(topic, 'description', '') or ''}\n\n"
        f"Return a JSON array of objects with 'front' (question/concept) and 'back' (answer/explanation).\n"
        f"Generate 5-10 flashcards covering key concepts, definitions, and important details.\n"
        f"Return ONLY the JSON array, no other text."
    )

    cards_data = await generate_content_json(prompt, max_tokens=2000, fallback=[], user_id=user_id)
    if not cards_data:
        return []

    created_cards = []
    for card in cards_data:
        if isinstance(card, dict) and "front" in card and "back" in card:
            flashcard = await create_flashcard(
                user_id=user_id,
                data={
                    "front": card["front"],
                    "back": card["back"],
                    "sourceType": "topic",
                    "sourceId": topic_id,
                },
            )
            created_cards.append(flashcard)

    return created_cards


async def review_flashcard(*, user_id: str, card_id: str, quality: int) -> Any:
    """
    Review a flashcard using SM-2 algorithm.

    Req 5.4: Update SM-2 parameters and compute next review date.
    Req 5.6: If quality < 3, reset interval to 1, increment lapse count.

    SM-2 Algorithm:
    - quality 0-2: reset (lapse)
    - quality 3-5: graduate (increase interval)
    - ease_factor adjusts: EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    - ease_factor minimum: 1.3
    """
    card = await repo.get_flashcard(card_id, user_id)
    if not card:
        return None

    now = datetime.now(UTC)
    new_ease = card.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ease = max(new_ease, 1.3)  # Minimum ease factor

    if quality < 3:
        # Lapse: reset interval, increment lapse count
        new_interval = 1
        new_repetition = 0
        new_lapse = card.lapse_count + 1
    else:
        # Graduate: increase interval
        new_lapse = card.lapse_count
        if card.repetition_count == 0:
            new_interval = 1
        elif card.repetition_count == 1:
            new_interval = 6
        else:
            new_interval = round(card.interval_days * new_ease)
        new_repetition = card.repetition_count + 1

    next_review = now + timedelta(days=new_interval)

    update_data = {
        "intervalDays": new_interval,
        "repetitionCount": new_repetition,
        "easeFactor": round(new_ease, 4),
        "nextReviewAt": next_review,
        "lastReviewedAt": now,
        "lastQuality": quality,
        "lapseCount": new_lapse,
    }

    result = await repo.update_flashcard(card_id, update_data)

    # Invalidate stats cache since a review changes due counts and mastery
    await _get_statistics_cached.invalidate(user_id=user_id)

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="flashcard_reviewed",
        title=f"Reviewed flashcard (quality: {quality}/5)",
        context={"source": "personal", "cardId": card_id, "quality": quality},
    )

    # Check milestones (50_flashcards_reviewed)
    from . import milestone_service

    total_reviews = new_repetition  # Approximate: use this card's repetition count as proxy
    # Get actual total reviewed cards for this user
    stats = await repo.get_flashcard_stats(user_id)
    total_reviewed = stats.get("total", 0) - stats.get(
        "due_today", 0
    )  # reviewed = total minus still-due
    await milestone_service.check_milestones(user_id, {"total_flashcard_reviews": total_reviewed})

    return result


async def get_due_flashcards(*, user_id: str) -> list[Any]:
    """
    Get flashcards due for review.

    Req 5.5: Return cards where next_review_at <= now, ordered by urgency (most overdue first).
    """
    return await repo.list_due_flashcards(user_id)


async def get_statistics(*, user_id: str) -> dict[str, Any]:
    """
    Get flashcard statistics.

    Req 5.8: Return total, due_today, mastered (interval > 21), average ease factor.

    Cached for 60s — stats change only on flashcard review or creation.
    """
    return await _get_statistics_cached(user_id=user_id)


@_cached(ttl_seconds=60, max_size=1000, key_arg="user_id")
async def _get_statistics_cached(*, user_id: str) -> dict[str, Any]:
    """Cached inner implementation."""
    stats = await repo.get_flashcard_stats(user_id)
    return {
        "total": stats["total"],
        "dueToday": stats["due_today"],
        "masteredCount": stats["mastered_count"],
        "averageEaseFactor": stats["avg_ease_factor"],
        "reviewedTotal": stats["reviewed_total"],
        "reviewedThisWeek": stats["reviewed_this_week"],
        "activeDaysThisWeek": stats["active_days_this_week"],
        "currentStreak": stats["current_streak"],
    }


async def create_deck(*, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create a flashcard deck."""
    deck_data = {
        "userId": user_id,
        "title": data["title"],
        "description": data.get("description"),
        "courseId": data.get("courseId"),
        "topicId": data.get("topicId"),
        "prepId": data.get("prepId"),
    }
    deck = await repo.create_deck(deck_data)
    return {
        "id": deck.id,
        "userId": deck.user_id,
        "title": deck.title,
        "description": deck.description,
        "courseId": deck.course_id,
        "topicId": deck.topic_id,
        "prepId": deck.prep_id,
        # A new deck has no cards yet; both counts are known to be zero.
        "cardCount": 0,
        "dueCount": 0,
        "createdAt": deck.created_at,
        "updatedAt": deck.updated_at,
    }


async def list_decks(*, user_id: str) -> list[dict[str, Any]]:
    """List all user's flashcard decks with card and due counts."""
    rows = await repo.list_decks_with_counts(user_id)
    return [
        {
            "id": deck.id,
            "userId": deck.user_id,
            "title": deck.title,
            "description": deck.description,
            "courseId": deck.course_id,
            "topicId": deck.topic_id,
            "prepId": deck.prep_id,
            "cardCount": card_count,
            "dueCount": due_count,
            "createdAt": deck.created_at,
            "updatedAt": deck.updated_at,
        }
        for deck, card_count, due_count in rows
    ]


async def list_deck_flashcards(*, user_id: str, deck_id: str) -> list[Any]:
    """List flashcards in a specific deck."""
    return await repo.list_deck_flashcards(deck_id, user_id)
