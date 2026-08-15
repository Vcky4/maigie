"""Persist everything the study plan create wizard collects.

Step 3 of the wizard ("Connected learning") gathered four things and could store none
of them: linked courses, reference files, and two toggles. Wiring the wizard as it stood
would have silently discarded all four — the same defect as the flashcard deck builder,
whose footer used to read "Nothing is sent to the backend".

**`StudyPlanCourse` — courses linked to a plan.**

A table with a real foreign key to `Course`, not a JSON list of ids on the plan. The
difference matters when a course is deleted: a foreign key with `CASCADE` removes the
link, while a JSON array keeps an id that resolves to nothing and has to be filtered on
every read by whoever remembers to. The plan's detail page lists these by title, which
means joining to `Course` anyway.

Unique on `(planId, courseId)`, because linking the same course twice is not a state the
UI can produce or the page can render.

**`StudyPlanMaterial` — reference files attached to a plan.**

Mirrors `PrepMaterial`, which already does exactly this for a preparation: filename, a
storage URL, a type and a size. No `extractedText` column — `PrepMaterial` has one
because preparation topics are extracted from material, and nothing reads a study plan's
files that way yet. Adding a column for a use that does not exist is how
`Course.progress` ended up written by nothing.

**Toggles become behaviour, not stored intent.**

`generateReviewCards` and `weeklyCheckIn` are only worth persisting because something
now acts on them, which is the whole point:

- `generateReviewCards` makes completing an item generate flashcards from it, into a deck
  owned by the plan — hence `reviewDeckId`, created on first use and reused after.
- `weeklyCheckIn` is read by a weekly beat task that creates a notification;
  `lastCheckInAt` is what makes that idempotent, so a retried or overlapping run cannot
  send twice and a missed week cannot silently become two.

Storing either flag without the behaviour would repeat the `NoteHistory` mistake: rows
written that nothing ever reads.

Revision ID: 022_add_plan_connections
Revises: 021_add_study_plan_shape
Create Date: 2026-08-14

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so a revision id
longer than that fails at the very last statement of the upgrade — after the DDL, which
then rolls back with it. Keep new ids short.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "022_add_plan_connections"
down_revision = "021_add_study_plan_shape"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "StudyPlanCourse",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "planId",
            sa.String(),
            sa.ForeignKey("StudyPlan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE, so a deleted course takes its links with it rather than leaving ids
        # that resolve to nothing.
        sa.Column(
            "courseId",
            sa.String(),
            sa.ForeignKey("Course.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("planId", "courseId", name="StudyPlanCourse_planId_courseId_key"),
    )
    op.create_index("StudyPlanCourse_planId_idx", "StudyPlanCourse", ["planId"])
    op.create_index("StudyPlanCourse_courseId_idx", "StudyPlanCourse", ["courseId"])

    op.create_table(
        "StudyPlanMaterial",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "planId",
            sa.String(),
            sa.ForeignKey("StudyPlan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("fileType", sa.String(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("StudyPlanMaterial_planId_idx", "StudyPlanMaterial", ["planId"])

    # Both default false: an existing plan was created without being asked, and turning
    # a behaviour on for it would be acting on a choice the learner never made.
    op.add_column(
        "StudyPlan",
        sa.Column(
            "generateReviewCards",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "StudyPlan",
        sa.Column("weeklyCheckIn", sa.Boolean(), nullable=False, server_default="false"),
    )
    # SET NULL: deleting the deck should not delete the plan that generated into it.
    op.add_column(
        "StudyPlan",
        sa.Column(
            "reviewDeckId",
            sa.String(),
            sa.ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "StudyPlan", sa.Column("lastCheckInAt", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("StudyPlan", "lastCheckInAt")
    op.drop_column("StudyPlan", "reviewDeckId")
    op.drop_column("StudyPlan", "weeklyCheckIn")
    op.drop_column("StudyPlan", "generateReviewCards")
    op.drop_index("StudyPlanMaterial_planId_idx", table_name="StudyPlanMaterial")
    op.drop_table("StudyPlanMaterial")
    op.drop_index("StudyPlanCourse_courseId_idx", table_name="StudyPlanCourse")
    op.drop_index("StudyPlanCourse_planId_idx", table_name="StudyPlanCourse")
    op.drop_table("StudyPlanCourse")
