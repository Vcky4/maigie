"""Add goal targets and GoalMilestone — what a goal measures, and its checkpoints.

`ReflectGoalDetailPage` renders `currentValue`, `targetValue`, `pace`, `projectedOutcome`,
`trend[]`, `milestones[]`, `evidence[]`, `insight`, `nextAction` and a three-way `status`. `Goal`
had `title`, `targetDate`, `status`, `progress` and three optional links. Everything else on that
page came from a fixture.

Three of those needed no schema and get none: `status` derives from `progress` against elapsed
time to `targetDate`, `trend[]` from `DailyLearningSnapshot`, and `evidence[]` from
`ActivityFeedEntry` filtered by the goal's linked course or topic. This migration funds the rest.

**`metricKind` is the column that makes `currentValue` honest, and it is why these four arrive
together rather than `currentValue` arriving alone.** "Study 300 focused minutes" is measurable
from `StudySession` and must never be a number the learner typed. "Reach 80% interview readiness"
maps to prep readiness over a linked preparation. A goal with no measurable source is `manual` and
says so. Without the discriminator, `currentValue` would be a column that is sometimes measured
and sometimes asserted with no way to tell which — which is the fabricated-metrics defect this
whole programme exists to close, one table over. `currentValue` is therefore written **only** when
`metricKind = 'manual'`; every other kind derives it on read, because a stored copy of a figure
that already exists starts disagreeing with it the moment the source moves.

Constrained by a CHECK as well as in Pydantic. `Reflection.type` is the precedent and the reason:
an unconstrained String let the weekly task write `"WEEKLY"` while the service branched on
`"weekly"`, and every scheduled row was silently wrong for months.

**Existing rows default to `'manual'`, and that is a description rather than an assumption.** No
existing goal has a measurable source attached — nobody chose one, and `progress` on those rows is
whatever was last set by hand. `'manual'` is the accurate account of them. Defaulting to
`'course_progress'` for goals that happen to carry a `courseId` would be the migration deciding
what the learner meant.

`targetValue`, `unit` and `currentValue` are all nullable with no backfill: a learner who never
stated a target has none, and inventing one would make `pace` and `projectedOutcome` computable
against a figure nobody chose. They stay null instead, and those derived fields stay null with
them.

**`GoalMilestone` is a table because milestones cannot be derived.** Nothing in the data can infer
that "finish the syllabus" divides into four stages; that division is the learner's own, and
generating one would be the surface asserting structure they never described. `orderIndex` is
explicit because a milestone list is a sequence they chose, and `createdAt` records only the order
they happened to type them in. `achievedAt` is a nullable timestamp rather than a boolean so
"when" is answerable — the goal trend needs it, and a boolean would have to be replaced by this
column later anyway.

Not to be confused with `Achievement`, which Reflect reads for *milestones reached* (Decision Q).
An `Achievement` is unlocked by the system for something done; a `GoalMilestone` is a step
planned. `CASCADE` and `delete-orphan`, unlike decks and their cards: a milestone is part of the
goal's definition rather than work the learner authored, so there is nothing to detach it to.

Revision ID: 042_add_goal_targets
Revises: 041_add_reflection_narrative
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "042_add_goal_targets"
down_revision = "041_add_reflection_narrative"
branch_labels = None
depends_on = None

_METRIC_KINDS = (
    "focused_minutes",
    "topics_mastered",
    "cards_reviewed",
    "course_progress",
    "prep_readiness",
    "manual",
)


def upgrade() -> None:
    # NOT NULL with a server default, so existing rows are described rather than left unstated.
    # `manual` is true of every goal that exists: none has a measurable source attached.
    op.add_column(
        "Goal",
        sa.Column("metricKind", sa.String(), nullable=False, server_default="manual"),
    )
    op.add_column("Goal", sa.Column("targetValue", sa.Float(), nullable=True))
    op.add_column("Goal", sa.Column("unit", sa.String(), nullable=True))
    op.add_column("Goal", sa.Column("currentValue", sa.Float(), nullable=True))

    values = ", ".join(f"'{kind}'" for kind in _METRIC_KINDS)
    op.create_check_constraint("Goal_metricKind_check", "Goal", f'"metricKind" IN ({values})')

    op.create_table(
        "GoalMilestone",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "goalId",
            sa.String(),
            sa.ForeignKey("Goal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        # Null for a milestone that is a step rather than a threshold, which is most of them.
        sa.Column("targetValue", sa.Float(), nullable=True),
        sa.Column("orderIndex", sa.Integer(), nullable=False, server_default="0"),
        # Null until reached.
        sa.Column("achievedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # The list query: one goal's milestones in the learner's chosen order. No separate index on
    # `goalId` alone — this one leads with it, so it serves those lookups and the FK cascade.
    op.create_index(
        "GoalMilestone_goalId_orderIndex_idx", "GoalMilestone", ["goalId", "orderIndex"]
    )


def downgrade() -> None:
    op.drop_index("GoalMilestone_goalId_orderIndex_idx", table_name="GoalMilestone")
    op.drop_table("GoalMilestone")

    op.drop_constraint("Goal_metricKind_check", "Goal", type_="check")
    op.drop_column("Goal", "currentValue")
    op.drop_column("Goal", "unit")
    op.drop_column("Goal", "targetValue")
    op.drop_column("Goal", "metricKind")
