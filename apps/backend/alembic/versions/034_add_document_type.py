"""Store the document type the learner chose, instead of discarding it.

`DocumentGenerateRequest.type` — "essay, report, presentation, letter, cv" — has always been sent by
the client, and has always been thrown away. It reaches `create_from_prompt`, shapes the prompt and
the token budget, names the activity-feed entry, and is then not among the fields written to
`GeneratedDocument`. §6.1 of the Learn plan is about exactly this: a request field accepted and
silently dropped.

The consequence was visible in the browser. The library page needed a type to group and label by, so
it inferred one:

    if (value.includes('report')) return 'report';
    if (value.includes('cv') || value.includes('resume')) return 'cv';

— substring matching on the title and filename, under a comment explaining that the API does not
persist a kind. So a report titled "Reporting standards" was a report by luck, an essay about
someone's CV was a CV, and the type filter could only ever filter the page already in the browser,
because the value it filtered on did not exist in the database to filter by.

## Nullable, and not backfilled

The fourteen existing rows have no recoverable type. Inferring one from their titles is precisely the
guesswork this column removes, and writing a guess into a column that reads as a fact is worse than
leaving it empty — a null says "not recorded", which is true, and clients can show the format
instead. New documents carry the real value from the request.

Nullable also fits the other writer: the chat skill generates a document from content the model
produced rather than from a chosen type, so it genuinely has none to record.

Revision ID: 034_add_document_type
Revises: 033_add_note_versions
Create Date: 2026-08-17

The revision id is 21 characters; `alembic_version.version_num` is `varchar(32)`.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "034_add_document_type"
down_revision = "033_add_note_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("GeneratedDocument", sa.Column("docType", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("GeneratedDocument", "docType")
