"""
Flashcard service — user-created and AI-generated flashcards with SM-2 spaced repetition.

Learners retain knowledge long-term with minimal effort through intelligent
spaced repetition scheduling.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from src.shared.time.learner_timezone import (
    UNKNOWN_TIMEZONE,
    LearnerTimezone,
    to_learner_local,
)

from .. import models
from ..repository import personal_learning_repo as repo
from .cache import cached as _cached

logger = logging.getLogger(__name__)

#: Seconds of work a single due card represents.
#:
#: Lives here rather than in a dashboard service because it is a fact about
#: flashcards, and two surfaces quote it. The Learn dashboard imports it from here so
#: that "about 12 minutes" cannot mean one thing on `/learn` and another on
#: `/flashcards`.
REVIEW_SECONDS_PER_CARD = 30

#: Quality at or above which a review counts as recalled rather than lapsed. The
#: client's rating buttons are mapped against this threshold, so it is part of the
#: contract rather than an implementation detail.
LAPSE_QUALITY_THRESHOLD = 3

#: Share of a deck's cards that must be mature before the deck reads as "strong".
STRONG_DECK_MASTERY_PERCENT = 80

#: Window used for "mastery changed by N points". A week, because that is the period
#: the surface labels it with.
MASTERY_CHANGE_WINDOW_DAYS = 7

#: How many days of review history the insight rules may consider.
INSIGHT_WINDOW_DAYS = 30

#: Lapses before a card is worth calling out as one the learner keeps forgetting.
LAPSING_CARD_THRESHOLD = 3


class DeckNotFound(Exception):
    """A deck was referenced that the caller does not own, or that does not exist.

    One exception for both cases on purpose. Distinguishing them would let a caller
    discover that a deck id exists by the difference in the error, so routes render
    this as a single `404`.
    """


async def _require_own_deck(user_id: str, deck_id: str | None) -> None:
    """Refuse a write that files a card into a deck the caller does not own.

    Card creation previously took ``deckId`` on trust and wrote it straight to the
    column. The foreign key only checks that *some* deck has that id, so a learner
    could file cards into another learner's deck; the card stayed theirs, but it
    appeared in someone else's deck listing and counted towards that deck's totals.
    """
    if deck_id is None:
        return
    if await repo.get_deck(deck_id, user_id) is None:
        raise DeckNotFound(deck_id)


async def create_flashcard(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Create a flashcard with SM-2 initialization.

    Req 5.1: Initialize with interval=1, repetition=0, ease_factor=2.5.
    """
    await _require_own_deck(user_id, data.get("deckId"))
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


async def generate_from_note(
    *, user_id: str, note_id: str, deck_id: str | None = None
) -> list[Any]:
    """
    Generate flashcards from a note using AI.

    Req 5.2: Extract key concepts from note content and create flashcards.

    FREE: up to 5 basic Q&A cards.
    PLUS: up to 10 cards with varied types (cloze, multi-choice, image prompts).
    """
    from . import feature_tier_service, trial_service
    from .llm_resilient import generate_content_json

    # Checked before the model call, not after: generating cards and then discovering
    # they cannot be filed would waste a paid request and leave them unfiled.
    await _require_own_deck(user_id, deck_id)

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
                    "deckId": deck_id,
                    "sourceType": "note",
                    "sourceId": note_id,
                },
            )
            created_cards.append(flashcard)

    return created_cards


async def generate_from_topic(
    *, user_id: str, topic_id: str, deck_id: str | None = None
) -> list[Any]:
    """
    Generate flashcards from a topic using AI.

    Req 5.3: Generate flashcards based on topic content and materials.
    """
    from sqlalchemy import select as sa_select

    from src.domains.knowledge.db_models import Topic
    from src.shared.database import get_session_factory

    from .llm_resilient import generate_content_json

    await _require_own_deck(user_id, deck_id)

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
                    "deckId": deck_id,
                    "sourceType": "topic",
                    "sourceId": topic_id,
                },
            )
            created_cards.append(flashcard)

    return created_cards


