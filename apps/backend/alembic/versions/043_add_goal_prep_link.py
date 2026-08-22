"""Add Goal.prepId — the link that makes the `prep_readiness` metric kind measurable.

Migration `042` gave `Goal` a `metricKind` with `prep_readiness` among its permitted values, on
Decision N's wording: "Reach 80% interview readiness" maps to readiness over a linked preparation.
Nothing on the row said *which* preparation. `Goal` carries `courseId`, `topicId` and `spaceId` and
no preparation link at all, so a goal set to `prep_readiness` had no source to measure and its
derived `currentValue` would have read null for good.

A metric kind a learner can choose and the server can never measure is worse than not offering the
choice: the goal detail page would show a target with a permanently blank current value and no
explanation. Rather than remove the enum value — which is in the design and is a real thing learners
want to track — this adds the column it needs.

Nullable, no backfill, no default. Existing goals are all `metricKind = 'manual'` and none of them
was ever attached to a preparation, so there is nothing to infer; guessing from a shared subject
string would be the migration deciding what the learner meant.

Plain `String` with an index and no foreign key, matching `courseId`, `topicId` and `spaceId` on this
same table. That is looser than this codebase's newer tables — a deleted preparation leaves a
dangling id — but a single column constrained differently from its three siblings is a worse
inconsistency than the one it fixes, and the derivation already treats an unresolvable id as
"unmeasured" rather than failing. Recorded in the plan as an open item covering all four columns
together.

Revision ID: 043_add_goal_prep_link
Revises: 042_add_goal_targets
Create Date: 2026-08-22
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "043_add_goal_prep_link"
down_revision = "042_add_goal_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Goal", sa.Column("prepId", sa.String(), nullable=True))
    op.create_index("ix_Goal_prepId", "Goal", ["prepId"])


def downgrade() -> None:
    op.drop_index("ix_Goal_prepId", table_name="Goal")
    op.drop_column("Goal", "prepId")
