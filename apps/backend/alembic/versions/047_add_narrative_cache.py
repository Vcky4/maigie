"""Add NarrativeCache — composed prose kept against the figures it was written about.

Three Reflect surfaces need a written interpretation of numbers they already publish: the growth
chart's drivers, a subject's strength/focus pair, and a goal's insight and next action. Each is read
on page load and each is Plus (Decision Z). Without storage, opening a goal would spend a language
model call, so the prose is composed once and reused.

**`inputsHash` is the invalidation, and there is no TTL.** The row is keyed by what the prose is
about and carries a fingerprint of the measured skeleton it was written from. A moved figure changes
the hash and misses; an unmoved figure has no new sentence to be written about it, so an expiry timer
would only buy a different sentence about the same number. That is the trade this makes explicitly.

**`entityId` and `scope` are NOT NULL, defaulting to `''`.** Postgres treats NULLs as distinct inside
a unique index, so `(userId, kind, NULL, NULL)` would admit unlimited duplicate rows for growth
drivers — the one kind with no entity — and the upsert would quietly become an insert. An empty
string is a real value and the constraint holds for every kind.

Revision ID: 047_add_narrative_cache
Revises: 046_schedule_block_completion
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "047_add_narrative_cache"
down_revision = "046_schedule_block_completion"
branch_labels = None
depends_on = None

TABLE = "NarrativeCache"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        # NOT NULL with an empty default: see the module docstring. A nullable column here would
        # silently disable the unique constraint for the learner-wide kinds.
        sa.Column("entityId", sa.String(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(), nullable=False, server_default=""),
        sa.Column("inputsHash", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
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

    op.create_index(f"{TABLE}_userId_idx", TABLE, ["userId"])
    # The read path and the writer's idempotency in one: every lookup is by the full key.
    op.create_unique_constraint(f"{TABLE}_key_key", TABLE, ["userId", "kind", "entityId", "scope"])


def downgrade() -> None:
    op.drop_constraint(f"{TABLE}_key_key", TABLE, type_="unique")
    op.drop_index(f"{TABLE}_userId_idx", table_name=TABLE)
    op.drop_table(TABLE)
