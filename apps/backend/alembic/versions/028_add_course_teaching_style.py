"""How a course should be taught, stored on the course.

The create wizard's second step asks four questions about the course being built: its level, its pace,
how long a sitting should be, and **how it should be explained** — Visual, Hands-on, Concept first, or
Mixed. Three of those four already had a home. This is the fourth.

## Why not `LearningProfile.preferredExplanationStyle`

That column exists, and reaching for it was the first instinct — it is the same vocabulary and it is
already there. It is the wrong home, and the reason is scope.

The profile field is a **global** preference: it describes how this learner likes things explained,
everywhere, and the tutor reads it for every subject. The wizard's question is about **one course**.
Writing the wizard's answer to the profile means choosing "Visual" for a diagram-heavy geometry course
silently changes how an unrelated course on writing style is explained, and nothing on screen says so.
A learner who then wondered why their other course had changed tone would have no way to find out.

So the two coexist, with a precedence the generator applies: a course's own style wins, and the profile
supplies the default when the course has none. That is the normal shape for a scoped override, and it
keeps both facts true — the learner's general preference, and their choice for this course.

## Why not on a study plan

Pace and session length went to `StudyPlan.sessionsPerWeek` and `sessionMinutes`, because those two
describe a *schedule* and a plan is the thing that holds a schedule. Style is not a schedule; it is a
property of the material. A course with no plan still needs to know how to explain itself.

Worth recording, because it is a live loose end: **pace and session length have no consumer when the
learner does not ask for a study plan.** The wizard collects them regardless. Rather than store them
somewhere nothing reads — a column written by nothing, which this programme has now found three times —
the wizard should present them as part of the study-plan option, so the learner can see when they apply.
That is a UI change, noted in the plan doc, not a column.

A plain string, not an enum, for the reason migration 001 gave when it dropped the Prisma-era enums:
extending one needs a migration and a deploy in lockstep, and the set of ways to explain something is
not closed.

Nullable, no default, no backfill. A course written before this has no style of its own and falls through
to the profile, which is exactly what is true of it.

Revision ID: 028_add_teaching_style
Revises: 027_add_authoring_fields
Create Date: 2026-08-16

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "028_add_teaching_style"
down_revision = "027_add_authoring_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Course", sa.Column("teachingStyle", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("Course", "teachingStyle")
