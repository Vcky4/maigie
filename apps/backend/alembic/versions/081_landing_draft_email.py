"""Add LandingDraft.email — the visitor's address, captured in the wizard.

The wizard now asks for an email before showing the starter preview, so an abandoned draft is still
a reachable person rather than an anonymous row. It is deliberately nullable: drafts created before
this migration have none, and a draft opened by the first goal click exists before the email step.

Indexed because the queries this column exists for are "unclaimed drafts for this address" and
"drafts to follow up", both of which look up by email rather than scanning.

Revision ID: 081_landing_draft_email
Revises: 080_landing_drafts
"""

import sqlalchemy as sa

from alembic import op

revision = "081_landing_draft_email"
down_revision = "080_landing_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("LandingDraft", sa.Column("email", sa.String(), nullable=True))
    op.create_index("LandingDraft_email_idx", "LandingDraft", ["email"])


def downgrade() -> None:
    op.drop_index("LandingDraft_email_idx", table_name="LandingDraft")
    op.drop_column("LandingDraft", "email")
