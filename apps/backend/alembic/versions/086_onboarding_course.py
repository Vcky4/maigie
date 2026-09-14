"""Onboarding remembers the course it created for a learner.

The "Learn" path used to produce an `ExamPrep` of type `PROJECT` — a preparation, with a
target date thirty days out, for a learner who had said they wanted to learn a subject and
mentioned no deadline at all. The surface they were then dropped into was `/prepare`. So the
one intent on the picker that names a surface pointed at a different one, and the course the
learner expected was never created by any branch of onboarding.

Now it creates a `Course`. Recording which course means two things work that could not before:

1. **Uploads have somewhere to go.** A document can only be attached to a course that exists,
   so the course row is written as soon as the learner names what they want to learn, before
   any model call. The client uploads to it, and generation reads what was uploaded. Without
   a recorded id the server could not tell "the course this onboarding is building" from
   "some course this learner owns".
2. **Generation is resumable and does not duplicate.** Auto-setup is best-effort and its parts
   are individually wrapped; a retry after a failed outline must extend the existing course
   rather than create a second one. `onboardingCourseId` is what makes the retry idempotent.

Nullable, no default, no backfill: learners who onboarded through the preparation path have no
onboarding course, and `NULL` is the accurate answer for them rather than a guess at which of
their courses came first.

Revision ID: 086_onboarding_course
Revises: 085_resource_extracted_text
"""

import sqlalchemy as sa

from alembic import op

revision = "086_onboarding_course"
down_revision = "085_resource_extracted_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "LearningProfile",
        sa.Column("onboardingCourseId", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("LearningProfile", "onboardingCourseId")
