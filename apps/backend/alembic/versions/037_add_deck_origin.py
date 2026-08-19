"""Add FlashcardDeck.originType/originId so generated cards land in a deck.

AI-generated flashcards were created with `deckId = NULL`. That put them in a state the
flashcards dashboard cannot render: `list_decks_with_stats` builds the deck list with a
`LEFT JOIN` *from* `FlashcardDeck`, so a card with no `deckId` joins to no row and is
absent from every per-deck figure — while `get_flashcard_stats` and
`count_overdue_flashcards` filter on `userId` alone and do count it. A learner who
generated cards from a note saw "5 cards due" above a deck list where nothing was due,
and no field in the payload said how many cards were unfiled.

`(originType, originId)` records what the server created a deck *for* — `('note', …)`,
`('topic', …)`, `('course', …)`, `('prep', …)` — and is the lookup key that makes
generation idempotent. Without it, "the deck for this note" is unanswerable and every
press of Generate would start a new pile.

A generic (type, id) pair rather than another nullable FK per kind: the set of things
cards get generated from is still growing, and each new kind would otherwise cost a
migration. `topicId`/`courseId`/`prepId` predate this pair and were written but never
read by anything — no query in the codebase filtered on them — so the backfill below
derives the pair from them and they stay for `DeckCreate`'s sake.

`originType IS NOT NULL` is also the provenance flag distinguishing a server-made deck
from a hand-made one, which is why no `isAuto` column is added.

The partial unique index is load-bearing, not hygiene: it is what makes the
get-or-create in `flashcard_service.ensure_deck_for_origin` safe when two generations
race for the same origin. The loser of the race takes a unique violation and re-selects
the winner's deck instead of creating a second one.

`deckId` stays nullable. Deleting a deck deliberately detaches its cards rather than
destroying them, so unfiled cards remain a legitimate state; this migration reduces how
cards *enter* that state, and does not forbid it.

Revision ID: 037_add_deck_origin
Revises: 036_add_collections
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

revision = "037_add_deck_origin"
down_revision = "036_add_collections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("FlashcardDeck", sa.Column("originType", sa.String(20), nullable=True))
    op.add_column("FlashcardDeck", sa.Column("originId", sa.String(), nullable=True))

    # Backfill from the three columns that recorded the same idea without a read path.
    #
    # Precedence topic → course → prep: the narrowest scope wins, because a deck that
    # names a topic is the deck for that topic even when it also names the course the
    # topic belongs to.
    #
    # `ROW_NUMBER` is the reason this is not a plain UPDATE. Nothing ever stopped two
    # decks from carrying the same `topicId` for one learner, so the naive statement
    # would produce duplicate origins and the unique index below would refuse to build.
    # The oldest deck in each group keeps the origin; later ones are left with a null
    # origin, which reads as "the learner made this" — the honest answer, since we
    # cannot tell which of two same-topic decks the server would have created.
    op.execute(
        sa.text(
            """
            WITH derived AS (
                SELECT
                    id,
                    "userId",
                    "createdAt",
                    CASE
                        WHEN "topicId"  IS NOT NULL THEN 'topic'
                        WHEN "courseId" IS NOT NULL THEN 'course'
                        WHEN "prepId"   IS NOT NULL THEN 'prep'
                    END AS kind,
                    COALESCE("topicId", "courseId", "prepId") AS ref
                FROM "FlashcardDeck"
                WHERE "topicId"  IS NOT NULL
                   OR "courseId" IS NOT NULL
                   OR "prepId"   IS NOT NULL
            ),
            ranked AS (
                SELECT
                    id,
                    kind,
                    ref,
                    ROW_NUMBER() OVER (
                        PARTITION BY "userId", kind, ref
                        ORDER BY "createdAt", id
                    ) AS rn
                FROM derived
            )
            UPDATE "FlashcardDeck" AS d
               SET "originType" = r.kind,
                   "originId"   = r.ref
              FROM ranked AS r
             WHERE d.id = r.id
               AND r.rn = 1
            """
        )
    )

    # Created after the backfill so a bad backfill fails here, loudly, rather than
    # leaving duplicate origins behind an index that was built before the data existed.
    op.create_index(
        "FlashcardDeck_userId_origin_uq",
        "FlashcardDeck",
        ["userId", "originType", "originId"],
        unique=True,
        postgresql_where=sa.text('"originType" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index("FlashcardDeck_userId_origin_uq", table_name="FlashcardDeck")
    op.drop_column("FlashcardDeck", "originId")
    op.drop_column("FlashcardDeck", "originType")
