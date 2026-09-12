"""Bug Hunt: seasons, submissions, and a per-person reward ledger.

The paid testing programme (`docs/implementation/bug-hunt-program-plan.md`). Eight tables in two groups,
and the group boundary is the important part.

**Season-scoped** — `BugHuntProgram`, `BugHuntParticipant`, `BugHuntSubmission`, `BugHuntAttachment` —
carry a `programId`. Every value that could differ between Season 1 and Season 2 is a column on
`BugHuntProgram`: dates, budget, per-tester cap, minimum withdrawal, pass uplift, country allowlist,
submission limit, terms version, and the reward matrix itself. That is what makes opening a season data
entry rather than a deploy, which matters because a second season is already committed.

**Wallet** — `BugHuntWallet`, `BugHuntLedgerEntry`, `BugHuntPayoutAccount`, `BugHuntWithdrawal` — carry a
`userId` and no programme. This is the decision most likely to be questioned later, so: hanging the ledger
off a participation breaks the first time a second season opens. A tester who earns ₦900 in Season 1 and
does not withdraw it (likely — the minimum is ₦1 000) either has that money stranded behind a closed
season, or holds two balances with two minimums to clear and two withdrawal queues. Fixing it afterwards
would be a migration over rows representing debts to real people. So earning is scoped to a season and
holding is not: `BugHuntLedgerEntry.programId` is set on credits and **NULL on spends**, enforced by a
CHECK, because attributing a redemption to a season would count it against a budget it never spent.

**Why the reward matrix is JSONB on the row and not a constant in Python.** Amounts held in code mean
that changing Season 2's numbers silently rewrites what every closed season claims it paid, and that a
submission triaged after the next season opens is paid at rates that did not exist when it was reported.
On the row, a closed season keeps telling the truth. Six amounts do not justify a table.

**Three partial unique indexes carry invariants that application code cannot.**

- One `open` season at a time. Two would make "the current season" ambiguous for every read in this
  domain, including the ones that decide what a finding is worth.
- One `award` entry per submission. This is what makes triage idempotent under a double-clicked form, a
  retried request, or two staff working the queue at once. A count-then-insert holds no lock between the
  two steps and pays twice.
- One open withdrawal per tester, for the same reason: two concurrent requests would each see no open
  request and both debit a balance that will be transferred once.

`BugHuntWallet` deliberately holds nothing but identity. It exists as a row to `SELECT … FOR UPDATE`, so
a redemption and a withdrawal cannot both spend the same kobo. Locking a participation would lock the
wrong thing for a cross-season debit; locking `User` would reach outside this domain.

**Deletion asymmetry.** `BugHuntParticipant` cascades with the account — a participation with no person
is meaningless. `BugHuntSubmission` does not: its `userId` and `participantId` are `SET NULL`, so the
finding survives the reporter, exactly as `Feedback.userId` already does. Forgetting the tester must not
erase the bug they found, and cross-season `known_issue` marking depends on those rows persisting.

Money is kobo, everywhere, as `Integer`. No column in this migration holds a currency amount as anything
else, and none is nullable where an amount is required.

Revision ID: 083_bug_hunt
Revises: 082_educator_survey
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "083_bug_hunt"
down_revision = "082_educator_survey"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Season-scoped
    # -----------------------------------------------------------------------

    op.create_table(
        "BugHuntProgram",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("seasonNumber", sa.Integer(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("startsAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("endsAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("budgetKobo", sa.Integer(), nullable=False),
        sa.Column("awardedKobo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("perParticipantCapKobo", sa.Integer(), nullable=False),
        sa.Column("minWithdrawalKobo", sa.Integer(), nullable=False, server_default="100000"),
        sa.Column("passUpliftPercent", sa.Integer(), nullable=False, server_default="25"),
        sa.Column(
            "countryAllowlist",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{NG}",
        ),
        sa.Column("rewardMatrix", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rulesVersion", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submissionDailyLimit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'closed')", name="BugHuntProgram_status_check"
        ),
        sa.CheckConstraint('"endsAt" > "startsAt"', name="BugHuntProgram_window_check"),
        sa.CheckConstraint('"budgetKobo" >= 0', name="BugHuntProgram_budget_check"),
        sa.CheckConstraint('"awardedKobo" >= 0', name="BugHuntProgram_awarded_check"),
        # The budget is a ceiling the database holds, not a note in a spreadsheet. Raising it has to be
        # a deliberate edit to this row rather than something a generous triage afternoon does by
        # accident, and lowering it below what is already awarded is refused — money promised to a
        # tester cannot be un-promised by editing a number.
        sa.CheckConstraint(
            '"awardedKobo" <= "budgetKobo"', name="BugHuntProgram_awarded_within_budget_check"
        ),
        sa.CheckConstraint('"perParticipantCapKobo" > 0', name="BugHuntProgram_cap_check"),
        sa.CheckConstraint('"minWithdrawalKobo" > 0', name="BugHuntProgram_min_withdrawal_check"),
        # Capped at 90 rather than 100: a 100% uplift is a free pass, and a redemption rail that can be
        # configured to charge nothing is a redemption rail with no balance check.
        sa.CheckConstraint(
            '"passUpliftPercent" >= 0 AND "passUpliftPercent" <= 90',
            name="BugHuntProgram_uplift_check",
        ),
        sa.CheckConstraint('"rulesVersion" >= 1', name="BugHuntProgram_rules_version_check"),
        sa.CheckConstraint('"submissionDailyLimit" > 0', name="BugHuntProgram_daily_limit_check"),
    )
    op.create_index(
        "BugHuntProgram_seasonNumber_key", "BugHuntProgram", ["seasonNumber"], unique=True
    )
    op.create_index("BugHuntProgram_slug_key", "BugHuntProgram", ["slug"], unique=True)
    # At most one open season. A unique index over `status` restricted to the rows where it is 'open'
    # permits exactly one such row, and unlike a Python guard it holds under concurrency.
    op.create_index(
        "BugHuntProgram_one_open_key",
        "BugHuntProgram",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("BugHuntProgram_status_idx", "BugHuntProgram", ["status"])
    op.create_index("BugHuntProgram_seasonNumber_idx", "BugHuntProgram", ["seasonNumber"])

    op.create_table(
        "BugHuntParticipant",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("programId", sa.String(), nullable=False),
        sa.Column("userId", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attemptCount", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("carriedFromProgramId", sa.String(), nullable=True),
        sa.Column("acceptedRulesVersion", sa.Integer(), nullable=True),
        sa.Column("termsAcceptedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decidedByUserId", sa.Text(), nullable=True),
        sa.Column("decidedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejectionReason", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["programId"], ["BugHuntProgram.id"], ondelete="CASCADE"),
        # A participation with no person behind it means nothing, so it goes when the account goes.
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["carriedFromProgramId"], ["BugHuntProgram.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["decidedByUserId"], ["User.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("programId", "userId", name="BugHuntParticipant_program_user_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended')",
            name="BugHuntParticipant_status_check",
        ),
        sa.CheckConstraint(
            '"attemptCount" >= 1 AND "attemptCount" <= 2',
            name="BugHuntParticipant_attempt_check",
        ),
        # A decided participation must say when. Without this, a bug that set the status alone would
        # leave a rejection with no date, and "why was I turned down, and when" gets a shrug.
        sa.CheckConstraint(
            "(status = 'pending') OR (\"decidedAt\" IS NOT NULL) "
            'OR ("carriedFromProgramId" IS NOT NULL)',
            name="BugHuntParticipant_decision_stamp_check",
        ),
        # A rejection without a reason is what makes a programme feel arbitrary.
        sa.CheckConstraint(
            "(status <> 'rejected') OR (\"rejectionReason\" IS NOT NULL)",
            name="BugHuntParticipant_rejection_reason_check",
        ),
    )
    op.create_index(
        "BugHuntParticipant_programId_status_idx", "BugHuntParticipant", ["programId", "status"]
    )
    op.create_index("BugHuntParticipant_userId_idx", "BugHuntParticipant", ["userId"])

    op.create_table(
        "BugHuntSubmission",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("programId", sa.String(), nullable=False),
        sa.Column("participantId", sa.String(), nullable=True),
        sa.Column("userId", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("appVersion", sa.Text(), nullable=True),
        sa.Column("buildNumber", sa.Text(), nullable=True),
        sa.Column("deviceModel", sa.Text(), nullable=True),
        sa.Column("osVersion", sa.Text(), nullable=True),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("stepsToReproduce", sa.Text(), nullable=False),
        sa.Column("expectedResult", sa.Text(), nullable=False),
        sa.Column("actualResult", sa.Text(), nullable=False),
        sa.Column("reportedSeverity", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="submitted"),
        sa.Column("duplicateOfId", sa.String(), nullable=True),
        sa.Column("publicResponse", sa.Text(), nullable=True),
        sa.Column("adminNotes", sa.Text(), nullable=True),
        sa.Column("isApplication", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("triagedByUserId", sa.Text(), nullable=True),
        sa.Column("triagedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # No ondelete: Postgres refuses to drop a season that has findings attached. Seasons are closed,
        # never deleted.
        sa.ForeignKeyConstraint(["programId"], ["BugHuntProgram.id"]),
        # SET NULL on both, so deleting an account removes the participation but leaves the finding,
        # anonymised. The bug outlives the reporter.
        sa.ForeignKeyConstraint(["participantId"], ["BugHuntParticipant.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicateOfId"], ["BugHuntSubmission.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triagedByUserId"], ["User.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "platform IN ('web', 'android', 'ios')", name="BugHuntSubmission_platform_check"
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'in_review', 'accepted', 'rejected', 'duplicate', 'known_issue')",
            name="BugHuntSubmission_status_check",
        ),
        sa.CheckConstraint(
            "category IS NULL OR category IN ('bug', 'feedback')",
            name="BugHuntSubmission_category_check",
        ),
        # Severity and category must agree. A `bug` graded `standard`, or `feedback` graded `critical`,
        # would look up nothing in the reward matrix and silently award zero.
        sa.CheckConstraint(
            "severity IS NULL "
            "OR (category = 'bug' AND severity IN ('critical', 'high', 'medium', 'low')) "
            "OR (category = 'feedback' AND severity IN ('high_value', 'standard'))",
            name="BugHuntSubmission_severity_pairing_check",
        ),
        sa.CheckConstraint(
            '"reportedSeverity" IS NULL OR "reportedSeverity" IN '
            "('critical', 'high', 'medium', 'low')",
            name="BugHuntSubmission_reported_severity_check",
        ),
        # An accepted finding must be graded and stamped, because that is what the award is computed
        # from. Otherwise a triage bug can accept a submission with a null severity and pay ₦0 while
        # telling the tester they were accepted.
        sa.CheckConstraint(
            "(status <> 'accepted') OR "
            '(category IS NOT NULL AND severity IS NOT NULL AND "triagedAt" IS NOT NULL)',
            name="BugHuntSubmission_accepted_graded_check",
        ),
        # `duplicate` and `known_issue` both mean "this one instead", so both must say which one.
        sa.CheckConstraint(
            "(status NOT IN ('duplicate', 'known_issue')) OR (\"duplicateOfId\" IS NOT NULL)",
            name="BugHuntSubmission_duplicate_target_check",
        ),
        sa.CheckConstraint('"duplicateOfId" <> id', name="BugHuntSubmission_self_duplicate_check"),
    )
    op.create_index(
        "BugHuntSubmission_programId_status_idx", "BugHuntSubmission", ["programId", "status"]
    )
    op.create_index(
        "BugHuntSubmission_programId_platform_idx", "BugHuntSubmission", ["programId", "platform"]
    )
    op.create_index("BugHuntSubmission_participantId_idx", "BugHuntSubmission", ["participantId"])
    op.create_index(
        "BugHuntSubmission_userId_createdAt_idx", "BugHuntSubmission", ["userId", "createdAt"]
    )
    op.create_index("BugHuntSubmission_duplicateOfId_idx", "BugHuntSubmission", ["duplicateOfId"])
    op.create_index("BugHuntSubmission_status_idx", "BugHuntSubmission", ["status"])

    op.create_table(
        "BugHuntAttachment",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("submissionId", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("contentType", sa.Text(), nullable=False),
        sa.Column("sizeBytes", sa.Integer(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["submissionId"], ["BugHuntSubmission.id"], ondelete="CASCADE"),
        sa.CheckConstraint('"sizeBytes" > 0', name="BugHuntAttachment_size_check"),
    )
    op.create_index("BugHuntAttachment_submissionId_idx", "BugHuntAttachment", ["submissionId"])

    # -----------------------------------------------------------------------
    # Wallet — per user, spanning every season
    # -----------------------------------------------------------------------

    op.create_table(
        "BugHuntWallet",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("userId", sa.Text(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
    )
    op.create_index("BugHuntWallet_userId_key", "BugHuntWallet", ["userId"], unique=True)

    op.create_table(
        "BugHuntPayoutAccount",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("userId", sa.Text(), nullable=False),
        sa.Column("bankCode", sa.Text(), nullable=False),
        sa.Column("bankName", sa.Text(), nullable=False),
        # Ciphertext. The plaintext account number is returned by no endpoint and written to no log; the
        # person executing a transfer reads it from one super-admin view, which is audited.
        sa.Column("accountNumberEnc", sa.Text(), nullable=False),
        sa.Column("accountNumberLast4", sa.String(), nullable=False),
        sa.Column("accountName", sa.Text(), nullable=False),
        sa.Column("verifiedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            'char_length("accountNumberLast4") = 4', name="BugHuntPayoutAccount_last4_check"
        ),
    )
    op.create_index(
        "BugHuntPayoutAccount_userId_key", "BugHuntPayoutAccount", ["userId"], unique=True
    )

    op.create_table(
        "BugHuntWithdrawal",
        sa.Column("id", sa.String(), primary_key=True),
        # SET NULL on both, not CASCADE. A completed cash payout is a financial record and outlives the
        # account: real money left the business account and has to be accountable for years. Deleting
        # the tester anonymises the payout rather than erasing it, and the snapshot columns below keep
        # the orphaned row reconcilable against a bank statement. The wallet and its ledger *do* cascade
        # away, which is right — a balance with nobody to pay is not a balance.
        sa.Column("walletId", sa.String(), nullable=True),
        sa.Column("userId", sa.Text(), nullable=True),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="requested"),
        sa.Column("payoutAccountId", sa.String(), nullable=True),
        # Snapshotted at request time, so a historic payout stays reconcilable against a bank statement
        # after the retention sweep deletes the account row — and without keeping the full number.
        sa.Column("bankNameSnapshot", sa.Text(), nullable=True),
        sa.Column("accountNameSnapshot", sa.Text(), nullable=True),
        sa.Column("accountLast4Snapshot", sa.String(), nullable=True),
        sa.Column("providerReference", sa.Text(), nullable=True),
        sa.Column("decidedByUserId", sa.Text(), nullable=True),
        sa.Column("decidedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paidAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejectionReason", sa.Text(), nullable=True),
        sa.Column("financeEntryId", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["walletId"], ["BugHuntWallet.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["payoutAccountId"], ["BugHuntPayoutAccount.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'paid', 'rejected')",
            name="BugHuntWithdrawal_status_check",
        ),
        sa.CheckConstraint('"amountKobo" > 0', name="BugHuntWithdrawal_amount_check"),
        # A payout claiming to be paid must carry the bank reference and the date. Without both it cannot
        # be matched to a statement line, which makes it indistinguishable from one that never happened —
        # and this is the only rail by which a tester actually receives cash.
        sa.CheckConstraint(
            '(status <> \'paid\') OR ("providerReference" IS NOT NULL AND "paidAt" IS NOT NULL)',
            name="BugHuntWithdrawal_paid_evidence_check",
        ),
        sa.CheckConstraint(
            "(status <> 'rejected') OR (\"rejectionReason\" IS NOT NULL)",
            name="BugHuntWithdrawal_rejection_reason_check",
        ),
    )
    # One open request per tester. The alternative — count the open requests, then insert — holds no lock
    # between the two steps, so two concurrent requests each see zero and both debit a balance that will
    # be transferred once.
    op.create_index(
        "BugHuntWithdrawal_one_open_key",
        "BugHuntWithdrawal",
        ["userId"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'approved')"),
    )
    op.create_index(
        "BugHuntWithdrawal_status_createdAt_idx", "BugHuntWithdrawal", ["status", "createdAt"]
    )
    op.create_index("BugHuntWithdrawal_userId_idx", "BugHuntWithdrawal", ["userId"])

    # Last, because it references both BugHuntSubmission and BugHuntWithdrawal.
    op.create_table(
        "BugHuntLedgerEntry",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("walletId", sa.String(), nullable=False),
        sa.Column("userId", sa.Text(), nullable=False),
        # Set on credits, NULL on spends. See the module docstring: attributing a redemption to a season
        # would count it against a budget it never spent.
        sa.Column("programId", sa.String(), nullable=True),
        sa.Column("participantId", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("amountKobo", sa.Integer(), nullable=False),
        sa.Column("submissionId", sa.String(), nullable=True),
        sa.Column("withdrawalId", sa.String(), nullable=True),
        # `PlusPass.id`, as a plain string. A cross-domain foreign key would tie this table's writes to
        # billing's schema for no gain: the id answers "which pass did this buy", and billing owns the
        # pass's lifecycle.
        sa.Column("passId", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("createdByUserId", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["walletId"], ["BugHuntWallet.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["programId"], ["BugHuntProgram.id"]),
        sa.ForeignKeyConstraint(["participantId"], ["BugHuntParticipant.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submissionId"], ["BugHuntSubmission.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["withdrawalId"], ["BugHuntWithdrawal.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["createdByUserId"], ["User.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "kind IN ('award', 'adjustment', 'pass_redemption', 'pass_redemption_reversal', "
            "'withdrawal', 'withdrawal_reversal')",
            name="BugHuntLedgerEntry_kind_check",
        ),
        sa.CheckConstraint('"amountKobo" <> 0', name="BugHuntLedgerEntry_nonzero_check"),
        # Sign follows kind. `adjustment` is exempt because a super admin correcting an over-award needs
        # to be able to claw it back.
        sa.CheckConstraint(
            "(kind = 'adjustment') "
            "OR (kind IN ('award', 'pass_redemption_reversal', 'withdrawal_reversal') "
            'AND "amountKobo" > 0) '
            "OR (kind IN ('pass_redemption', 'withdrawal') AND \"amountKobo\" < 0)",
            name="BugHuntLedgerEntry_sign_check",
        ),
        # An award must say which season and which finding earned it, or the per-tester cap and the
        # season budget are computed from an incomplete set and nobody notices until the numbers are
        # wrong.
        sa.CheckConstraint(
            '(kind <> \'award\') OR ("programId" IS NOT NULL AND "submissionId" IS NOT NULL)',
            name="BugHuntLedgerEntry_award_attribution_check",
        ),
        # The other half of that rule: a spend belongs to no season.
        sa.CheckConstraint(
            "(kind NOT IN ('pass_redemption', 'pass_redemption_reversal', 'withdrawal', "
            "'withdrawal_reversal')) OR (\"programId\" IS NULL)",
            name="BugHuntLedgerEntry_spend_unattributed_check",
        ),
        sa.CheckConstraint(
            "(kind NOT IN ('withdrawal', 'withdrawal_reversal')) OR (\"withdrawalId\" IS NOT NULL)",
            name="BugHuntLedgerEntry_withdrawal_link_check",
        ),
    )
    # One award per finding, enforced by the database. This is what makes triage idempotent under a
    # double-submitted form, a retried request, or two staff working the queue at the same time.
    op.create_index(
        "BugHuntLedgerEntry_award_once_key",
        "BugHuntLedgerEntry",
        ["submissionId"],
        unique=True,
        postgresql_where=sa.text("kind = 'award'"),
    )
    op.create_index(
        "BugHuntLedgerEntry_walletId_createdAt_idx", "BugHuntLedgerEntry", ["walletId", "createdAt"]
    )
    op.create_index("BugHuntLedgerEntry_userId_idx", "BugHuntLedgerEntry", ["userId"])
    op.create_index(
        "BugHuntLedgerEntry_programId_kind_idx", "BugHuntLedgerEntry", ["programId", "kind"]
    )
    op.create_index("BugHuntLedgerEntry_submissionId_idx", "BugHuntLedgerEntry", ["submissionId"])


def downgrade() -> None:
    # Reverse creation order: the ledger references submissions and withdrawals, withdrawals reference
    # wallets and payout accounts.
    op.drop_table("BugHuntLedgerEntry")
    op.drop_table("BugHuntWithdrawal")
    op.drop_table("BugHuntPayoutAccount")
    op.drop_table("BugHuntWallet")
    op.drop_table("BugHuntAttachment")
    op.drop_table("BugHuntSubmission")
    op.drop_table("BugHuntParticipant")
    op.drop_table("BugHuntProgram")
