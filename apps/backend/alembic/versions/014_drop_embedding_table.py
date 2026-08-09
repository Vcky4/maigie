"""Drop the Embedding table.

DESTRUCTIVE. Do not apply without explicit confirmation: it deletes 15 rows that
cannot be reconstructed from anything else in the database.

# Why it is dead

The table stores `vector` as `jsonb`. Postgres cannot index or perform a similarity
search over a JSON array, so nothing can query it by nearest neighbour, which is the
only thing an embedding is for. Combined with:

* no SQLAlchemy writer anywhere in `src` populates it;
* Pinecone, the vector store this fed, has been removed entirely;
* `rag_service` now reports `available = False` and retrieves nothing.

So the rows are unreachable by design, not merely unused.

# What replaces it

Nothing, yet. When retrieval returns it should use `pgvector` in this same database, so
an embedding and the row it describes commit in one transaction, with a real vector
column and an index. That is a new table with a different column type, not a revival of
this one, which is why this drops rather than alters.

# Reversibility

`downgrade` recreates the structure but **not the data**. Take a dump of the 15 rows
first if there is any chance they are wanted:

    COPY (SELECT * FROM "Embedding") TO STDOUT WITH CSV HEADER;

Revision ID: 014_drop_embedding_table
Revises: 013_add_quiz_session_topic_fk
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers
revision = "014_drop_embedding_table"
down_revision = "013_add_quiz_session_topic_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("Embedding")


def downgrade() -> None:
    # Structure only; the rows are not recoverable from here.
    op.create_table(
        "Embedding",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("objectType", sa.Text(), nullable=False),
        sa.Column("objectId", sa.Text(), nullable=False),
        sa.Column("vector", postgresql.JSONB(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updatedAt", sa.DateTime(), nullable=False),
        sa.Column("resourceId", sa.Text(), nullable=True),
        sa.Column("resourceBankItemId", sa.Text(), nullable=True),
    )
