"""Give a topic the structure a lesson has, and a course the facts its page states.

Everything here exists because a web surface already presents it. The lesson workspace at
`/learn/lessons/:lessonId` renders a topic as an ordered sequence of typed sections, each advanced
deliberately, with learning objectives above and a knowledge check at the end. The course page
states a category, lists outcomes, names an instructor and shows a learner rating. All of it was
fixture-backed, and the schema had no counterpart for any of it.

The rule this migration follows: where a surface shows something the schema lacks, **the schema
gains it**. The alternative that was tried first — deleting the parts of the page with no column
behind them — removed capability from the product to make the backend look complete, which is
backwards.

## Sections as rows, not as headings in the markdown

`Topic.content` is a single `Text` column, and the first instinct was to keep it that way and treat
section boundaries as markdown headings, since a title is a heading and a body is the prose beneath
it. That reasoning fails on one requirement: **each section is separately completable**, and the
learner advances through them one at a time. Completion needs identity — something to name in a
write and to store a timestamp against — and a heading inside a text blob has none. Parsing content
to find the third `##` and calling that an identity would make a rename silently reassign progress.

So a section is a row. Once it is a row, `paragraphs`, `steps`, `bullets` and `code` are stored as
JSON rather than re-parsed out of prose on every read, because the generator that produces them
already knows their structure and flattening it only to recover it later loses the distinction
between a step and a paragraph that happens to be numbered.

This reverses an earlier decision in this programme that cited `StudyPlanItem.phase` as precedent
for a label over a table. The precedent does not transfer: a phase is a grouping label with no state
of its own, and a section has state — whether the learner has finished it.

## Per-learner or per-row completion

`completed` sits on the section row, not in a join table keyed by learner, which matches
`Topic.completed` and `Module.completed` directly above it. A course has exactly one owner
(`Course.userId`), so the row and the learner are the same thing today. When shared courses need
per-learner section progress, `UserTopicProgress` is the table that grows — and the note on that
table already records that it is currently written by nothing.

## The knowledge check is JSON, not a fifth table

One check per topic, holding a question, an explanation and a list of choices with one marked
correct. It is never queried on its own, never joined, and never listed across topics; it is read
exactly when its topic is read. A `TopicKnowledgeCheck` table plus a `TopicChoice` table would add
two joins and a second ordering to keep honest, to store a value object that is always fetched whole.

Deliberately **not** routed through `QuizSession` in the preparation domain. That machinery models a
timed, scored, multi-question attempt with per-question observations feeding readiness scoring. An
end-of-lesson check is one question the learner answers to move on, and nothing records the attempt.
Borrowing the session machinery would mean creating and abandoning a scored session per lesson, which
would then appear in preparation analytics as an unfinished exam.

## Rating is a table, because an aggregate needs raters

The obvious shape is `Course.rating` as a float. That is a number with nothing behind it: it can be
written to any value, no one can change their mind, and "4.9 learner rating" would be as invented as
the fixture it replaces. `CourseRating` holds one row per learner per course, unique on the pair, so
the aggregate the page shows is computed from ratings that were actually given.

This is meaningful beyond self-rating precisely because courses are shared: classrooms assign courses
to members, so a course can genuinely have several raters. A course with none returns null, and the
page shows no rating rather than a zero.

## Instructor is authoring metadata, and nullable for a reason

`instructorName` and `instructorRole` are plain nullable strings on the course, not a foreign key to
`User` and not a new `Instructor` entity. A learner who generates a course for themselves has no
instructor, and inventing one — crediting the owner as the teacher of their own course, or crediting
"Maigie" — would state something untrue on the page. Null means there is no instructor to name, the
panel does not render, and a course that does have one (authored for a classroom, imported from a
syllabus) can say so.

Revision ID: 025_add_lesson_structure
Revises: 024_add_topic_completed
Create Date: 2026-08-16

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back. The id
below is 24 characters.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "025_add_lesson_structure"
down_revision = "024_add_topic_completed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- Topic
    # Objectives: what the learner will be able to do after this topic, shown above the first
    # section. A JSON array of strings, nullable and not backfilled — a topic written before this
    # column has no objectives, which is different from having an empty list of them, and the page
    # renders no objectives block rather than an empty one.
    op.add_column("Topic", sa.Column("objectives", postgresql.JSON(), nullable=True))
    # The end-of-lesson check: {question, explanation, choices: [{id, label, correct}]}.
    op.add_column("Topic", sa.Column("knowledgeCheck", postgresql.JSON(), nullable=True))

    # ---------------------------------------------------------------- TopicSection
    op.create_table(
        "TopicSection",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("topicId", sa.String(), nullable=False),
        # Float, matching Module.order and Topic.order, so a section can be inserted between two
        # others without renumbering the rest.
        sa.Column("order", sa.Float(), nullable=False, server_default="0"),
        # concept | example | algorithm | comparison | check. A plain string rather than a native
        # enum: migration 001 dropped the Prisma-era enums from this database precisely because
        # adding a value to one required a migration and a deploy in lockstep, and the set of ways
        # to explain something is not closed.
        sa.Column("kind", sa.String(), nullable=False, server_default="concept"),
        sa.Column("title", sa.String(), nullable=False),
        # The small label above the title ("Core idea", "Worked example").
        sa.Column("eyebrow", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        # Stored as minutes, an integer, rather than the "6 min" string the fixture held. A number
        # can be summed into a lesson total and formatted per locale; a formatted string can be
        # neither, and the page needs the total.
        sa.Column("durationMinutes", sa.Integer(), nullable=True),
        # The body. JSON arrays because the generator emits them structured and the reader wants
        # them structured: a step has a title and a detail, and a bullet is not a paragraph.
        sa.Column("paragraphs", postgresql.JSON(), nullable=True),
        sa.Column("keyIdea", sa.Text(), nullable=True),
        sa.Column("steps", postgresql.JSON(), nullable=True),
        sa.Column("bullets", postgresql.JSON(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default="false"),
        # Cleared when the section is reopened, so a pending section never carries a completion
        # time. Same contract as Topic.completedAt from migration 024.
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every read is "the sections of this topic, in order" — the outline, the reader, and the
    # next/previous controls all want exactly that, so the index carries the sort.
    op.create_index("TopicSection_topicId_order_idx", "TopicSection", ["topicId", "order"])

    # ---------------------------------------------------------------- Course
    # A subject taxonomy: "Computer Science", "Mathematics". Distinct from `difficulty`, which was
    # briefly used as a stand-in for it — how hard a course is and what it is about are two
    # different facts, and one cannot answer for the other.
    op.add_column("Course", sa.Column("category", sa.String(), nullable=True))
    # Free-form labels shown on the library card.
    op.add_column("Course", sa.Column("tags", postgresql.JSON(), nullable=True))
    # "What you'll be able to do" — course-level, and deliberately separate from Topic.objectives.
    # A course outcome is the promise the course makes; a topic objective is what one sitting
    # delivers. Deriving either from the other would flatten a curriculum into a list of lessons.
    op.add_column("Course", sa.Column("outcomes", postgresql.JSON(), nullable=True))
    op.add_column("Course", sa.Column("instructorName", sa.String(), nullable=True))
    op.add_column("Course", sa.Column("instructorRole", sa.String(), nullable=True))

    # ---------------------------------------------------------------- CourseRating
    op.create_table(
        "CourseRating",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("courseId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        # 1 to 5. Constrained in the database as well as the request model, because the aggregate
        # the page prints has no way to notice a 40 that got in by another path.
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint("value >= 1 AND value <= 5", name="CourseRating_value_range"),
        sa.ForeignKeyConstraint(["courseId"], ["Course.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One rating per learner per course. Rating again updates the existing row rather than
        # adding a second, so nobody can weight the average by clicking repeatedly.
        sa.UniqueConstraint("courseId", "userId", name="CourseRating_courseId_userId_key"),
    )
    op.create_index("CourseRating_courseId_idx", "CourseRating", ["courseId"])


def downgrade() -> None:
    op.drop_index("CourseRating_courseId_idx", table_name="CourseRating")
    op.drop_table("CourseRating")

    op.drop_column("Course", "instructorRole")
    op.drop_column("Course", "instructorName")
    op.drop_column("Course", "outcomes")
    op.drop_column("Course", "tags")
    op.drop_column("Course", "category")

    op.drop_index("TopicSection_topicId_order_idx", table_name="TopicSection")
    op.drop_table("TopicSection")

    op.drop_column("Topic", "knowledgeCheck")
    op.drop_column("Topic", "objectives")
