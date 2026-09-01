"""Add digest runs and their membership, so a digest preference means something.

Until now a learner who asked for a weekly email got one only for notification types that are
themselves periodic; everything else was recorded as `DIGEST_NOT_SUPPORTED` and never sent. The
preference was honoured in the narrowest possible sense and silently ignored in the common one.

Two tables rather than a timestamp on `Notification`, because both questions need answering
independently: "has this period already been summarised for this learner?" and "has this
notification already been summarised?" The first is the digest's unique key, and it lets the
planner run hourly — necessary, since a week ends at a different moment in every timezone —
without sending the same week twice. The second is a global unique constraint on the item's
notification, so an item created near a period boundary or during a retry cannot be summarised in
two digests, which would leave a learner unable to tell one event from two.

`itemCount >= 1` is a constraint rather than a convention: a digest that says nothing happened
teaches its reader to ignore the sender, so an empty one must be impossible to record.
"""

import sqlalchemy as sa

from alembic import op

revision = "066_notification_digests"
down_revision = "065_outbound_message_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "NotificationDigest",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("periodStart", sa.DateTime(timezone=True), nullable=False),
        sa.Column("periodEnd", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notificationId", sa.String(), nullable=True),
        sa.Column("itemCount", sa.Integer(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("period IN ('DAILY', 'WEEKLY')", name="NotificationDigest_period_check"),
        sa.CheckConstraint('"itemCount" >= 1', name="NotificationDigest_itemCount_check"),
        sa.CheckConstraint(
            '"periodEnd" > "periodStart"', name="NotificationDigest_period_order_check"
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notificationId"], ["Notification.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "userId",
            "category",
            "period",
            "periodStart",
            name="NotificationDigest_user_category_period_start_key",
        ),
    )
    op.create_index(
        "NotificationDigest_userId_periodStart_idx",
        "NotificationDigest",
        ["userId", "periodStart"],
    )

    op.create_table(
        "NotificationDigestItem",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("digestId", sa.String(), nullable=False),
        sa.Column("notificationId", sa.String(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["digestId"], ["NotificationDigest.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notificationId"], ["Notification.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "digestId", "notificationId", name="NotificationDigestItem_digest_notification_key"
        ),
        sa.UniqueConstraint("notificationId", name="NotificationDigestItem_notificationId_key"),
    )
    op.create_index("NotificationDigestItem_digestId_idx", "NotificationDigestItem", ["digestId"])


def downgrade() -> None:
    op.drop_index("NotificationDigestItem_digestId_idx", table_name="NotificationDigestItem")
    op.drop_table("NotificationDigestItem")
    op.drop_index("NotificationDigest_userId_periodStart_idx", table_name="NotificationDigest")
    op.drop_table("NotificationDigest")
