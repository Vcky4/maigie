"""Add the Prepare columns the web workspace needs and could not derive.

Four columns, each backing a UI element that previously had no data source and was
rendering a fixture value.

``ExamPrep.targetReadiness`` — the workspace draws readiness *against a target*
(the fixture hardcodes 85) and the trend chart draws a second line for it. There
is no way to derive a goal from measurements: a target is an intention, and only
the learner has it. Nullable, because a preparation without a stated target is
valid and the client then draws one line instead of two.

``PrepTopic.targetMastery`` — the same intention at topic level, where the fixture
varies it per topic (85 for most, 80 for regression). Nullable, falling back to
the preparation's target.

``PrepTopic.category`` — the workspace and dashboard group topics under headings
("Foundations", "Statistical inference", "Modelling"). Grouping is a property of
the topic, not of its mastery, so it cannot be computed. Written by topic
extraction going forward; existing topics keep ``NULL`` and render ungrouped,
rather than being assigned a heading nobody chose.

``PrepReadinessSnapshot.targetPercent`` — the target as it stood on the day
captured. Stored rather than joined so that changing a target later does not
retroactively rewrite the chart's history, which is the whole reason this table
exists.

Revision ID: 016_align_prepare_ui_fields
Revises: 015_add_onboarding_state_fields
Create Date: 2026-08-11
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "016_align_prepare_ui_fields"
down_revision = "015_add_onboarding_state_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0-100. Nullable with no server default: a default would invent a goal.
    op.add_column("ExamPrep", sa.Column("targetReadiness", sa.Integer(), nullable=True))

    # Free-text grouping label, written by topic extraction.
    op.add_column("PrepTopic", sa.Column("category", sa.String(), nullable=True))
    # 0-100. Falls back to the preparation's target when null.
    op.add_column("PrepTopic", sa.Column("targetMastery", sa.Float(), nullable=True))

    # The target in force on the captured day, so history stays stable when the
    # target changes.
    op.add_column("PrepReadinessSnapshot", sa.Column("targetPercent", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("PrepReadinessSnapshot", "targetPercent")
    op.drop_column("PrepTopic", "targetMastery")
    op.drop_column("PrepTopic", "category")
    op.drop_column("ExamPrep", "targetReadiness")
