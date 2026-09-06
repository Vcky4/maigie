"""Replace the credit columns with a rolling usage window.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.2. Nine columns on `User` described the old meter: a monthly
period (`creditsPeriodStart`/`creditsPeriodEnd`) with a soft and a hard cap, a daily sub-period
(`creditsUsedToday`/`creditsDailyLimit`/`lastDailyReset`) that applied to FREE only, and a
`purchasedCreditsBalance` that the withdrawal of credit packs left with nothing to hold. Answering
one question — may this operation run? — required reading five interacting quantities, and the
learner-visible failure was "I ran out on the 9th and have three weeks of nothing".

Four columns replace them: a window that a billable operation opens and a later one rolls over, and
a calendar month that exists only as an abuse backstop. `usageWindowStartedAt` is nullable and null
means "elapsed", so the first operation opens the window and there is no initialisation step —
therefore no user who can be missing one.

**The drop is unconditional and the data is not carried forward.** `scripts/count_legacy_commercial_
state.py` confirmed zero payment relationships: 1 205 FREE users and one hand-set PREMIUM_MONTHLY
with no subscription behind it. There is no paying learner whose balance could be wronged, and the
old figures are denominated in a unit (a token, scaled by a 0.2 multiplier) that no longer exists —
converting them would mean inventing an exchange rate for a currency we are retiring. `downgrade`
restores the columns' shape but cannot restore their values, which is the honest position.

`LimitReachedEmailLog.periodEnd` becomes `windowDay`: the dedupe key moves to the calendar day
rather than to the window, because 4.8 windows a day would mean up to five emails daily where the
monthly period sent one. Existing rows are discarded rather than mapped — a `periodEnd` months in
the future would suppress today's email for a learner it was never about.
"""

import sqlalchemy as sa

from alembic import op

revision = "067_usage_windows"
down_revision = "066_notification_digests"
branch_labels = None
depends_on = None


_DROPPED_USER_COLUMNS = (
    "creditsUsed",
    "creditsPeriodStart",
    "creditsPeriodEnd",
    "creditsSoftCap",
    "creditsHardCap",
    "purchasedCreditsBalance",
    "creditsUsedToday",
    "creditsDailyLimit",
    "lastDailyReset",
)


def upgrade() -> None:
    op.add_column(
        "User",
        sa.Column("usageWindowStartedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "User",
        sa.Column(
            "usageWindowUnitsUsed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "User",
        sa.Column("usageMonthStartedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "User",
        sa.Column(
            "usageMonthUnitsUsed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    for column in _DROPPED_USER_COLUMNS:
        op.drop_column("User", column)

    # The old rows are keyed on a period that no longer exists, and a stale future `periodEnd` would
    # suppress a legitimate email. Clear first, then swap the key, so the NOT NULL column has no
    # rows to backfill.
    op.execute('DELETE FROM "LimitReachedEmailLog"')
    op.drop_index("LimitReachedEmailLog_userId_periodEnd_key", table_name="LimitReachedEmailLog")
    op.drop_column("LimitReachedEmailLog", "periodEnd")
    op.add_column(
        "LimitReachedEmailLog",
        sa.Column("windowDay", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "LimitReachedEmailLog_userId_windowDay_key",
        "LimitReachedEmailLog",
        ["userId", "windowDay"],
        unique=True,
    )


def downgrade() -> None:
    op.execute('DELETE FROM "LimitReachedEmailLog"')
    op.drop_index("LimitReachedEmailLog_userId_windowDay_key", table_name="LimitReachedEmailLog")
    op.drop_column("LimitReachedEmailLog", "windowDay")
    op.add_column(
        "LimitReachedEmailLog",
        sa.Column("periodEnd", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "LimitReachedEmailLog_userId_periodEnd_key",
        "LimitReachedEmailLog",
        ["userId", "periodEnd"],
        unique=True,
    )

    # Shape only. The values are gone, and a downgrade that invented plausible-looking balances
    # would be worse than one that admits the loss.
    op.add_column(
        "User", sa.Column("creditsUsed", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "User", sa.Column("creditsPeriodStart", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("User", sa.Column("creditsPeriodEnd", sa.DateTime(timezone=True), nullable=True))
    op.add_column("User", sa.Column("creditsSoftCap", sa.Integer(), nullable=True))
    op.add_column("User", sa.Column("creditsHardCap", sa.Integer(), nullable=True))
    op.add_column(
        "User",
        sa.Column("purchasedCreditsBalance", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "User", sa.Column("creditsUsedToday", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("User", sa.Column("creditsDailyLimit", sa.Integer(), nullable=True))
    op.add_column("User", sa.Column("lastDailyReset", sa.DateTime(timezone=True), nullable=True))

    op.drop_column("User", "usageMonthUnitsUsed")
    op.drop_column("User", "usageMonthStartedAt")
    op.drop_column("User", "usageWindowUnitsUsed")
    op.drop_column("User", "usageWindowStartedAt")
