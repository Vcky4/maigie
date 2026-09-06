"""Record evidence for transactional mail, which had none.

Engagement notifications get `NotificationDelivery` rows. The messages that deliberately bypass
consent — email verification, password reset, billing receipts, space invites — got nothing, so
"the reset code never arrived" was unanswerable: there was no way to tell a message never
attempted from one the provider refused from one that was accepted and lost downstream.

Not linked to `Notification`, because there is no notification and creating one would put a
security email into a learner's notification centre. `userId` is nullable because an invite is
addressed to a person who may not have an account yet.

Stores no content: the address only as a hash, and otherwise just the class of message, a purpose
label, and the provider's own outcome.
"""

import sqlalchemy as sa

from alembic import op

revision = "065_outbound_message_evidence"
down_revision = "064_email_suppression_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "OutboundMessage",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("messageClass", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("addressHash", sa.String(length=64), nullable=False),
        sa.Column("userId", sa.String(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("providerMessageId", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("errorCode", sa.String(length=64), nullable=True),
        sa.Column("errorDetail", sa.Text(), nullable=True),
        sa.Column("requestedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("durationMs", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "\"messageClass\" IN ('AUTH', 'SECURITY', 'BILLING', 'MEMBERSHIP', 'OPERATIONS')",
            name="OutboundMessage_messageClass_check",
        ),
        sa.CheckConstraint(
            "status IN ('ACCEPTED', 'FAILED', 'SKIPPED')",
            name="OutboundMessage_status_check",
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "OutboundMessage_addressHash_createdAt_idx",
        "OutboundMessage",
        ["addressHash", "createdAt"],
    )
    op.create_index(
        "OutboundMessage_userId_createdAt_idx", "OutboundMessage", ["userId", "createdAt"]
    )
    op.create_index(
        "OutboundMessage_purpose_createdAt_idx", "OutboundMessage", ["purpose", "createdAt"]
    )
    op.create_index(
        "OutboundMessage_providerMessageId_idx",
        "OutboundMessage",
        ["providerMessageId"],
        postgresql_where=sa.text('"providerMessageId" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index("OutboundMessage_providerMessageId_idx", table_name="OutboundMessage")
    op.drop_index("OutboundMessage_purpose_createdAt_idx", table_name="OutboundMessage")
    op.drop_index("OutboundMessage_userId_createdAt_idx", table_name="OutboundMessage")
    op.drop_index("OutboundMessage_addressHash_createdAt_idx", table_name="OutboundMessage")
    op.drop_table("OutboundMessage")