async def generate_deck_starter_cards(*, user_id: str, deck_id: str) -> list[Any]:
    """Generate a deck's first cards from the intent the learner already described.

    The create wizard offers a "guided starter" that promised six example cards. A
    fixed template would satisfy the promise literally and be worthless — six cards
    reading "What is the central idea?" are not cards about anything. The deck's own
    title, subject and learning goal are the only description of intent that exists,
    so they are what the cards are generated from.

    Returns an empty list rather than raising when the model produces nothing usable.
    The deck still exists and is still usable by hand; failing the request would imply
    the deck was not created.
    """
    from .llm_resilient import generate_content_json

    deck = await repo.get_deck(deck_id, user_id)
    if deck is None:
        raise DeckNotFound(deck_id)

    prompt = (
        "Create starter flashcards for a learner's new deck.\n"
        f"Deck title: {deck.title}\n"
        f"Subject: {deck.subject or 'unspecified'}\n"
        f"Learning goal: {deck.description or 'unspecified'}\n\n"
        "Each card must test one specific idea, not a general prompt about the topic.\n"
        "Return ONLY a JSON array of 6 objects with 'front' and 'back' fields."
    )
    cards_data = await generate_content_json(prompt, max_tokens=1500, fallback=[], user_id=user_id)
    if not cards_data:
        return []

    created_cards = []
    for card in cards_data[:6]:
        if isinstance(card, dict) and card.get("front") and card.get("back"):
            created_cards.append(
                await create_flashcard(
                    user_id=user_id,
                    data={
                        "front": card["front"],
                        "back": card["back"],
                        "deckId": deck_id,
                        # Provenance recorded by the server, so a generated card is
                        # distinguishable from one the learner wrote.
                        "sourceType": "deck_starter",
                        "sourceId": deck_id,
                    },
                )
            )
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

    if quality < LAPSE_QUALITY_THRESHOLD:
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

    # The schedule change and its log row share one transaction. The card row keeps
    # only the latest review, so if the log write could fail independently the
    # learner's history would develop holes that nothing could later detect — and
    # streaks derived from it would silently understate what they did.
    result = await repo.apply_flashcard_review(
        card_id,
        user_id,
        card_update=update_data,
        review={
            # The deck the grade was earned in, not the deck the card may be moved
            # to later.
            "deckId": card.deck_id,
            "quality": quality,
            "intervalDays": new_interval,
            "easeFactor": round(new_ease, 4),
            "repetitionCount": new_repetition,
            "wasLapse": quality < LAPSE_QUALITY_THRESHOLD,
            "reviewedAt": now,
        },
    )
    if result is None:
        return None

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

    # Counted from the review log. This used to read `total - due_today`, which is not
    # a review count at all: it treats every card that is not currently due as having
    # been reviewed exactly once, so a learner who reviewed one card fifty times
    # scored 1 and a learner who created fifty cards with a future first review scored
    # 50 without reviewing anything.
    stats = await repo.get_flashcard_stats(user_id)
    await milestone_service.check_milestones(
        user_id, {"total_flashcard_reviews": stats.get("reviewed_total", 0)}
    )

    return result


async def get_due_flashcards(
    *, user_id: str, limit: int | None = None, deck_id: str | None = None
) -> list[Any]:
    """
    Get flashcards due for review.

    Req 5.5: Return cards where next_review_at <= now, ordered by urgency (most overdue first).

    ``limit`` bounds a session, and ``deck_id`` scopes it to one deck so the deck page
    can start a review without pulling the learner's whole queue. Both default to the
    previous behaviour — every due card, every deck.
    """
    return await repo.list_due_flashcards(user_id, limit=limit, deck_id=deck_id)


async def get_statistics(
    *, user_id: str, deck_id: str | None = None, timezone_name: str | None = None
) -> dict[str, Any]:
    """
    Get flashcard statistics.

    Req 5.8: Return total, due_today, mastered, learning, new, average ease, recall,
    and review-history counts.

    Only the unscoped, UTC reading is cached. A deck-scoped or zone-specific reading
    goes straight to the database, because this cache keys on user id alone: caching
    those variants under the same key would serve one deck's numbers for another
    deck's request. Widening the key would mean review invalidation could no longer
    clear every variant, which is the worse failure.
    """
    if deck_id is not None or timezone_name is not None:
        return _shape_statistics(
            await repo.get_flashcard_stats(user_id, deck_id=deck_id, timezone_name=timezone_name)
        )
    return await _get_statistics_cached(user_id=user_id)


