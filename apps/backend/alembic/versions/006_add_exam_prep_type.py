"""Add the preparation type column.

``POST /api/v1/learning/preparations`` has always accepted a required ``type``
field (EXAM, CERTIFICATION, INTERVIEW, PRESENTATION, ASSIGNMENT, PROJECT) and
silently discarded it: there was no column to write it to, so the value only
ever reached an activity-feed context blob. This adds the column so the field
is persisted instead of dropped.

Nullable with no server default: existing rows have no value that could be
inferred, and guessing "EXAM" for them would invent data.

Revision ID: 006_add_exam_prep_type
Revises: 005_add_commercial_models
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "006_add_exam_prep_type"
down_revision = "005_add_commercial_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ExamPrep", sa.Column("type", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("ExamPrep", "type")
