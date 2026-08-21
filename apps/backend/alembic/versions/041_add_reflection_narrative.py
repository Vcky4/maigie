"""Add Reflection.narrative — the written half, typed.

Migration 038 removed `activitiesLayer`, `progressLayer` and `achievementsLayer` and replaced
them with one `metrics` column that `ReflectionMetrics` validates. That fixed the *numbers*. It
left the prose with nowhere structured to live: a reflection has `summary`, one paragraph, while
the page it feeds renders an opening, a theme, per-signal explanations, per-subject insights, a
week rhythm strip, keep/watch patterns, highlights and a closing line.

This adds one JSON column for all of it, validated by `ReflectionNarrative` at the boundary.

**One column, not five tables.** The narrative is read and written whole; nothing filters on
`closing` or joins on a signal. A table per section would add joins to serve no query.

**Typed at the boundary, which is the part that matters.** The three columns this follows were
untyped `dict`, and the consequence is the reason this programme exists: the service wrote
snake_case keys into a camelCase payload, so `ReflectionDetailPage` read `undefined` for every
field, and four of the keys it wanted were never written under any spelling. A `dict` cannot be
wrong — nothing can disagree with it, so nothing reports when it drifts.

**Nullable, unlike `metrics` and `recommendations`.** Those are NOT NULL with `{}` / `[]`
defaults, because for them absence belongs *inside* the object as a null field. Here absence is
the whole object. Narration is a separate step from measurement and fails on its own, and Phase 1
settled that a reflection with genuine metrics and no prose is worth keeping rather than
discarding — so `NULL` has to mean "not narrated". An empty narrative would assert prose was
written and then say nothing, which is precisely what the stored apology text used to do.

No backfill. Existing rows get `NULL`, which is the true statement about them: no narrative was
ever composed in this shape for a row that predates the shape. Their `summary` is untouched and
still renders.

The column is added ahead of the composer that fills it, so every row reads `NULL` until that
lands. `NULL` is a published, meaningful state rather than a placeholder, and the alternative —
holding the schema back — would mean the composer and its storage arriving in one unreviewable
change.

Revision ID: 041_add_reflection_narrative
Revises: 040_add_reflection_notes
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "041_add_reflection_narrative"
down_revision = "040_add_reflection_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Reflection", sa.Column("narrative", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("Reflection", "narrative")
