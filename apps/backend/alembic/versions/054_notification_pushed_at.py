"""Record whether a notification actually reached a device.

Part of making the notification path trustworthy. Four phases of this programme have now shipped features
whose only way of reaching a learner is a notification, and each one had to write a defensive comment
explaining that the message might silently vanish. This is the column that lets delivery stop lying.

**`deliveredAt` and a push are not the same event**, and conflating them is the mistake this prevents.
`deliveredAt` means the row was released into the learner's in-app list — a real channel, and one that
always succeeds. A push is a second, best-effort channel that can be honestly skipped: Firebase
unconfigured, the learner opted out through `UserPreferences`, or — universally today — no `DeviceToken` row
exists, because nothing in the codebase registers one. `send_push_notification` reports that case as
`no_tokens`, and marking a row as pushed on the strength of a truthy return would put a claim in the
database that nothing sent anything to.

It is also the idempotency marker for the send. `status` moving off `PENDING` is what stops a row being
selected by the delivery sweep, so without a separate column a crash between the send and the status write
would push the same message again five minutes later. With it, the sweep marks the row delivered *first* and
pushes second: a crash then loses a push rather than repeating one, which is the right way round for
something that buzzes a phone in someone's pocket.

Nullable, no backfill, no default. Null means "no push reached a device", which is true of every row that
already exists — there has never been a push on the learning path. Backfilling from `deliveredAt` would
assert deliveries that never happened.

Revision ID: 054_notification_push
Revises: 053_goal_lifecycle
Create Date: 2026-08-27

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the DDL
then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError` about
a value nobody wrote — see `046_schedule_block_completion`. This one is 20 characters.
"""

import sqlalchemy as sa

from alembic import op

revision = "054_notification_push"
down_revision = "053_goal_lifecycle"
branch_labels = None
depends_on = None

TABLE = "Notification"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("pushedAt", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "pushedAt")
