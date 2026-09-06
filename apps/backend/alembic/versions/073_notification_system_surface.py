"""Allow a SYSTEM surface on NotificationInteraction.

Outcome attribution needs to record when a learner actually did the thing a notification asked
for — an ACTIONED interaction. Those interactions are inferred server-side from a state change (a
study block marked complete, a goal nudge answered), not reported by a client, so none of the
existing surfaces — WEB, IOS, ANDROID, EMAIL — is honest for them. SYSTEM names that provenance,
which keeps a server-observed action distinguishable from a click a client reported.

Widening a CHECK constraint is backwards-compatible: every existing row already satisfies the new,
looser predicate, so there is nothing to backfill and the down-revision only narrows it again
(safe because no SYSTEM rows exist before this revision).
"""

from alembic import op

revision = "073_notification_system_surface"
down_revision = "072_drop_credit_tables"
branch_labels = None
depends_on = None

_TABLE = "NotificationInteraction"
_CONSTRAINT = "NotificationInteraction_surface_check"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "surface IN ('WEB', 'IOS', 'ANDROID', 'EMAIL', 'SYSTEM')",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "surface IN ('WEB', 'IOS', 'ANDROID', 'EMAIL')",
    )
