"""Add the preparation intent columns collected by the create wizard.

The four-step create wizard asks the learner two things about how they want to
work — how confident they already feel, and how hard they want to push — and then
discards both, because there was nowhere to put them.

Only two columns are added, not the three originally specified. The wizard's
"session length" is not a field the learner fills in: each pace option carries
descriptive copy ("3 sessions each week", "About 2 hours weekly") that is derived
from the pace itself. Persisting a session length would have stored a number the
learner never chose. The pace-to-effort mapping now lives server-side in
``prep_intent`` instead, so study-plan generation and the wizard cannot disagree
about what "Balanced" means.

Both nullable with no server default, for the same reason as migration 006:
existing rows have nothing to infer a value from, and defaulting would invent
user intent.

Revision ID: 007_add_prep_intent_fields
Revises: 006_add_exam_prep_type
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "007_add_prep_intent_fields"
down_revision = "006_add_exam_prep_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # STARTING | DEVELOPING | CONFIDENT
    op.add_column("ExamPrep", sa.Column("confidence", sa.String(), nullable=True))
    # LIGHT | BALANCED | INTENSIVE
    op.add_column("ExamPrep", sa.Column("pace", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("ExamPrep", "pace")
    op.drop_column("ExamPrep", "confidence")
