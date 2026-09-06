"""Add question metadata: difficulty, provenance, and exam tips.

The practice fixtures showed a difficulty badge, a "past paper" or "AI generated"
provenance label, a year, and an exam tip on every question. None of it had a
column, so all four were invented by the client.

``source`` is **set by the server, never taken from the generator.** A model
asked to report whether its own output came from a past paper will happily say
yes; provenance that the producer can self-declare is not provenance. Questions
created by generation are therefore always ``AI_GENERATED``, and ``PAST_PAPER``
becomes available only through an import path that actually knows better.

``sourceYear`` is meaningful only for past papers and stays null otherwise.

Revision ID: 009_add_question_metadata
Revises: 008_promote_questions_to_bank
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "009_add_question_metadata"
down_revision = "008_promote_questions_to_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # EASY | MEDIUM | HARD. Nullable: questions banked before this column existed
    # have no difficulty, and inferring one would be inventing data.
    op.add_column("PrepQuestion", sa.Column("difficulty", sa.String(), nullable=True))
    # AI_GENERATED | PAST_PAPER. Server-set.
    op.add_column("PrepQuestion", sa.Column("source", sa.String(), nullable=True))
    op.add_column("PrepQuestion", sa.Column("sourceYear", sa.Integer(), nullable=True))
    op.add_column("PrepQuestion", sa.Column("examTip", sa.Text(), nullable=True))

    # Every question that exists at this point was produced by generation, so this
    # backfill states a fact rather than guessing one.
    op.execute(
        """
        UPDATE "PrepQuestion"
        SET source = 'AI_GENERATED'
        WHERE source IS NULL
        """
    )

    # Browsing the bank filters on these two together.
    op.create_index("PrepQuestion_prepId_difficulty_idx", "PrepQuestion", ["prepId", "difficulty"])


def downgrade() -> None:
    op.drop_index("PrepQuestion_prepId_difficulty_idx", table_name="PrepQuestion")
    op.drop_column("PrepQuestion", "examTip")
    op.drop_column("PrepQuestion", "sourceYear")
    op.drop_column("PrepQuestion", "source")
    op.drop_column("PrepQuestion", "difficulty")
