"""Points: a ledger earned by referring learners who stay, spent on passes.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.9, Decision O. One earned currency, one thing to spend it on. A
learner earns 100 points when someone they referred has genuinely studied — a billable operation on
seven distinct days — and spends them on a pass, never on the subscription.

**A ledger, not a balance column, and the distinction is the whole design.** Each grant expires 60
days after it is earned, so the truth is a set of dated signed entries and the balance is
`SUM(points) WHERE NOT expired`. A single `pointsBalance` integer cannot express per-grant expiry, so
it exists only as a cache the ledger can always rebuild — the same relationship the usage-window
columns have to `UsageEvent`, and for the same reason: the balance is read often and its truth is
elsewhere.

**The unique index is the anti-abuse mechanism, in the database rather than the service.** One grant
per referred learner, `(userId, kind, sourceRef) WHERE kind='referral_qualified'`. The qualification
job is idempotent only if this holds: it runs daily and re-evaluates everyone not yet qualified, so
without the constraint a job that ran twice, or two jobs that overlapped, would pay a referral twice.
Partial, because `redemption` and `expiry` entries share a `sourceRef` shape and must not collide with
it — a redemption's `sourceRef` is a `PlusPass.id`, an expiry's is the expiring entry's own id, and
neither is unique per user the way a qualification is.

**`expiresAt` is set on positive entries only.** A grant expires; a redemption and an expiry entry are
themselves the accounting of points leaving, and have nothing left to expire. That is why the FIFO
read and the sweep both filter on `kind` and `expiresAt` together rather than on sign alone.

`PlusPass.source` and the nullable `PlusPass.purchaseId` this depends on were created in migration 071
— a redeemed pass is a `PlusPass` with `source='points'` and no purchase behind it (Decision O), which
is why 071 made that column nullable rather than this migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "073_points_ledger"
down_revision = "072_drop_credit_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PointsLedgerEntry",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Signed: +100 grant, -100 redemption, -40 expiry. The sign and the `kind` together are the
        # whole vocabulary — a positive `referral_qualified`, a negative `redemption`, a negative
        # `expiry`, and an `adjustment` of either sign for support.
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        # Set on positive entries only. Null on a redemption or an expiry, which are records of points
        # already leaving and have nothing to expire.
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=True),
        # The referred learner's id for a grant, the redeemed PlusPass.id for a redemption, or the
        # expiring entry's own id for an expiry. What the entry is *about*.
        sa.Column("sourceRef", sa.String(), nullable=True),
        # Support-visible reason, for `adjustment` entries.
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "PointsLedgerEntry_userId_createdAt_idx", "PointsLedgerEntry", ["userId", "createdAt"]
    )
    # For the FIFO read (oldest live grant first) and the expiry sweep, both of which walk a learner's
    # entries by expiry.
    op.create_index(
        "PointsLedgerEntry_userId_expiresAt_idx", "PointsLedgerEntry", ["userId", "expiresAt"]
    )
    # One grant per referred learner, owned by the database because the qualification job is idempotent
    # only if this holds. Partial, so it constrains grants alone — a redemption and an expiry share a
    # sourceRef shape and would otherwise collide with it.
    op.create_index(
        "PointsLedgerEntry_oneGrantPerReferral_idx",
        "PointsLedgerEntry",
        ["userId", "kind", "sourceRef"],
        unique=True,
        postgresql_where=sa.text("kind = 'referral_qualified'"),
    )

    # The cache over the ledger. Rebuildable from `SUM(points) WHERE NOT expired`, so a drift is a bug
    # in the writer rather than lost data.
    op.add_column(
        "User",
        sa.Column("pointsBalance", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("User", "pointsBalance")
    op.drop_index("PointsLedgerEntry_oneGrantPerReferral_idx", table_name="PointsLedgerEntry")
    op.drop_index("PointsLedgerEntry_userId_expiresAt_idx", table_name="PointsLedgerEntry")
    op.drop_index("PointsLedgerEntry_userId_createdAt_idx", table_name="PointsLedgerEntry")
    op.drop_table("PointsLedgerEntry")
