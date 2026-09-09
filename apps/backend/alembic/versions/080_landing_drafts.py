"""Add LandingDraft — the anonymous starter setup built on the marketing site.

The public landing page ends in a wizard that asks a visitor what they are working toward, shows
them a sketch of the setup Maigie would build, and then asks them to sign up to keep it. Until now
that whole flow was client-local: the answers lived in React state and the signup link carried at
most a goal string, so the thing the visitor was persuaded by did not survive the hop to
`app.maigie.com`. This table is where it survives.

**Why a separate table and not an early LearningProfile.** Writing straight into the
personal-learning tables would create user-owned rows with no user, on tables where `userId` scoping
is the invariant every query relies on. Most visitors never convert, so the majority of those rows
would be permanent ghosts sitting in learners' own content. A draft is a different kind of object
with its own lifecycle, so it gets its own table and nothing crosses into the workspace until a real
account claims it.

**Why the token is hashed.** `tokenHash` holds a SHA-256 of a token that is returned exactly once,
at creation, and thereafter lives only in the visitor's browser and in the `?draft=` parameter on
the signup URL. The token is the sole authorisation for reading or claiming the row — there is no
account behind it — so storing it in the clear would make a database dump equivalent to a bearer
token for every unclaimed draft. Unique, because lookup is by token and a collision would be a
cross-visitor read.

**Why `expiresAt` is NOT NULL with no default.** Every draft expires; there is no such thing as a
permanent anonymous draft, and a nullable column would eventually hold one. The value is set by the
service (7 days out) rather than by a server default, because the TTL is a product decision that
should be visible in code and changeable without a migration.

**Why `generateCount` is a count.** The preview costs an LLM call on an unauthenticated endpoint, so
it needs a hard per-draft ceiling as well as a per-IP rate limit. A boolean would allow exactly one
attempt and punish a visitor who fixed a typo in their subject; a count allows the honest second
look and stops there.

Revision ID: 080_landing_drafts
Revises: 079_user_country
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "080_landing_drafts"
down_revision = "079_user_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "LandingDraft",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tokenHash", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=True),
        sa.Column("subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("goalsText", sa.Text(), nullable=True),
        sa.Column("examName", sa.String(), nullable=True),
        sa.Column("examDate", sa.Date(), nullable=True),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generateCount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claimedBy",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("schemaVersion", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'claimed', 'expired')",
            name="LandingDraft_status_check",
        ),
        sa.CheckConstraint(
            '(status <> \'claimed\') OR ("claimedBy" IS NOT NULL AND "claimedAt" IS NOT NULL)',
            name="LandingDraft_claimed_link_check",
        ),
    )
    # Unique rather than a plain index: the token is the identity of the row for every public read.
    op.create_index(
        "LandingDraft_tokenHash_key", "LandingDraft", ["tokenHash"], unique=True
    )
    op.create_index("LandingDraft_claimedBy_idx", "LandingDraft", ["claimedBy"])
    op.create_index(
        "LandingDraft_status_expiresAt_idx", "LandingDraft", ["status", "expiresAt"]
    )


def downgrade() -> None:
    op.drop_index("LandingDraft_status_expiresAt_idx", table_name="LandingDraft")
    op.drop_index("LandingDraft_claimedBy_idx", table_name="LandingDraft")
    op.drop_index("LandingDraft_tokenHash_key", table_name="LandingDraft")
    op.drop_table("LandingDraft")