def _shape_statistics(stats: dict[str, Any]) -> dict[str, Any]:
    """Rename repository keys to the wire contract. Pure, so it is trivially testable."""
    return {
        "total": stats["total"],
        "dueToday": stats["due_today"],
        "masteredCount": stats["mastered_count"],
        "learningCount": stats["learning_count"],
        "newCount": stats["new_count"],
        "averageEaseFactor": stats["avg_ease_factor"],
        "recallPercent": stats["recall_percent"],
        "reviewedCardCount": stats["reviewed_card_count"],
        "reviewedTotal": stats["reviewed_total"],
        "reviewedThisWeek": stats["reviewed_this_week"],
        "activeDaysThisWeek": stats["active_days_this_week"],
        "currentStreak": stats["current_streak"],
    }


@_cached(ttl_seconds=60, max_size=1000, key_arg="user_id")
async def _get_statistics_cached(*, user_id: str) -> dict[str, Any]:
    """Cached inner implementation."""
    return _shape_statistics(await repo.get_flashcard_stats(user_id))


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------


def deck_status(*, card_count: int, due_count: int, mastered_count: int) -> str:
    """Classify a deck as ``due``, ``strong`` or ``learning``.

    Derived on the server so every surface answers the question the same way. Order
    matters: anything due outranks how mature the deck is, because a strong deck with
    work waiting is still work waiting. An empty deck is ``learning`` rather than
    ``strong`` — zero of zero cards mature is not mastery.
    """
    if due_count > 0:
        return "due"
    if (
        card_count > 0
        and mastery_percent(mastered_count, card_count) >= STRONG_DECK_MASTERY_PERCENT
    ):
        return "strong"
    return "learning"


def mastery_percent(mastered: int, total: int) -> int:
    """Mastered share of a set of cards, 0-100. Zero cards is 0, not a division error."""
    if total <= 0:
        return 0
    return max(0, min(100, round(mastered / total * 100)))


def _deck_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Map one aggregate row onto the deck wire contract."""
    deck = row["deck"]
    card_count = row["card_count"]
    mastered = row["mastered_count"]
    return {
        "id": deck.id,
        "userId": deck.user_id,
        "title": deck.title,
        "description": deck.description,
        "subject": deck.subject,
        "accent": deck.accent,
        "dailyGoal": deck.daily_goal,
        "courseId": deck.course_id,
        "topicId": deck.topic_id,
        "prepId": deck.prep_id,
        "cardCount": card_count,
        "dueCount": row["due_count"],
        "masteredCount": mastered,
        "reviewedCount": row["reviewed_count"],
        "recallPercent": row["recall_percent"],
        "masteryPercent": mastery_percent(mastered, card_count),
        "lastReviewedAt": row["last_reviewed_at"],
        "nextReviewAt": row["next_review_at"],
        "status": deck_status(
            card_count=card_count,
            due_count=row["due_count"],
            mastered_count=mastered,
        ),
        "createdAt": deck.created_at,
        "updatedAt": deck.updated_at,
    }


def _empty_deck_payload(deck: Any) -> dict[str, Any]:
    """The contract for a deck with no cards, without querying aggregates for it."""
    return _deck_payload(
        {
            "deck": deck,
            "card_count": 0,
            "due_count": 0,
            "mastered_count": 0,
            "reviewed_count": 0,
            "last_reviewed_at": None,
            "next_review_at": None,
            "recall_percent": None,
        }
    )


async def create_deck(*, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create a flashcard deck."""
    deck = await repo.create_deck({"userId": user_id, **data})
    # A new deck has no cards, so every aggregate is known to be empty without a read.
    return _empty_deck_payload(deck)


async def list_decks(*, user_id: str) -> list[dict[str, Any]]:
    """List the learner's decks with every per-deck figure their library card shows."""
    rows = await repo.list_decks_with_stats(user_id)
    return [_deck_payload(row) for row in rows]


async def get_deck(*, user_id: str, deck_id: str) -> dict[str, Any] | None:
    """One deck with its aggregates, or ``None`` when it is not the caller's."""
    rows = await repo.list_decks_with_stats(user_id, deck_id=deck_id)
    return _deck_payload(rows[0]) if rows else None


