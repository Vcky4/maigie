"""Drop the retired per-notification preference columns from UserPreferences.

`notifications`, `emailMorningSchedule`, `emailScheduleReminder`, `emailWeeklyTips`,
`pushScheduleReminder`, and `pushStudyTips` were the pre-platform notification toggles. Notification
consent now lives entirely in `NotificationPolicy` (engagement + timing) and `NotificationPreference`
(per category/channel), which migration 077 materialized for every user. The notifications domain was
rewired off these columns (dispatch master gate, settings read, digest subscriptions, email plan) and
the settings write stopped dual-writing them; the identity preferences API no longer exposes them and
no client sends them. This drops the now-unread columns.

The downgrade re-adds them as nullable-with-default booleans, matching their original definition. It
cannot restore per-user values — those were never authoritative again after 077 — but the columns
return empty-and-defaulted, which is their only meaningful prior state for a rollback.
"""

import sqlalchemy as sa

from alembic import op

revision = "078_drop_legacy_pref_columns"
down_revision = "077_materialize_category_prefs"
branch_labels = None
depends_on = None

_COLUMNS = (
    "notifications",
    "emailMorningSchedule",
    "emailScheduleReminder",
    "emailWeeklyTips",
    "pushScheduleReminder",
    "pushStudyTips",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.drop_column("UserPreferences", column)


def downgrade() -> None:
    for column in _COLUMNS:
        op.add_column(
            "UserPreferences",
            sa.Column(column, sa.Boolean(), server_default="true", nullable=False),
        )
