"""Add email suppression and replay-safe provider event ingestion.

Two gaps this closes, both of which only matter once email actually sends — which it now does.

`EmailSuppression` is the hard stop a provider asks for. A hard bounce or a spam complaint is a
statement about the mailbox, so it has to outrank per-category preferences; continuing to send
to an address the provider has already rejected is how a sending domain gets throttled or
blocked, and the damage is shared by every learner including the ones who do want mail.

`EmailProviderEvent` makes webhook ingestion idempotent. Providers retry, and they do not
promise ordering: without a uniqueness constraint on their own event id, a retried `bounced`
suppresses twice, and a late `delivered` arriving after a `bounced` would overwrite a real
failure with a success.

Neither table stores an address in plaintext. Both are checked and correlated with the address
already in hand, so a SHA-256 hash answers every question they are asked.
"""

import sqlalchemy as sa

from alembic import op

revision = "064_email_suppression_events"
down_revision = "063_notification_email_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "EmailSuppression",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("addressHash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("providerEventId", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("releasedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "reason IN ('HARD_BOUNCE', 'COMPLAINT', 'UNSUBSCRIBE', 'MANUAL')",
            name="EmailSuppression_reason_check",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique: one *active* suppression per address, while released rows stay as history.
    op.create_index(
        "EmailSuppression_addressHash_active_key",
        "EmailSuppression",
        ["addressHash"],
        unique=True,
        postgresql_where=sa.text('"releasedAt" IS NULL'),
    )
    op.create_index("EmailSuppression_addressHash_idx", "EmailSuppression", ["addressHash"])

    op.create_table(
        "EmailProviderEvent",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("providerEventId", sa.String(), nullable=False),
        sa.Column("eventType", sa.String(length=48), nullable=False),
        sa.Column("providerMessageId", sa.String(), nullable=True),
        sa.Column("addressHash", sa.String(length=64), nullable=True),
        sa.Column("deliveryId", sa.String(), nullable=True),
        sa.Column("occurredAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["deliveryId"], ["NotificationDelivery.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "providerEventId", name="EmailProviderEvent_provider_providerEventId_key"
        ),
    )
    op.create_index(
        "EmailProviderEvent_providerMessageId_idx", "EmailProviderEvent", ["providerMessageId"]
    )
    op.create_index("EmailProviderEvent_occurredAt_idx", "EmailProviderEvent", ["occurredAt"])


def downgrade() -> None:
    op.drop_index("EmailProviderEvent_occurredAt_idx", table_name="EmailProviderEvent")
    op.drop_index("EmailProviderEvent_providerMessageId_idx", table_name="EmailProviderEvent")
    op.drop_table("EmailProviderEvent")
    op.drop_index("EmailSuppression_addressHash_idx", table_name="EmailSuppression")
    op.drop_index("EmailSuppression_addressHash_active_key", table_name="EmailSuppression")
    op.drop_table("EmailSuppression")
