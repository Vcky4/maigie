"""Passes: the thing a learner can actually hold.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §8, Decisions A, C, E and G. Until now nothing could hold a pass, so
`entitlement_service._read_active_pass` returned `None` as a named seam and the pass branch of
`_compose` was written and tested against a shape that had no table behind it. This is the table.

**Numbered 071, not the `063` §8 names.** The plan was written when `062_chat_generation_attempt` was
head; four migrations have landed since, three of them Phase 3's. §8's instruction not to reuse `060`
or `061` still holds and now goes without saying.

**`status` and the partial unique index are the whole of Decision A.** A pass is inventory when bought
and starts its clock on activation, so a learner can buy a $0.99 five-hour pass on Tuesday and spend it
on Saturday's revision — which is the product. Exactly one may be active at a time, and that is
enforced here rather than in `pass_service`:

    CREATE UNIQUE INDEX "PlusPass_oneActivePerUser_idx" ON "PlusPass" ("userId") WHERE status = 'active'

A partial unique index rather than an application check because two concurrent activations must
produce one winner and one `409`, not a race whose loser leaves two rows looking active while only one
is cached on `User`. The service catches the `IntegrityError`; it does not try to prevent it.

**`durationMinutes` and `unitsAllowance` are snapshotted onto the row**, not looked up from the product
when read. Re-pricing or re-timing a product must never change a pass already sold, and it also lets a
market set its own allowance: §6.8 gives NGN learners 1 800 units on the 5-hour pass where §6.3 gives
2 000, and a snapshot is what makes that a property of the purchase rather than a branch in the reader.

**`unitsUsed` is a third counter, and Decision E needs it.** A pass ends when its wall clock ends *or*
when its allowance is spent, and the second condition is what stops the pass being a product that loses
money faster the more it is used — five hours of continuous live voice is about $6.00 of inference
against $0.75 of net revenue. The window and month counters on `User` cannot answer it: they reset, and
a pass total does not.

**`purchaseId` is nullable** because Phase 4b redeems passes from points, where nothing was purchased
(Decision O). `providerReference` on `PlusPurchase` is unique, and that uniqueness is the idempotency
key for the whole purchase path — webhook replay, client retry and an iOS `restore()` re-presenting the
same token all collapse onto one row (Decision G).

**What this migration does not do.** It does not drop `CreditPack` or `CreditPurchaseTransaction`.
Decision H drops them and reads purchase history from `PlusPurchase` only, but they still back a live
`GET /billing/credits/purchases`, and repointing that endpoint at a table nothing writes until Phase 5
is a separate change from creating the table. Both are zero-row operations either way; sequencing them
apart keeps each one reviewable.
"""

import sqlalchemy as sa

from alembic import op

revision = "071_plus_passes"
down_revision = "070_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PlusPurchase",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("productId", sa.String(), nullable=False),
        # `pass` | `subscription`. A `plus_voice_30` purchase is a `pass`-kind row with no `PlusPass`
        # behind it — the first purchase in the plan that grants no entitlement (Decision R).
        sa.Column("productKind", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        # **The idempotency key for every rail.** Decision G: verify, persist, then grant, in that
        # order, because StoreKit does not return finished consumables from `currentEntitlements` — a
        # reinstalled app cannot recover a purchased-but-unactivated pass from the device, so if the
        # server did not persist it at verification time the learner is owed a refund.
        sa.Column("providerReference", sa.String(), nullable=False, unique=True),
        # As charged, in the learner's currency. Not converted: §6.8 sets NGN prices independently
        # rather than by FX, so a stored USD equivalent would be a number nobody agreed to.
        sa.Column("amountMinor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refundedAt", sa.DateTime(timezone=True), nullable=True),
        # The verification response, kept for disputes. A refund argued six months later is argued
        # against what the provider actually said, not against our summary of it.
        sa.Column("rawPayload", sa.JSON(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("PlusPurchase_userId_createdAt_idx", "PlusPurchase", ["userId", "createdAt"])

    op.create_table(
        "PlusPass",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # `plus_pass_5h` | `plus_pass_7d` | `plus_pass_term`. **Never `plus_voice_30`** — a voice pack
        # is a balance, not a pass, and must not become a row here (Decision R).
        sa.Column("productId", sa.String(), nullable=False),
        sa.Column("durationMinutes", sa.Integer(), nullable=False),
        sa.Column("unitsAllowance", sa.Integer(), nullable=False),
        sa.Column("unitsUsed", sa.Integer(), nullable=False, server_default="0"),
        # `inventory` | `active` | `consumed` | `refunded`
        sa.Column("status", sa.String(), nullable=False, server_default="inventory"),
        # Nullable: null when `source='points'`, since nothing was purchased (Decision O).
        sa.Column(
            "purchaseId",
            sa.String(),
            sa.ForeignKey("PlusPurchase.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Both null while in inventory. That is what "the clock starts on activation" means in the
        # schema rather than in a comment.
        sa.Column("activatedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=True),
        # null | `expired` | `exhausted` | `refund`. Two ways for a pass to end and they need different
        # copy: "your five hours are up" and "you've used this pass's allowance" are different facts.
        sa.Column("endedReason", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="purchase"),
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
    op.create_index("PlusPass_userId_status_idx", "PlusPass", ["userId", "status"])
    # For the sweep, which asks "which active passes have expired" every five minutes.
    op.create_index("PlusPass_status_expiresAt_idx", "PlusPass", ["status", "expiresAt"])
    # Decision A's invariant, owned by the database.
    op.create_index(
        "PlusPass_oneActivePerUser_idx",
        "PlusPass",
        ["userId"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # Decision C: the active pass is denormalised onto `User` so `resolve()` stays one round trip.
    # `PlusPass` is still the record of truth; these have exactly one writer (`pass_service`) and the
    # sweep reconciles them. The same trade `Course.progress` already makes.
    op.add_column("User", sa.Column("activePlusPassId", sa.String(), nullable=True))
    op.add_column(
        "User", sa.Column("activePlusPassExpiresAt", sa.DateTime(timezone=True), nullable=True)
    )

    # Apple's cross-purchase identity. Unique because it is the account-collision defence: a token
    # already bound to another learner is `409 PURCHASE_ALREADY_CLAIMED`, which is the standard IAP
    # abuse vector and the constraint is the whole of the answer to it (Decision G).
    op.add_column("User", sa.Column("appleOriginalTransactionId", sa.String(), nullable=True))
    op.add_column("User", sa.Column("appleProductId", sa.String(), nullable=True))
    op.create_index(
        "User_appleOriginalTransactionId_key",
        "User",
        ["appleOriginalTransactionId"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("User_appleOriginalTransactionId_key", table_name="User")
    op.drop_column("User", "appleProductId")
    op.drop_column("User", "appleOriginalTransactionId")
    op.drop_column("User", "activePlusPassExpiresAt")
    op.drop_column("User", "activePlusPassId")

    op.drop_index("PlusPass_oneActivePerUser_idx", table_name="PlusPass")
    op.drop_index("PlusPass_status_expiresAt_idx", table_name="PlusPass")
    op.drop_index("PlusPass_userId_status_idx", table_name="PlusPass")
    op.drop_table("PlusPass")

    op.drop_index("PlusPurchase_userId_createdAt_idx", table_name="PlusPurchase")
    op.drop_table("PlusPurchase")
