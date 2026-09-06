"""Produce one canonical review-due reminder per deck that has flashcards due.

Until now nothing announced that flashcards were waiting: the due queue was only visible if the
learner opened the app and looked. This sweep turns "you have 12 cards due in Biology" into a
notification, and — because it is keyed on the deck — it gives a completed review something to be
attributed to. When `review_flashcard` grades a card, `record_action(deck)` maps it back to the
reminder that pointed at that deck, so the outcome funnel can finally say whether a review reminder
led to a review.

Shape mirrors `progress.schedule_reminders`: a sweep that finds due work and calls
`create_notification` per unit, leaving channels and timing to the orchestrator. Two deliberate
choices:

- **Keyed on the deck, not the card.** A card has no page of its own, the deck is the routable
  entity every client already opens for `OPEN_REVIEW`, and it is the key `review_flashcard` records
  its grade under — so the reminder and the completion agree on `("deck", deck_id)`. Cards with no
  deck are skipped: there is nowhere to send the learner and nothing to attribute against.
- **Plus-gated, like schedule reminders.** A proactive reminder is a paid feature here (Decision B
  routes every "is this learner paid" question through `entitlement_service`), and this is the same
  kind of reminder as the study-session one, so it uses the same gate rather than inventing a
  second answer. In-app review reminders for the free tier would be a separate product decision.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from src.domains.identity.db_models import User
from src.domains.personal_learning.db_models import Flashcard, FlashcardDeck
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)


async def _eligible(user_id: str) -> bool:
    """Whether this learner gets proactive reminders at all — the paid-plan gate.

    Resolved once per learner per sweep and cached by the caller. Channel consent is not checked
    here; the orchestrator owns it and rechecks it at send time.
    """

    from src.domains.billing.services import entitlement_service

    entitlement = await entitlement_service.resolve(user_id)
    return entitlement.tier == "plus"


async def send_review_due_reminders() -> dict[str, int]:
    """Create one `learning.review_due` per deck with due cards, for eligible learners.

    Returns counts so the task can log an empty run distinctly from a broken one.
    """

    from src.domains.notifications.service import create_notification

    now = datetime.now(UTC)
    # One reminder per deck per UTC day. The idempotency key makes a re-run within the day a replay,
    # and the day boundary lets the next day's due work re-announce. UTC rather than per-learner
    # local day for simplicity; the notification's own quiet-hours handling decides when it shows,
    # and being in-app-only it is low-stakes if a learner near the boundary sees it an hour early.
    day = now.date().isoformat()

    factory = get_session_factory()
    async with factory() as session:
        # Decks with at least one due card, with the deck title and owner, in one grouped scan.
        # Deckless due cards (deckId IS NULL) are excluded: no deck page, no attribution seam.
        rows = (
            await session.execute(
                select(
                    Flashcard.user_id,
                    Flashcard.deck_id,
                    FlashcardDeck.title,
                    func.count(Flashcard.id),
                    User.is_active,
                )
                .join(FlashcardDeck, FlashcardDeck.id == Flashcard.deck_id)
                .join(User, User.id == Flashcard.user_id)
                .where(Flashcard.next_review_at <= now)
                .group_by(
                    Flashcard.user_id,
                    Flashcard.deck_id,
                    FlashcardDeck.title,
                    User.is_active,
                )
                .order_by(Flashcard.user_id, Flashcard.deck_id)
            )
        ).all()

    created = 0
    skipped = 0
    failed = 0
    eligible: dict[str, bool] = {}

    for user_id, deck_id, deck_title, due_count, is_active in rows:
        if not is_active:
            skipped += 1
            continue
        if user_id not in eligible:
            eligible[user_id] = await _eligible(user_id)
        if not eligible[user_id]:
            skipped += 1
            continue

        try:
            plural = "card" if due_count == 1 else "cards"
            await create_notification(
                user_id=user_id,
                type="learning.review_due",
                title=f"{due_count} {plural} to review in {deck_title}",
                body=f"You have {due_count} {plural} due in {deck_title}. A few minutes keeps them from piling up.",
                action={"version": 1, "kind": "OPEN_REVIEW", "entityId": deck_id},
                # One live reminder per deck: the group key collapses a still-unread reminder from a
                # previous day into today's rather than stacking, and the dated idempotency key makes
                # a same-day re-run a replay.
                idempotency_key=f"review-due:{deck_id}:{day}",
                group_key=f"review-due:{deck_id}",
                priority=4,
                source_domain="personal_learning",
                source_entity_type="deck",
                source_entity_id=deck_id,
            )
            created += 1
        except Exception:
            # One bad deck must not stop the sweep.
            failed += 1
            logger.exception("Failed to create review-due reminder for deck %s", deck_id)

    summary: dict[str, Any] = {
        "consideredDecks": len(rows),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }
    if rows:
        logger.info("Review-due reminders: %s", summary)
    return summary
