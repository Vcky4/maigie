"""Resources keep the text of the file they point at.

A course material was a download link and nothing else: `add_course_material` handed the
upload straight to storage and wrote a `Resource` row holding a filename and a URL. Nothing
ever read the bytes, so the syllabus a learner attached to a course could not inform anything
the course generated from it.

That is the gap this closes. `Resource.extractedText` is the course-side equivalent of
`PrepMaterial.extractedText`, which has always existed for exactly this reason — preparation
topic extraction reads it — and it lets the same `prep_material_context` budgeting select
course material for an outline prompt.

**A column rather than `metadata`.** `Resource.metadata` is JSON and would have avoided a
migration, but it is published: `ResourceResponse.metadata` is serialised to clients on every
resource listing the course page reads. Twenty thousand characters of extracted PDF would then
ride along with every card in that list. `ResourceResponse` names its fields explicitly, so a
new column is invisible to the API until something asks for it — which is the property we want
for a payload this size.

Nullable with no default and no backfill. Existing resources genuinely have no extracted text:
they are links, or they are files uploaded before anything read them. `NULL` says that, and
`hasExtractedText`-style reads treat it as "contributes nothing", which is the truth.

Revision ID: 085_resource_extracted_text
Revises: 084_bug_hunt_pass_bonus
"""

import sqlalchemy as sa

from alembic import op

revision = "085_resource_extracted_text"
down_revision = "084_bug_hunt_pass_bonus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Resource", sa.Column("extractedText", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("Resource", "extractedText")
