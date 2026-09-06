"""Give EMAIL deliveries an addressable destination and duplicate protection.

Mobile push rows are made unique by their `destinationId`, which points at a
`PushInstallation`. Email has no installation row, so before this migration two email
deliveries for the same notification were indistinguishable to the database: the existing
partial unique index only covers `destinationId IS NOT NULL`. A retried planner, a replayed
task, or two workers racing could each insert one and the learner would receive the same
message twice.

`destinationRef` carries a SHA-256 hash of the lowercased recipient address, so an attempt
can be correlated with the address it was sent to without the ledger storing anyone's email
in plaintext. It is deliberately not a foreign key: the address belongs to the identity
domain and may change, and the point of the snapshot is to record what was true when the
delivery was planned.
"""

import sqlalchemy as sa

from alembic import op

revision = "063_notification_email_channel"
down_revision = "062_chat_generation_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "NotificationDelivery",
        sa.Column("destinationRef", sa.String(length=64), nullable=True),
    )
    # One delivery per notification per channel for destination-less channels, which today
    # means EMAIL. Partial so it cannot collide with the push index above it.
    op.create_index(
        "NotificationDelivery_notification_channel_no_destination_key",
        "NotificationDelivery",
        ["notificationId", "channel"],
        unique=True,
        postgresql_where=sa.text('"destinationId" IS NULL'),
    )
    # The dispatcher claims by (channel, provider, status, nextAttemptAt); the existing
    # index leads with status only, which makes the email claim scan push rows too.
    op.create_index(
        "NotificationDelivery_channel_status_nextAttemptAt_idx",
        "NotificationDelivery",
        ["channel", "status", "nextAttemptAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "NotificationDelivery_channel_status_nextAttemptAt_idx",
        table_name="NotificationDelivery",
    )
    op.drop_index(
        "NotificationDelivery_notification_channel_no_destination_key",
        table_name="NotificationDelivery",
    )
    op.drop_column("NotificationDelivery", "destinationRef")
