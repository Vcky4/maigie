"""Persist quiz generation duration and study-plan strategy.

Two columns, both for the same reason: a property that was asserted in a docstring
and left no trace in the data, so nobody could check it.

Decision H fixed quiz start as synchronous "until p95 start latency exceeds 10s or
a client timeout is observed", and Phase 4e deferred a real staged progress bar to
that same reading. The reading could not be taken.

``generation_ms`` was computed on every start and emitted only as a structured log
field, so the p95 required log aggregation — while every other measurement on this
surface (answer position bias, hint coverage, session sizing, timeline coverage)
was answerable with a read-only script against the database. That asymmetry is why
the number was cited three times in the plan and never actually read.

One nullable integer on ``QuizSession``. Nullable with no default and no backfill:
sessions created before this column have no timing, and ``0`` would read as
instantaneous generation, which is exactly the kind of invented value that would
skew the percentile the column exists to produce. ``scripts/check_generation_latency.py``
reports coverage alongside the percentiles for that reason.

Revision ID: 018_add_quiz_generation_ms
Revises: 017_add_timezone_provenance
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "018_add_quiz_generation_ms"
down_revision = "017_add_timezone_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("QuizSession", sa.Column("generationMs", sa.Integer(), nullable=True))

    # `StudyPlan.strategy` — `ADAPTIVE` or `EVEN`.
    #
    # Same class of problem, same fix. "Adaptive study plans" was sold as a Plus
    # capability while `generate_plan` computed `is_adaptive` and branched on
    # nothing, so a Plus plan was byte-for-byte a Free plan. That survived because
    # the claim left no trace in the data for anyone to check against. It does now.
    #
    # Nullable and not backfilled: every existing plan was in fact scheduled evenly,
    # but writing `EVEN` onto rows nobody measured would be asserting a property of
    # history rather than recording one.
    op.add_column("StudyPlan", sa.Column("strategy", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("StudyPlan", "strategy")
    op.drop_column("QuizSession", "generationMs")
