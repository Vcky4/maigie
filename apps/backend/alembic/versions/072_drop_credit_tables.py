"""Drop the credit-pack tables. Decision H.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision H. `CreditPack` and `CreditPurchaseTransaction` described a
product withdrawn in Phase 1: a pack of credits cannot be priced honestly once a credit's value is a
rolling usage window rather than a stored balance. Everything that sold or fulfilled a pack was deleted
across Phases 1–3, and `PlusPurchase` (migration 071) records every pass and subscription purchase
instead.

**The drop is unconditional because the count is zero.** `scripts/count_legacy_commercial_state.py`
found no completed `CreditPurchaseTransaction` rows and no non-zero `purchasedCreditsBalance`, so there
is no purchase history to preserve and no union to write — the reconciliation of two purchase schemas
that revision 3 designed was for the benefit of rows that do not exist. `GET /billing/purchases` now
reads `PlusPurchase` only.

`downgrade` restores the tables' shape but not their rows, which is the same honest position as `067`:
there were no rows to lose, and inventing plausible-looking history on a downgrade would be worse than
admitting the emptiness.

**Order matters.** `CreditPurchaseTransaction` has a foreign key to `CreditPack`, so the child goes
first. Any lingering foreign key from `PlusPass.purchaseId` points at `PlusPurchase`, not here, so
nothing in the pass tables is touched.
"""

import sqlalchemy as sa

from alembic import op

revision = "072_drop_credit_tables"
down_revision = "071_plus_passes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("CreditPurchaseTransaction")
    op.drop_table("CreditPack")


def downgrade() -> None:
    # Shape only — there were no rows, and there is no way to restore data that never existed. The
    # column definitions mirror the Prisma-created originals so a re-migration lands on the same schema.
    op.create_table(
        "CreditPack",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("bonusCredits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priceUsdCents", sa.Integer(), nullable=False),
        sa.Column("priceNgnKobo", sa.Integer(), nullable=False),
        sa.Column("sortOrder", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("isActive", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("CreditPack_isActive_sortOrder_idx", "CreditPack", ["isActive", "sortOrder"])

    op.create_table(
        "CreditPurchaseTransaction",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("creditPackId", sa.String(), sa.ForeignKey("CreditPack.id"), nullable=False),
        sa.Column("creditsGranted", sa.Integer(), nullable=False),
        sa.Column("amountPaid", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("paymentProvider", sa.String(), nullable=False),
        sa.Column("providerReference", sa.String(), nullable=False, unique=True),
        sa.Column("sessionId", sa.String(), nullable=True),
        sa.Column("sessionExpiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("CreditPurchaseTransaction_userId_idx", "CreditPurchaseTransaction", ["userId"])
    op.create_index(
        "CreditPurchaseTransaction_providerReference_idx",
        "CreditPurchaseTransaction",
        ["providerReference"],
    )