async def update_deck(*, user_id: str, deck_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Rename or relabel a deck. Returns ``None`` when it is not the caller's."""
    deck = await repo.update_deck(deck_id, user_id, data)
    if deck is None:
        return None
    return await get_deck(user_id=user_id, deck_id=deck_id)


async def delete_deck(*, user_id: str, deck_id: str) -> bool:
    """Delete a deck, detaching rather than destroying its cards.

    See ``repository.delete_deck`` for why detaching is the chosen semantic.
    """
    deleted = await repo.delete_deck(deck_id, user_id)
    if deleted:
        # Cards did not disappear, but their deck membership changed, and the cached
        # stats include per-deck-independent counts that a caller may re-read
        # immediately after this returns.
        await _get_statistics_cached.invalidate(user_id=user_id)
    return deleted


async def list_deck_flashcards(*, user_id: str, deck_id: str) -> list[Any]:
    """List flashcards in a specific deck."""
    return await repo.list_deck_flashcards(deck_id, user_id)


# ---------------------------------------------------------------------------
# Cards — read, update, delete
# ---------------------------------------------------------------------------


async def list_flashcards(
    *,
    user_id: str,
    deck_id: str | None = None,
    search: str | None = None,
    source_type: str | None = None,
    state: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """A page of the learner's cards, with the total for the same filters."""
    return await repo.list_flashcards(
        user_id,
        deck_id=deck_id,
        search=search,
        source_type=source_type,
        state=state,
        skip=(page - 1) * page_size,
        take=page_size,
    )


async def get_flashcard(*, user_id: str, card_id: str) -> Any | None:
    return await repo.get_flashcard(card_id, user_id)


async def update_flashcard(*, user_id: str, card_id: str, data: dict[str, Any]) -> Any | None:
    """Edit a card's text, or move it between decks.

    SM-2 state is deliberately not editable. Letting a client post an interval or an
    ease factor would put a second scheduler in the browser, which is the same reason
    the review page stopped predicting intervals locally.
    """
    if "deckId" in data:
        await _require_own_deck(user_id, data["deckId"])
    card = await repo.update_flashcard_fields(card_id, user_id, data)
    if card is not None and "deckId" in data:
        await _get_statistics_cached.invalidate(user_id=user_id)
    return card


async def delete_flashcard(*, user_id: str, card_id: str) -> bool:
    """Delete a card. Its review rows survive, detached — the reviews still happened."""
    deleted = await repo.delete_flashcard(card_id, user_id)
    if deleted:
        await _get_statistics_cached.invalidate(user_id=user_id)
    return deleted


# ---------------------------------------------------------------------------
# Flashcards dashboard — composed read
# ---------------------------------------------------------------------------


def _weekday_label(value: date) -> str:
    return value.strftime("%a")


def _bucket_by_deck_and_day(
    events: list[dict[str, Any]],
    *,
    timestamp_key: str,
    learner_timezone: LearnerTimezone,
) -> dict[tuple[date, str | None], dict[str, Any]]:
    """Collect events into one bucket per deck per local calendar day."""
    buckets: dict[tuple[date, str | None], dict[str, Any]] = {}
    for event in events:
        occurred = event.get(timestamp_key)
        if occurred is None:
            continue
        local_day = to_learner_local(occurred, learner_timezone).date()
        bucket = buckets.setdefault(
            (local_day, event.get("deck_id")),
            {"occurred_at": occurred, "card_ids": set(), "rows": []},
        )
        if occurred > bucket["occurred_at"]:
            bucket["occurred_at"] = occurred
        if event.get("flashcard_id"):
            bucket["card_ids"].add(event["flashcard_id"])
        bucket["rows"].append(event)
    return buckets


def group_activity(
    events: list[dict[str, Any]],
    *,
    deck_titles: dict[str, str],
    graduations: list[dict[str, Any]] | None = None,
    creations: list[dict[str, Any]] | None = None,
    learner_timezone: LearnerTimezone = UNKNOWN_TIMEZONE,
    limit: int,
) -> list[models.FlashcardActivityEntry]:
    """Build the activity feed: reviews, graduations and card creations, newest first.

    A "session" is not persisted and was never observed: the server sees grades
    arriving, not a sitting starting and ending. Grouping by deck and calendar day
    claims only what the rows support, and distinct cards are counted rather than
    grades so that re-grading one card does not read as reviewing two.

    Three kinds rather than one because progress is not only review. A learner who
    spent the week writing cards, or who finally pushed a hard deck to maturity, did
    something the feed should show; reporting reviews alone would leave it blank for
    them.
    """
    entries: list[models.FlashcardActivityEntry] = []

    def deck_title(deck_id: str | None) -> str | None:
        return deck_titles.get(deck_id) if deck_id else None

    for (local_day, deck_id), bucket in _bucket_by_deck_and_day(
        events, timestamp_key="reviewed_at", learner_timezone=learner_timezone
    ).items():
        rows = bucket["rows"]
        qualities = [row["quality"] for row in rows]
        # A card deleted since its review leaves a row with no card id, so the grade
        # count is the floor for how many cards were involved.
        card_count = len(bucket["card_ids"]) or len(rows)
        entries.append(
            models.FlashcardActivityEntry(
                id=f"reviewed:{local_day.isoformat()}:{deck_id or 'unfiled'}",
                kind="reviewed",
                deck_id=deck_id,
                deck_title=deck_title(deck_id),
                occurred_at=bucket["occurred_at"],
                card_count=card_count,
                recall_percent=(
                    round(sum(qualities) / len(qualities) / 5 * 100) if qualities else None
                ),
                lapse_count=sum(1 for row in rows if row["was_lapse"]),
            )
        )

    for kind, rows in (("graduated", graduations or []), ("created", creations or [])):
        for (local_day, deck_id), bucket in _bucket_by_deck_and_day(
            rows, timestamp_key="occurred_at", learner_timezone=learner_timezone
        ).items():
            entries.append(
                models.FlashcardActivityEntry(
                    id=f"{kind}:{local_day.isoformat()}:{deck_id or 'unfiled'}",
                    kind=kind,
                    deck_id=deck_id,
                    deck_title=deck_title(deck_id),
                    occurred_at=bucket["occurred_at"],
                    card_count=len(bucket["card_ids"]) or len(bucket["rows"]),
                    # Neither graduating nor creating a card produces a recall figure.
                    recall_percent=None,
                    lapse_count=0,
                )
            )

    entries.sort(key=lambda entry: entry.occurred_at, reverse=True)
    return entries[:limit]


def choose_insight(
    *,
    events: list[dict[str, Any]],
    learner_timezone: LearnerTimezone = UNKNOWN_TIMEZONE,
    due_today: int,
    overdue: int,
    total_cards: int,
    lapsing_cards: int,
) -> models.FlashcardInsight:
    """Pick the one true thing most worth saying, by a fixed ladder.

    Every branch reports counts or percentages taken from persisted rows. Nothing here
    is generated prose, and there is no fallback that asserts a pattern the data does
    not show — the last rung is a plain summary of the library.

    The time-of-day rung is skipped when the learner's zone was never captured, even
    if the data would support it. "You recall best in the morning" is a claim about
    their morning, and with an assumed zone it could be a claim about someone else's.
    """
    bands = (
        ("morning", range(5, 12), "in the morning"),
        ("afternoon", range(12, 18), "in the afternoon"),
        ("evening", list(range(18, 24)) + list(range(0, 5)), "in the evening"),
    )

    if learner_timezone.is_known and len(events) >= 20:
        by_band: dict[str, list[int]] = {name: [] for name, _, _ in bands}
        for event in events:
            hour = to_learner_local(event["reviewed_at"], learner_timezone).hour
            for name, hours, _ in bands:
                if hour in hours:
                    by_band[name].append(event["quality"])
                    break
        overall = sum(event["quality"] for event in events) / len(events) / 5 * 100
        candidates = [
            (name, sum(values) / len(values) / 5 * 100, len(values))
            for name, values in by_band.items()
            if len(values) >= 8
        ]
        if candidates:
            name, recall, count = max(candidates, key=lambda item: item[1])
            if recall - overall >= 5:
                phrase = next(text for band, _, text in bands if band == name)
                return models.FlashcardInsight(
                    kind="best_time_of_day",
                    title=f"Your recall is strongest {phrase}",
                    body=(
                        f"Across {count} reviews {phrase}, you recalled "
                        f"{round(recall)}% against {round(overall)}% overall. "
                        "Scheduling the harder decks then plays to that."
                    ),
                    action_label="Start a review",
                )

    if overdue > 0:
        return models.FlashcardInsight(
            kind="overdue_backlog",
            title=(
                f"{overdue} card slipped past its date"
                if overdue == 1
                else f"{overdue} cards slipped past their date"
            ),
            body=(
                "Overdue cards decay fastest, so clearing them first recovers the most "
                "for the time spent."
            ),
            action_label="Clear overdue cards",
        )

    if due_today > 0:
        minutes = max(1, (due_today * REVIEW_SECONDS_PER_CARD + 59) // 60)
        return models.FlashcardInsight(
            kind="due_now",
            title=("1 card is ready now" if due_today == 1 else f"{due_today} cards are ready now"),
            body=f"About {minutes} {'minute' if minutes == 1 else 'minutes'} of review keeps this schedule intact.",
            action_label="Start review",
        )

    if lapsing_cards > 0:
        return models.FlashcardInsight(
            kind="lapsing_cards",
            title=f"{lapsing_cards} {'card keeps' if lapsing_cards == 1 else 'cards keep'} slipping",
            body=(
                f"These have lapsed {LAPSING_CARD_THRESHOLD} or more times. Rewriting the "
                "prompt to test one idea usually works better than reviewing it again."
            ),
            action_label="Review your decks",
        )

    if total_cards == 0:
        return models.FlashcardInsight(
            kind="empty_library",
            title="Nothing to review yet",
            body="Cards work best when each prompt tests one clear idea. Add a few and scheduling starts on its own.",
            action_label="Create a deck",
        )

    return models.FlashcardInsight(
        kind="library_summary",
        title="Nothing is due right now",
        body=(
            f"All {total_cards} {'card' if total_cards == 1 else 'cards'} are scheduled ahead. "
            "The forecast below shows when the next ones come back."
        ),
        action_label="Browse your decks",
    )


def _log_source_failure(user_id: str, source: str, error: BaseException) -> None:
    logger.warning(
        "Flashcards dashboard source unavailable",
        extra={"user_id": user_id, "source": source},
        exc_info=(type(error), error, error.__traceback__),
    )


async def get_dashboard(
    *, user_id: str, forecast_days: int, activity_limit: int, mastery_limit: int
) -> models.FlashcardsDashboardResponse:
    """Everything the flashcards page renders, in one bounded request.

    Composed the same way as the Learn dashboard: independent sources fetched
    concurrently, a failing source degrading its own sections rather than the page,
    and a total failure surfacing as `503` instead of a page of zeros. Zeros are
    indistinguishable from an empty account, which is exactly the confusion this
    programme exists to remove.
    """
    from fastapi import status

    from src.shared.exceptions import MaigieError
    from src.shared.time.learner_timezone import resolve_learner_timezone

    learner_timezone = await resolve_learner_timezone(user_id)
    # Passed to SQL day-grouping as a name only when it is a fact. An assumed zone
    # falls through to UTC there, which keeps the fallback in one place instead of
    # letting each query decide.
    timezone_name = learner_timezone.name if learner_timezone.is_known else None
    now = datetime.now(UTC)
    insight_since = now - timedelta(days=INSIGHT_WINDOW_DAYS)
    mastery_cutoff = now - timedelta(days=MASTERY_CHANGE_WINDOW_DAYS)

    results = await asyncio.gather(
        get_statistics(user_id=user_id, timezone_name=timezone_name),
        repo.count_overdue_flashcards(user_id),
        list_decks(user_id=user_id),
        repo.get_review_forecast(user_id, days=forecast_days, timezone_name=timezone_name),
        repo.list_review_events(user_id, since=insight_since),
        repo.count_mastered_by_deck_as_of(user_id, cutoff=mastery_cutoff),
        repo.count_lapsing_flashcards(user_id, min_lapses=LAPSING_CARD_THRESHOLD),
        repo.list_graduation_events(user_id, since=insight_since),
        repo.list_card_creations(user_id, since=insight_since),
        return_exceptions=True,
    )
    source_names = (
        "stats",
        "overdue",
        "decks",
        "forecast",
        "events",
        "masteryHistory",
        "lapsing",
        "graduations",
        "creations",
    )
    if all(isinstance(result, BaseException) for result in results):
        for source, error in zip(source_names, results, strict=True):
            _log_source_failure(user_id, source, error)
        raise MaigieError(
            "Flashcards are temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="FLASHCARDS_DASHBOARD_UNAVAILABLE",
        )

    source_sections: dict[str, set[models.FlashcardsDashboardSection]] = {
        "stats": {"review", "stats"},
        "overdue": {"review", "insight"},
        "decks": {"decks", "deckMastery"},
        "forecast": {"forecast"},
        "events": {"activity", "insight"},
        "masteryHistory": {"deckMastery"},
        "lapsing": {"insight"},
        "graduations": {"activity"},
        "creations": {"activity"},
    }
    degraded: set[models.FlashcardsDashboardSection] = set()
    for source, result in zip(source_names, results, strict=True):
        if isinstance(result, BaseException):
            degraded.update(source_sections[source])
            _log_source_failure(user_id, source, result)

    stats: dict[str, Any] = {} if isinstance(results[0], BaseException) else results[0]
    overdue = 0 if isinstance(results[1], BaseException) else results[1]
    decks: list[dict[str, Any]] = [] if isinstance(results[2], BaseException) else results[2]
    forecast_rows: list[dict[str, Any]] = (
        [] if isinstance(results[3], BaseException) else results[3]
    )
    events: list[dict[str, Any]] = [] if isinstance(results[4], BaseException) else results[4]
    mastered_before: dict[str, int] = {} if isinstance(results[5], BaseException) else results[5]
    lapsing_cards = 0 if isinstance(results[6], BaseException) else results[6]
    graduations: list[dict[str, Any]] = [] if isinstance(results[7], BaseException) else results[7]
    creations: list[dict[str, Any]] = [] if isinstance(results[8], BaseException) else results[8]

    due_today = max(0, int(stats.get("dueToday", 0)))
    total_cards = max(0, int(stats.get("total", 0)))
    mastered_cards = max(0, int(stats.get("masteredCount", 0)))

    review = models.FlashcardReviewSummary(
        due_today=due_today,
        overdue=max(0, overdue),
        estimated_minutes=(due_today * REVIEW_SECONDS_PER_CARD + 59) // 60,
        retention_percent=stats.get("recallPercent"),
        review_streak=max(0, int(stats.get("currentStreak", 0))),
        reviewed_this_week=max(0, int(stats.get("reviewedThisWeek", 0))),
    )
    library = models.FlashcardLibraryStats(
        total_cards=total_cards,
        mastered_cards=mastered_cards,
        learning_cards=max(0, int(stats.get("learningCount", 0))),
        new_cards=max(0, int(stats.get("newCount", 0))),
        average_ease=float(stats.get("averageEaseFactor", 2.5)),
        mastered_percent=mastery_percent(mastered_cards, total_cards) if total_cards else None,
    )

    today = forecast_rows[0]["date"] if forecast_rows else None
    forecast = [
        models.FlashcardForecastDay(
            date=row["date"],
            weekday=_weekday_label(row["date"]),
            is_today=row["date"] == today,
            due=row["due"],
            new_cards=row["new"],
        )
        for row in forecast_rows
    ]

    deck_titles = {deck["id"]: deck["title"] for deck in decks}
    activity = group_activity(
        events,
        deck_titles=deck_titles,
        graduations=graduations,
        creations=creations,
        learner_timezone=learner_timezone,
        limit=activity_limit,
    )

    deck_mastery = []
    for deck in decks:
        if deck["cardCount"] == 0:
            # A deck with no cards has no mastery to rank, and including it at 0%
            # would push decks the learner is actually working on off the list.
            continue
        previous = mastered_before.get(deck["id"])
        deck_mastery.append(
            models.DeckMasterySummary(
                deck_id=deck["id"],
                title=deck["title"],
                subject=deck["subject"],
                mastery_percent=deck["masteryPercent"],
                # Null rather than 0 when the window holds no earlier reading: "no
                # change" and "no record of what it was" are different statements.
                change_percent=(
                    deck["masteryPercent"] - mastery_percent(previous, deck["cardCount"])
                    if previous is not None
                    else None
                ),
            )
        )
    deck_mastery.sort(key=lambda entry: entry.mastery_percent, reverse=True)
    deck_mastery = deck_mastery[:mastery_limit]

    insight = choose_insight(
        events=events,
        learner_timezone=learner_timezone,
        due_today=due_today,
        overdue=max(0, overdue),
        total_cards=total_cards,
        lapsing_cards=lapsing_cards,
    )

    section_order: list[models.FlashcardsDashboardSection] = [
        "review",
        "stats",
        "decks",
        "forecast",
        "activity",
        "deckMastery",
        "insight",
    ]
    return models.FlashcardsDashboardResponse(
        meta=models.FlashcardsDashboardMeta(
            generated_at=now,
            degraded_sections=[section for section in section_order if section in degraded],
            timezone=models.FlashcardsTimezoneMeta(
                name=learner_timezone.name,
                is_known=learner_timezone.is_known,
            ),
            has_review_history=bool(stats.get("reviewedTotal", 0)),
        ),
        review=review,
        stats=library,
        decks=[models.DeckResponse(**deck) for deck in decks],
        forecast=forecast,
        activity=activity,
        deck_mastery=deck_mastery,
        insight=insight,
    )
