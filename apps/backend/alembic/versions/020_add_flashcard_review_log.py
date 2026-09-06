"""Record every flashcard review as an event, and let a deck carry the learner's own labels.

Two additions, both required before the flashcards page can stop inventing its numbers.

**`FlashcardReview` — one row per grade.**

`Flashcard` keeps only the *latest* review: `lastReviewedAt`, `lastQuality`, and the
SM-2 state that grade produced. That is all the scheduler needs, and it is why the
scheduler was built that way. It is not enough for anything that asks *how often* or
*since when*, and the repository was already trying to answer those questions from it:

    activity_dates = {value.date() for value in review_timestamps}  # one date per card

`reviewedThisWeek`, `activeDaysThisWeek` and `currentStreak` are all derived from that
set. The result is not an approximation, it is wrong in a direction that destroys
history. Review cards A, B and C on Monday, review the same three on Tuesday, and every
`lastReviewedAt` now reads Tuesday: Monday vanishes from the set and the streak reports
1 instead of 2. A learner's streak could be shortened by studying.

Per-review rows make those figures answerable, and three more the page needs: a real
recall trend, per-deck recall attribution, and mastery *change*, which requires knowing
what a card's interval was at an earlier point in time rather than only now.

Deliberate choices in the shape:

- `flashcardId` and `deckId` are `SET NULL`, not `CASCADE`. Stage 2 adds card and deck
  deletion, and a review that happened is a fact about the learner's week regardless of
  whether the card still exists. Cascading would mean deleting a card silently rewrites
  a streak that was already earned and already displayed.
- `deckId` is stored on the row rather than read through the card, because a card can be
  moved between decks. Per-deck recall has to attribute a grade to the deck it was
  graded in, not the deck the card ended up in.
- `intervalDays`, `easeFactor` and `repetitionCount` are the values *after* this review.
  They make "was this card mature on a given date" answerable by replay, which is what
  mastery change needs. Storing only the quality would leave that unanswerable again.
- `wasLapse` is stored rather than recomputed as `quality < 3`. The lapse threshold is a
  scheduling policy that may be tuned; a stored flag keeps historic rows meaning what
  they meant when they were written.

**No backfill.** Existing cards yield exactly one timestamp each and no history, so any
attempt to seed the log would be inventing sessions that were never observed — the same
class of fabrication this programme exists to remove. The log therefore starts empty:
streak, weekly counts and activity read as "no data yet" until reviews accumulate, while
totals, due counts, mastery and recall keep working immediately because they come from
`Flashcard` columns that are already populated.

**Deck columns: `subject`, `accent`, `dailyGoal`.**

The deck create modal has always collected a subject, a colour and a daily pace, and its
own footer admitted "Nothing is sent to the backend". There was nowhere to send them.
All three are nullable: a deck created before this migration has no subject to infer, no
colour the learner chose, and no pace they set, and defaulting any of them would state a
preference on the learner's behalf.

`accent` is presentation, which the API otherwise refuses to describe. The distinction
being drawn is who chose it: the server never picks a deck colour, and clients still
derive one for decks where this is null. What is stored here is a choice the learner made
in a colour picker, which is their data in the same way a title is.

Revision ID: 020_add_flashcard_review_log
Revises: 019_add_quiz_generation_stage
Create Date: 2026-08-13
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "020_add_flashcard_review_log"
down_revision = "019_add_quiz_generation_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "FlashcardReview",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: see the module docstring. A deleted card must not
        # retract reviews that already happened.
        sa.Column(
            "flashcardId",
            sa.String(),
            sa.ForeignKey("Flashcard.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "deckId",
            sa.String(),
            sa.ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("quality", sa.Integer(), nullable=False),
        # SM-2 state produced by this review, so an earlier state can be replayed.
        sa.Column("intervalDays", sa.Integer(), nullable=False),
        sa.Column("easeFactor", sa.Float(), nullable=False),
        sa.Column("repetitionCount", sa.Integer(), nullable=False),
        sa.Column("wasLapse", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewedAt", sa.DateTime(timezone=True), nullable=False),
    )
    # Every read is "this learner's reviews, newest first, optionally within a window".
    op.create_index(
        "FlashcardReview_userId_reviewedAt_idx",
        "FlashcardReview",
        ["userId", "reviewedAt"],
    )
    # Per-deck recall and per-deck activity attribution.
    op.create_index(
        "FlashcardReview_deckId_reviewedAt_idx",
        "FlashcardReview",
        ["deckId", "reviewedAt"],
    )
    # Replaying one card's history for mastery-at-a-date.
    op.create_index(
        "FlashcardReview_flashcardId_reviewedAt_idx",
        "FlashcardReview",
        ["flashcardId", "reviewedAt"],
    )

    op.add_column("FlashcardDeck", sa.Column("subject", sa.String(), nullable=True))
    op.add_column("FlashcardDeck", sa.Column("accent", sa.String(), nullable=True))
    op.add_column("FlashcardDeck", sa.Column("dailyGoal", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("FlashcardDeck", "dailyGoal")
    op.drop_column("FlashcardDeck", "accent")
    op.drop_column("FlashcardDeck", "subject")
    op.drop_index("FlashcardReview_flashcardId_reviewedAt_idx", table_name="FlashcardReview")
    op.drop_index("FlashcardReview_deckId_reviewedAt_idx", table_name="FlashcardReview")
    op.drop_index("FlashcardReview_userId_reviewedAt_idx", table_name="FlashcardReview")
    op.drop_table("FlashcardReview")
