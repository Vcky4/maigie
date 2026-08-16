"""Two columns the create wizard needs, and no more than two.

The wizard at `/courses/new` gathers twelve things across four steps and persists none of them — its
"Create course" button is a `setTimeout` that shows a success screen. Making it real means finding a
home for each of the twelve, and the interesting result of that audit is that **ten already have one**:

| Wizard input | Where it goes |
| ------------ | ------------- |
| Title, category, tags, outcomes | `Course` columns, some added in 025 |
| Level (Foundation / Intermediate / Advanced) | `Course.difficulty` |
| Learning style (Visual / Hands-on / Concept first / Mixed) | `LearningProfile.preferredExplanationStyle`, which already exists |
| Pace, session length | `StudyPlan.sessionsPerWeek` and `sessionMinutes`, when the learner asks for a plan |
| Web links | `Resource` rows with `courseId` and type `WEBSITE` |
| Uploaded files | `Resource` rows pointing at object storage |
| The outline: modules, lesson titles, durations | `Module` and `Topic`, with `Topic.estimatedHours` |
| "Generate flashcards", "Create a study plan" | Actions taken after creation, not state |

Only two have nowhere to go, and both are added here. Adding a column for each of the other ten would
have meant ten places where the same fact could disagree with itself — a course's pace stored beside
its plan's pace, a style on the course beside the style on the profile.

## `Course.sourcePrompt`

The wizard's largest input: a free-text description of what the learner wants built. It drives
generation, and without a column it is typed, read once to produce an outline, and thrown away — so a
learner cannot see what they asked for, and regeneration has nothing to regenerate from.

Named `sourcePrompt` rather than `prompt` because it is the learner's brief, not the prompt sent to the
model. The prompt sent is composed by `lesson_service` and the generators from this plus the topic
title, the existing content and the shape; storing the composed version would freeze prompt engineering
into the data, so that a later improvement to the wording would not apply to any existing course.

## `Topic.kind`

The outline preview labels each lesson Lesson, Practice, Project or Check, and the schema has no
counterpart, so every topic would render identically and the distinction the wizard shows would be lost
on save.

**Not the same thing as `TopicSection.kind`**, added in 025, and the two are easy to confuse. A
section's kind is how one passage within a lesson explains something — concept, example, algorithm. A
topic's kind is what kind of work the whole sitting is: reading, practising, building, or being tested.
A project made of three explanatory sections is coherent; collapsing the two would make it
contradictory.

A plain string, not an enum, for the reason migration 001 gave when it dropped the Prisma-era enums:
extending one requires a migration and a deploy in lockstep, and the set of things a sitting can be is
not closed.

Nullable, with no backfill and no default. A topic written before this has no kind, which is different
from being a `Lesson` — defaulting would assert something about every existing topic that nobody
decided, and the outline shows no label rather than a guessed one.

Revision ID: 027_add_authoring_fields
Revises: 026_restore_review_aids
Create Date: 2026-08-16

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back. The id below
is 25 characters.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "027_add_authoring_fields"
down_revision = "026_restore_review_aids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Text rather than String: this is a brief the learner writes, and a length limit here would
    # truncate their description rather than reject it.
    op.add_column("Course", sa.Column("sourcePrompt", sa.Text(), nullable=True))
    op.add_column("Topic", sa.Column("kind", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("Topic", "kind")
    op.drop_column("Course", "sourcePrompt")
