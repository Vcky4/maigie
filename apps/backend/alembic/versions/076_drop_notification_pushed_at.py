"""Drop Notification.pushedAt — the retired FCM path's write-only column.

`pushedAt` was written in exactly one place: the legacy FCM delivery sweep, which set it "only when a
device actually received something". Because every registered token was an Expo token and that sender
spoke FCM, a device was never actually addressed, so the column was null for every row that ever
existed and no code read it. Phase 7 removed the sweep and the FCM sender; this drops the column they
fed. Push evidence now lives on `NotificationDelivery`.

Verified before writing this revision: zero non-null `pushedAt` across the configured database, so the
drop loses no information. The downgrade re-adds the column as a nullable timestamp (empty, matching
its only ever state).
"""

import sqlalchemy as sa

from alembic import op

revision = "076_drop_notification_pushed_at"
down_revision = "075_points_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("Notification", "pushedAt")


def downgrade() -> None:
    op.add_column(
        "Notification",
        sa.Column("pushedAt", sa.DateTime(timezone=True), nullable=True),
    )
