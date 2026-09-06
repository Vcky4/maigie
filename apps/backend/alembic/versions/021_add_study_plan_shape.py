"""Let a study plan carry the shape its UI is built around: phases, a weekly goal, skills.

The three study-plan pages are designed around a richer object than these tables hold.
They render a plan as *phases* containing *milestones*, with a weekly minute goal, a
skill list, and a pause control. `StudyPlan` and `StudyPlanItem` are flat: a plan with
dated items, three statuses, and no grouping. Every one of those page concepts was
supplied by a fixture.

This migration closes the gap with three nullable columns rather than new tables,
because on inspection the hierarchy the UI draws is a *grouping*, not a new entity.

**`StudyPlanItem.phase` — a grouping label.**

A phase has no properties of its own that a plan item does not already imply. Its
title is the label, its week range is the span of its items' `scheduledDate`, its
progress is the share of its items completed, and its number is its position in that
order. A `StudyPlanPhase` table would therefore store a name and a foreign key, and
every other field on it would be derived from the items anyway — while adding a second
thing that can disagree with them about ordering.

The precedent is `PrepTopic.category`, added for exactly this shape: a grouping label
that is a property of the item, cannot be computed from anything else, and is null for
rows created before it existed. Same reasoning, same column type, same nullability.

Nullable and not backfilled. Plans generated before this exist and were never grouped;
inventing phase names for them would be writing a structure the generator never
produced. A plan with no phases on any item renders as one flat list, which is what it
is.

**`StudyPlan.weeklyGoalMinutes` — how much a week the learner intends to study.**

An intention, not a measurement, so it cannot be derived and a default would invent a
goal on their behalf — the same reasoning as `ExamPrep.targetReadiness`. The create
wizard already collects a pace and a session length and had nowhere to send them. When
null, a surface shows minutes planned without a target.

**`StudyPlan.skills` — what the plan builds, as a JSON array.**

Named by the generator alongside the items, so it is not derivable from titles after
the fact without guessing. Follows `LearningProfile.subjects`, which stores a JSON list
for the same reason.

**No column for `PAUSED`.** `StudyPlan.status` is already a free-text column and gains
a fourth value rather than a schema change, so this migration does not mention it. It is
recorded here because the value is new: `ACTIVE`, `PAUSED`, `COMPLETED`, `SUPERSEDED`.
The detail page has always had a pause control, and pausing previously had nowhere to be
stored, so the button changed local state and forgot.

Revision ID: 021_add_study_plan_shape
Revises: 020_add_flashcard_review_log
Create Date: 2026-08-13
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "021_add_study_plan_shape"
down_revision = "020_add_flashcard_review_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("StudyPlanItem", sa.Column("phase", sa.String(), nullable=True))
    # Items are read grouped by phase and ordered by date within it.
    op.create_index(
        "StudyPlanItem_planId_phase_idx",
        "StudyPlanItem",
        ["planId", "phase"],
    )
    op.add_column("StudyPlan", sa.Column("weeklyGoalMinutes", sa.Integer(), nullable=True))
    op.add_column("StudyPlan", sa.Column("skills", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("StudyPlan", "skills")
    op.drop_column("StudyPlan", "weeklyGoalMinutes")
    op.drop_index("StudyPlanItem_planId_phase_idx", table_name="StudyPlanItem")
    op.drop_column("StudyPlanItem", "phase")
