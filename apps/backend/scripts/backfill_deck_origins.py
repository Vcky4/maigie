"""Give existing unfiled generated flashcards a deck.

Generation used to create cards with ``deckId = NULL``. That is the one state the
flashcards dashboard cannot render: its deck list is a ``LEFT JOIN`` *from*
``FlashcardDeck``, so a card with no ``deckId`` matches no row, while the header counts
read straight from ``Flashcard`` and did include it. Learners saw cards due with no deck
holding them.

Migration 037 and the generation changes fix this going forward. This script fixes what
is already in the database, by grouping unfiled cards on the provenance they already
carry — ``(sourceType, sourceId)`` — and filing each group into the deck for that origin,
creating it through the same ``ensure_deck_for_origin`` the live code uses.

Deliberately a script and not part of migration 037. It writes a potentially large number
of rows, it resolves titles from three other tables, and it is the kind of change worth
reading a plan for before running. A schema migration that also rewrote data would offer
no dry run and no way to do half of it.

**Only cards with a `sourceType` are touched.** A card the learner wrote by hand can also
be unfiled — deleting a deck detaches its cards on purpose — and inventing a deck for
those would be filing someone's work somewhere they did not choose. Generated cards never
got a choice, which is what makes them ours to place.

Idempotent. Re-running finds nothing, because the cards it filed are no longer unfiled,
and ``ensure_deck_for_origin`` reuses a deck it already created.

Usage::

    python scripts/backfill_deck_origins.py                    # dry run, all learners
    python scripts/backfill_deck_origins.py --apply            # write
    python scripts/backfill_deck_origins.py --user-id abc123   # one learner
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domains.knowledge.db_models import Topic  # noqa: E402
from src.domains.personal_learning.db_models import (  # noqa: E402
    ExamPrep,
    Flashcard,
    FlashcardDeck,
    Note,
    StudyPlan,
    StudyPlanItem,
)
from src.domains.personal_learning.services import flashcard_service  # noqa: E402
from src.shared.database.session import connect_db, get_session_factory  # noqa: E402

#: A group of unfiled cards that share an origin, and where they should go.
#: ``origin_type``/``origin_id`` are ``None`` for a group we decline to place.
Plan = dict[str, Any]


def decide_placement(
    source_type: str,
    source_id: str | None,
    *,
    note_titles: dict[str, str],
    topic_titles: dict[str, str],
    prep_titles: dict[str, str],
    live_decks: set[str],
    item_to_review_deck: dict[str, str],
) -> dict[str, Any]:
    """Where one group of cards belongs, decided from already-resolved lookups.

    Pure, and separate from the query that feeds it, because this is the part with
    judgement in it — six source kinds, three of which can legitimately decline — and it
    is worth being able to test the whole table without a database.

    Returns the placement fields of a ``Plan``. A ``skip_reason`` means the cards stay
    unfiled, which is a real outcome rather than a failure: some of these groups point at
    things the learner has since deleted.
    """
    result: dict[str, Any] = {
        "origin_type": None,
        "origin_id": None,
        "deck_title": None,
        "existing_deck_id": None,
        "skip_reason": None,
    }

    if not source_id:
        result["skip_reason"] = "no sourceId to group on"
        return result

    if source_type == "note":
        title = note_titles.get(source_id)
        if title is None:
            result["skip_reason"] = "note no longer exists"
        else:
            result["origin_type"] = flashcard_service.DECK_ORIGIN_NOTE
            result["origin_id"] = source_id
            result["deck_title"] = f"{title} — cards"
        return result

    if source_type == "topic":
        title = topic_titles.get(source_id)
        if title is None:
            result["skip_reason"] = "topic no longer exists"
        else:
            result["origin_type"] = flashcard_service.DECK_ORIGIN_TOPIC
            result["origin_id"] = source_id
            result["deck_title"] = f"{title} — cards"
        return result

    if source_type == "auto_setup":
        subject = prep_titles.get(source_id)
        if subject is not None:
            result["origin_type"] = flashcard_service.DECK_ORIGIN_PREP
            result["origin_id"] = source_id
            result["deck_title"] = f"{subject} — starter cards"
        else:
            # Legacy row: `sourceId` is the subject the learner typed, not an id. Filed
            # under the text rather than title-matched against their preparations, which
            # would be a guess presented as a fact.
            result["origin_type"] = flashcard_service.DECK_ORIGIN_SUBJECT
            result["origin_id"] = source_id
            result["deck_title"] = f"{source_id} — starter cards"
        return result

    if source_type == "deck_starter":
        # These name the deck they were made for. If it still exists they simply go home.
        if source_id in live_decks:
            result["existing_deck_id"] = source_id
            result["deck_title"] = "(its original deck)"
        else:
            result["skip_reason"] = "the deck these were made for was deleted"
        return result

    if source_type == "study_plan_item":
        # Routed through the plan's own `reviewDeckId` rather than given a new origin
        # kind, so study plans keep one mechanism for this instead of two.
        review_deck = item_to_review_deck.get(source_id)
        if review_deck:
            result["existing_deck_id"] = review_deck
            result["deck_title"] = "(the plan's review deck)"
        else:
            result["skip_reason"] = "plan has no review deck"
        return result

    result["skip_reason"] = f"unrecognised sourceType {source_type!r}"
    return result


async def _resolve_titles(session: Any, kind: str, ids: set[str]) -> dict[str, str]:
    """Look up display titles for one source kind in a single query."""
    if not ids:
        return {}
    if kind == "note":
        rows = (await session.execute(select(Note.id, Note.title).where(Note.id.in_(ids)))).all()
    elif kind == "topic":
        rows = (await session.execute(select(Topic.id, Topic.title).where(Topic.id.in_(ids)))).all()
    elif kind == "prep":
        rows = (
            await session.execute(select(ExamPrep.id, ExamPrep.subject).where(ExamPrep.id.in_(ids)))
        ).all()
    else:
        return {}
    return {row[0]: row[1] for row in rows if row[1]}


async def build_plan(user_id: str | None) -> list[Plan]:
    """Decide, without writing anything, where every unfiled generated card should go."""
    factory = get_session_factory()
    async with factory() as session:
        conditions = [Flashcard.deck_id.is_(None), Flashcard.source_type.is_not(None)]
        if user_id:
            conditions.append(Flashcard.user_id == user_id)

        rows = (
            await session.execute(
                select(
                    Flashcard.id,
                    Flashcard.user_id,
                    Flashcard.source_type,
                    Flashcard.source_id,
                ).where(*conditions)
            )
        ).all()

        # Group first, so titles are resolved once per source rather than once per card.
        grouped: dict[tuple[str, str, str | None], list[str]] = defaultdict(list)
        for card_id, owner, source_type, source_id in rows:
            grouped[(owner, source_type, source_id)].append(card_id)

        note_ids = {sid for (_, st, sid) in grouped if st == "note" and sid}
        topic_ids = {sid for (_, st, sid) in grouped if st == "topic" and sid}
        # `auto_setup` carries either a preparation id (current) or a subject string
        # (legacy). Which one it is decides the origin, so both are looked up.
        auto_ids = {sid for (_, st, sid) in grouped if st == "auto_setup" and sid}
        starter_deck_ids = {sid for (_, st, sid) in grouped if st == "deck_starter" and sid}
        plan_item_ids = {sid for (_, st, sid) in grouped if st == "study_plan_item" and sid}

        note_titles = await _resolve_titles(session, "note", note_ids)
        topic_titles = await _resolve_titles(session, "topic", topic_ids)
        prep_titles = await _resolve_titles(session, "prep", auto_ids)

        # Deck-starter cards name the deck they were made for. If it still exists the
        # cards simply go home; if the learner deleted it, they deleted it deliberately.
        live_decks: set[str] = set()
        if starter_deck_ids:
            live_decks = {
                row[0]
                for row in (
                    await session.execute(
                        select(FlashcardDeck.id).where(FlashcardDeck.id.in_(starter_deck_ids))
                    )
                ).all()
            }

        # Plan-item cards belong in the plan's review deck, which the plan already
        # points at. Resolved through the plan rather than given a new origin kind, so
        # study plans keep one mechanism instead of two.
        item_to_review_deck: dict[str, str] = {}
        if plan_item_ids:
            item_rows = (
                await session.execute(
                    select(StudyPlanItem.id, StudyPlan.review_deck_id)
                    .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
                    .where(StudyPlanItem.id.in_(plan_item_ids))
                )
            ).all()
            item_to_review_deck = {row[0]: row[1] for row in item_rows if row[1]}

    plans: list[Plan] = []
    for (owner, source_type, source_id), card_ids in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")
    ):
        entry: Plan = {
            "user_id": owner,
            "source_type": source_type,
            "source_id": source_id,
            "card_ids": card_ids,
            **decide_placement(
                source_type,
                source_id,
                note_titles=note_titles,
                topic_titles=topic_titles,
                prep_titles=prep_titles,
                live_decks=live_decks,
                item_to_review_deck=item_to_review_deck,
            ),
        }
        plans.append(entry)

    return plans


async def apply_plan(plans: list[Plan]) -> tuple[int, int]:
    """File each group. Returns ``(cards_filed, decks_touched)``."""
    factory = get_session_factory()
    cards_filed = 0
    decks: set[str] = set()

    for entry in plans:
        if entry["skip_reason"]:
            continue

        deck_id = entry["existing_deck_id"]
        if deck_id is None:
            deck_id = await flashcard_service.ensure_deck_for_origin(
                user_id=entry["user_id"],
                origin_type=entry["origin_type"],
                origin_id=entry["origin_id"],
                title=entry["deck_title"],
                description="Cards generated before Maigie filed them automatically.",
            )

        async with factory() as session:
            # `deckId IS NULL` is repeated in the predicate so a card filed by a
            # concurrent generation is not moved out from under it.
            await session.execute(
                update(Flashcard)
                .where(
                    Flashcard.id.in_(entry["card_ids"]),
                    Flashcard.user_id == entry["user_id"],
                    Flashcard.deck_id.is_(None),
                )
                .values(deck_id=deck_id)
            )
            await session.commit()

        cards_filed += len(entry["card_ids"])
        decks.add(deck_id)

    return cards_filed, len(decks)


def report(plans: list[Plan]) -> None:
    placeable = [entry for entry in plans if not entry["skip_reason"]]
    skipped = [entry for entry in plans if entry["skip_reason"]]

    if not plans:
        print("No unfiled generated flashcards. Nothing to do.")
        return

    print(f"{len(plans)} group(s) of unfiled generated cards\n")

    if placeable:
        print("Will be filed:")
        for entry in placeable:
            origin = (
                f"{entry['origin_type']}:{entry['origin_id']}"
                if entry["origin_type"]
                else "existing deck"
            )
            print(
                f"  {len(entry['card_ids']):4d} card(s)  {entry['source_type']:<16}"
                f"  -> {entry['deck_title']}  [{origin}]"
            )
        print(f"\n  {sum(len(e['card_ids']) for e in placeable)} card(s) total")

    if skipped:
        print("\nLeft unfiled:")
        for entry in skipped:
            print(
                f"  {len(entry['card_ids']):4d} card(s)  {entry['source_type']:<16}"
                f"  -- {entry['skip_reason']}"
            )
        print(f"\n  {sum(len(e['card_ids']) for e in skipped)} card(s) total")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the script only prints its plan.",
    )
    parser.add_argument("--user-id", default=None, help="Restrict to one learner.")
    args = parser.parse_args()

    # The engine is normally created during app startup. A standalone script has no
    # startup, so it opens the pool itself.
    await connect_db()

    plans = await build_plan(args.user_id)
    report(plans)

    if not args.apply:
        if any(not entry["skip_reason"] for entry in plans):
            print("\nDry run. Re-run with --apply to make these changes.")
        return 0

    placeable = [entry for entry in plans if not entry["skip_reason"]]
    if not placeable:
        print("\nNothing to apply.")
        return 0

    print("\nApplying...")
    cards_filed, deck_count = await apply_plan(plans)
    print(f"Filed {cards_filed} card(s) into {deck_count} deck(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
