"""Bug Hunt — SQLAlchemy models.

Eight tables in two groups, and the split between them is the single most important decision in this
domain: **season-scoped** tables carry a `programId`, **wallet** tables carry a `userId` and know
nothing about seasons.

**Why the wallet is not season-scoped.** The obvious model hangs the ledger off a participation, and
it breaks the first time a second season opens. A tester who earns ₦900 in Season 1 and does not
withdraw it — likely, given a ₦1 000 minimum — either has that money stranded behind a closed season,
or ends up holding two balances with two minimums to clear and two withdrawal queues. Neither is
defensible to the person who is owed the money, and merging them afterwards is a migration over rows
that represent debts to real people. So: *earning* is scoped to a season, *holding and spending* are
not. Awards carry their `programId`; wallet-level debits carry `NULL`, because a pass redemption is
not attributable to a season and pretending otherwise would double-count it against that season's
budget.

**Why `BugHuntWallet` has no columns beyond identity.** It exists to be locked. A cross-season debit
needs one row to `SELECT … FOR UPDATE` so that a redemption and a withdrawal cannot both spend the
same kobo; locking a participation row would be locking the wrong thing, and locking `User` would
reach outside this domain to serialise unrelated writes.

**Deletion.** A `BugHuntParticipant` goes with its account (`CASCADE`) — a participation with no
person is meaningless. A `BugHuntSubmission` does **not**: its `userId` and `participantId` are
`SET NULL`, so the finding survives the reporter, exactly as `Feedback.userId` does. Forgetting the
tester should not erase the bug they found, and cross-season `known_issue` marking depends on those
rows still being there.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# Vocabularies
#
# Held here as frozensets and mirrored as CHECK constraints in migration 083. Two copies, deliberately:
# the constraint is what makes a bad row impossible, and the frozenset is what lets a route reject it
# with a readable message instead of an IntegrityError.
# ---------------------------------------------------------------------------

PROGRAM_STATUSES = frozenset({"draft", "open", "closed"})

PARTICIPANT_STATUSES = frozenset({"pending", "approved", "rejected", "suspended"})

PLATFORMS = frozenset({"web", "android", "ios"})

SUBMISSION_STATUSES = frozenset(
    {"submitted", "in_review", "accepted", "rejected", "duplicate", "known_issue"}
)

#: Outcomes that pay nothing. `duplicate` means somebody else reported it *this season*;
#: `known_issue` means we already knew and have not fixed it, which is an admission about our backlog
#: rather than a judgement on the reporter. Same ₦0, different copy — and the distinction only exists
#: because seasons recur.
UNPAID_SUBMISSION_STATUSES = frozenset({"rejected", "duplicate", "known_issue"})

CATEGORIES = frozenset({"bug", "feedback"})

BUG_TYPES = frozenset({"crash", "functional", "data_loss", "security", "performance", "ui"})
FEEDBACK_TYPES = frozenset({"usability", "copy", "suggestion"})

BUG_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
#: Feedback is graded on value delivered, not on severity, so it carries its own two tiers in the same
#: column. One column with a pair-validity constraint beats two nullable columns that can disagree.
FEEDBACK_TIERS = frozenset({"high_value", "standard"})
SEVERITIES = BUG_SEVERITIES | FEEDBACK_TIERS

#: Credits are positive, debits negative, and `adjustment` is the one kind allowed either sign —
#: a super admin correcting an over-award needs to be able to claw it back.
LEDGER_CREDIT_KINDS = frozenset({"award", "pass_redemption_reversal", "withdrawal_reversal"})
LEDGER_DEBIT_KINDS = frozenset({"pass_redemption", "withdrawal"})
LEDGER_SIGNED_KINDS = frozenset({"adjustment"})
LEDGER_KINDS = LEDGER_CREDIT_KINDS | LEDGER_DEBIT_KINDS | LEDGER_SIGNED_KINDS

WITHDRAWAL_STATUSES = frozenset({"requested", "approved", "paid", "rejected"})
#: Statuses that hold money the tester cannot spend again. The partial unique index below is keyed on
#: exactly this set, which is what makes "one open request at a time" a database guarantee rather than
#: a check-then-insert race.
WITHDRAWAL_OPEN_STATUSES = frozenset({"requested", "approved"})


def _cuid() -> str:
    """A 25-character id, matching the Prisma-era ids the rest of this schema uses."""
    return uuid4().hex[:25]


# ===========================================================================
# Season-scoped
# ===========================================================================


class BugHuntProgram(Base, TimestampMixin):
    """One season, and **everything that differs between seasons**.

    The test for whether a value belongs on this row is simply: could Season 2 want it different? If
    yes, it is a column here and not a constant in Python. That is what makes opening a season a form
    in the admin dashboard rather than a release, which is the whole of Decision 12.
    """

    __tablename__ = "BugHuntProgram"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)

    #: Human-facing ordinal ("Season 2"). Unique, and the only place a season number is authoritative
    #: — it is never in a URL, a page title or an email subject, so there is nothing to keep in sync.
    season_number: Mapped[int] = mapped_column("seasonNumber", Integer, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    #: `draft` → `open` → `closed`. Status governs intake, not the wallet: closing a season stops
    #: submissions and nothing else, because an earned balance is permanent (§5.3).
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="draft", server_default="draft"
    )

    starts_at: Mapped[datetime] = mapped_column("startsAt", DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column("endsAt", DateTime(timezone=True), nullable=False)

    #: Season budget in kobo, and the running total awarded against it. `awardedKobo` is the **one**
    #: deliberate denormalisation in this domain: the budget is checked inside the award transaction,
    #: where a full aggregate over a growing ledger on every triage is the wrong shape. It is
    #: incremented in the same transaction as the entry it counts, so it cannot drift by one, and it
    #: is recomputable from the ledger if it ever does. Same reasoning `points_service` gives for
    #: `User.pointsBalance`.
    budget_kobo: Mapped[int] = mapped_column("budgetKobo", Integer, nullable=False)
    awarded_kobo: Mapped[int] = mapped_column(
        "awardedKobo", Integer, nullable=False, default=0, server_default="0"
    )

    #: Per-tester ceiling for this season. Reached, further accepted submissions are still recorded
    #: and still triaged — they are credited ₦0 with a stated reason, because the finding has value
    #: even when the budget line does not.
    per_participant_cap_kobo: Mapped[int] = mapped_column(
        "perParticipantCapKobo", Integer, nullable=False
    )

    #: Smallest cash request this season will accept, and the discount for taking a pass instead.
    #: The uplift is a percentage off the pass's catalogue price: 25 means a tester spends 75 kobo of
    #: balance per ₦1 of pass. Both per-season so they can be tuned between runs without a deploy.
    min_withdrawal_kobo: Mapped[int] = mapped_column(
        "minWithdrawalKobo", Integer, nullable=False, default=100_000, server_default="100000"
    )
    pass_uplift_percent: Mapped[int] = mapped_column(
        "passUpliftPercent", Integer, nullable=False, default=25, server_default="25"
    )

    #: ISO alpha-2 codes this season is open to. An array from the start, so widening past Nigeria is
    #: data rather than code — though a second country also needs a second payout method, which is
    #: not a config change.
    country_allowlist: Mapped[list[str]] = mapped_column(
        "countryAllowlist", ARRAY(Text), nullable=False, server_default="{NG}"
    )

    #: What each category × severity pays, in kobo. **On the row, not in code**: a matrix held as a
    #: module constant would mean editing Season 2's amounts silently rewrites what every closed
    #: season claims it paid. Seeded from `rewards.DEFAULT_REWARD_MATRIX` at creation; authoritative
    #: thereafter. Shape is `{"bug": {"critical": 200000, ...}, "feedback": {"standard": 50000, ...}}`.
    reward_matrix: Mapped[dict] = mapped_column("rewardMatrix", JSONB, nullable=False)

    #: Bumped whenever the terms change materially. Participants — including ones carried forward from
    #: a previous season — accept the version for the season they are taking part in, because consent
    #: to Season 1's amounts and dates is not consent to Season 2's.
    rules_version: Mapped[int] = mapped_column(
        "rulesVersion", Integer, nullable=False, default=1, server_default="1"
    )

    #: Anti-volume guard. A ₦500 floor rewards quantity if nothing pushes back, and this is the push.
    submission_daily_limit: Mapped[int] = mapped_column(
        "submissionDailyLimit", Integer, nullable=False, default=10, server_default="10"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'open', 'closed')", name="BugHuntProgram_status_check"
        ),
        CheckConstraint('"endsAt" > "startsAt"', name="BugHuntProgram_window_check"),
        CheckConstraint('"budgetKobo" >= 0', name="BugHuntProgram_budget_check"),
        CheckConstraint('"awardedKobo" >= 0', name="BugHuntProgram_awarded_check"),
        # The budget is a real ceiling, not a note in a spreadsheet. Raising it has to be a deliberate
        # act on this row rather than something an over-generous triage afternoon does by accident.
        CheckConstraint(
            '"awardedKobo" <= "budgetKobo"', name="BugHuntProgram_awarded_within_budget_check"
        ),
        CheckConstraint('"perParticipantCapKobo" > 0', name="BugHuntProgram_cap_check"),
        CheckConstraint('"minWithdrawalKobo" > 0', name="BugHuntProgram_min_withdrawal_check"),
        # Capped below 100 because a 100% uplift is a free pass, and a pass rail that can be
        # configured to charge nothing is a pass rail with no balance check.
        CheckConstraint(
            '"passUpliftPercent" >= 0 AND "passUpliftPercent" <= 90',
            name="BugHuntProgram_uplift_check",
        ),
        CheckConstraint('"rulesVersion" >= 1', name="BugHuntProgram_rules_version_check"),
        CheckConstraint('"submissionDailyLimit" > 0', name="BugHuntProgram_daily_limit_check"),
        # **At most one open season, enforced by Postgres.** A unique index over `status` restricted to
        # rows where it is `'open'` permits exactly one such row. Two open seasons would make
        # "the current season" ambiguous for every read in this domain — including the ones that decide
        # what a finding is worth — and a Python guard cannot prevent it under concurrency.
        Index(
            "BugHuntProgram_one_open_key",
            "status",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("BugHuntProgram_status_idx", "status"),
        Index("BugHuntProgram_seasonNumber_idx", "seasonNumber"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntProgram season={self.season_number} status={self.status}>"


class BugHuntParticipant(Base, TimestampMixin):
    """One person's participation in one season.

    Unique on `(programId, userId)`: one participation per season, several across seasons. That is the
    row a carry-forward creates, and it is why "have they taken part before" is answerable.
    """

    __tablename__ = "BugHuntParticipant"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)

    program_id: Mapped[str] = mapped_column(
        "programId", String, ForeignKey("BugHuntProgram.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )

    #: 1 for a first application, 2 for the one permitted retry after a rejection.
    attempt_count: Mapped[int] = mapped_column(
        "attemptCount", Integer, nullable=False, default=1, server_default="1"
    )

    #: Set when this participation was seeded from a previous season rather than applied for. Present
    #: so the app can tell a returning tester apart from a new one and never show them an application
    #: form — and so "how many carried over" is a query rather than a guess.
    carried_from_program_id: Mapped[str | None] = mapped_column(
        "carriedFromProgramId",
        String,
        ForeignKey("BugHuntProgram.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Which terms version they accepted, and when. Null for a carried-forward participant who has not
    #: yet re-accepted — which is exactly the state the app uses to show the acknowledgement screen.
    accepted_rules_version: Mapped[int | None] = mapped_column(
        "acceptedRulesVersion", Integer, nullable=True
    )
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        "termsAcceptedAt", DateTime(timezone=True), nullable=True
    )

    decided_by_user_id: Mapped[str | None] = mapped_column(
        "decidedByUserId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        "decidedAt", DateTime(timezone=True), nullable=True
    )
    #: Shown to the applicant verbatim. A rejection without a reason is the thing that makes a
    #: programme feel arbitrary, so this is required on the way in by the route.
    rejection_reason: Mapped[str | None] = mapped_column("rejectionReason", Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("programId", "userId", name="BugHuntParticipant_program_user_key"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended')",
            name="BugHuntParticipant_status_check",
        ),
        CheckConstraint(
            '"attemptCount" >= 1 AND "attemptCount" <= 2',
            name="BugHuntParticipant_attempt_check",
        ),
        # A decided participation must say who decided it and when. Without this, a bug that set the
        # status alone would leave a rejection with no author and no date, and the audit trail for
        # "why was I turned down" would be a shrug.
        CheckConstraint(
            "(status IN ('pending')) OR (\"decidedAt\" IS NOT NULL) "
            'OR ("carriedFromProgramId" IS NOT NULL)',
            name="BugHuntParticipant_decision_stamp_check",
        ),
        CheckConstraint(
            "(status <> 'rejected') OR (\"rejectionReason\" IS NOT NULL)",
            name="BugHuntParticipant_rejection_reason_check",
        ),
        Index("BugHuntParticipant_programId_status_idx", "programId", "status"),
        Index("BugHuntParticipant_userId_idx", "userId"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntParticipant id={self.id} program={self.program_id} status={self.status}>"


class BugHuntSubmission(Base, TimestampMixin):
    """One bug or piece of feedback, including the one that served as an application.

    Carries **no award amount**. The ledger is the only place a figure lives and list endpoints join
    it; a mirror here is one deploy away from disagreeing with the balance.
    """

    __tablename__ = "BugHuntSubmission"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)

    #: No `ondelete`, so Postgres refuses to drop a season that has findings attached. Seasons are
    #: closed, never deleted.
    program_id: Mapped[str] = mapped_column(
        "programId", String, ForeignKey("BugHuntProgram.id"), nullable=False
    )
    #: Both nullable and `SET NULL`: deleting the account removes the participation but leaves the
    #: finding, anonymised. `userId` is denormalised alongside `participantId` so a wallet-side query
    #: never has to walk participations to find someone's submissions.
    participant_id: Mapped[str | None] = mapped_column(
        "participantId",
        String,
        ForeignKey("BugHuntParticipant.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )

    # --- What was tested ---
    platform: Mapped[str] = mapped_column(String, nullable=False)
    app_version: Mapped[str | None] = mapped_column("appVersion", Text, nullable=True)
    build_number: Mapped[str | None] = mapped_column("buildNumber", Text, nullable=True)
    device_model: Mapped[str | None] = mapped_column("deviceModel", Text, nullable=True)
    os_version: Mapped[str | None] = mapped_column("osVersion", Text, nullable=True)
    #: Route or screen, as the tester describes it. Not a URL column: a mobile screen has no URL.
    route: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- What they found ---
    title: Mapped[str] = mapped_column(Text, nullable=False)
    steps_to_reproduce: Mapped[str] = mapped_column("stepsToReproduce", Text, nullable=False)
    expected_result: Mapped[str] = mapped_column("expectedResult", Text, nullable=False)
    actual_result: Mapped[str] = mapped_column("actualResult", Text, nullable=False)

    #: The tester's own severity guess, kept apart from the triaged one. Never used for money — it
    #: exists so we can see who calibrates well, and recognise a good reporter later.
    reported_severity: Mapped[str | None] = mapped_column("reportedSeverity", String, nullable=True)

    # --- Triage ---
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="submitted", server_default="submitted"
    )
    #: The canonical finding this duplicates. **Not constrained to the same season**: a bug reported
    #: in Season 1 and never fixed will be reported again in Season 2, and pointing at it is how a
    #: triager marks `known_issue`.
    duplicate_of_id: Mapped[str | None] = mapped_column(
        "duplicateOfId",
        String,
        ForeignKey("BugHuntSubmission.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Shown to the tester. `adminNotes` is not.
    public_response: Mapped[str | None] = mapped_column("publicResponse", Text, nullable=True)
    admin_notes: Mapped[str | None] = mapped_column("adminNotes", Text, nullable=True)

    #: True for the submission that doubled as an application. Approving the participant and triaging
    #: this row are separate acts, so this flag does not imply either outcome.
    is_application: Mapped[bool] = mapped_column(
        "isApplication", Boolean, nullable=False, default=False, server_default="false"
    )

    triaged_by_user_id: Mapped[str | None] = mapped_column(
        "triagedByUserId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    triaged_at: Mapped[datetime | None] = mapped_column(
        "triagedAt", DateTime(timezone=True), nullable=True
    )

    #: Eager-loadable, because every read of a submission wants its evidence: a detail view renders it and
    #: a list view shows a thumbnail count. Without the relationship those become a query per row.
    attachments: Mapped[list["BugHuntAttachment"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="BugHuntAttachment.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "platform IN ('web', 'android', 'ios')", name="BugHuntSubmission_platform_check"
        ),
        CheckConstraint(
            "status IN ('submitted', 'in_review', 'accepted', 'rejected', 'duplicate', 'known_issue')",
            name="BugHuntSubmission_status_check",
        ),
        CheckConstraint(
            "category IS NULL OR category IN ('bug', 'feedback')",
            name="BugHuntSubmission_category_check",
        ),
        # Severity and category have to agree. A `bug` graded `standard` or a `feedback` graded
        # `critical` would look up nothing in the reward matrix and award zero silently.
        CheckConstraint(
            "severity IS NULL "
            "OR (category = 'bug' AND severity IN ('critical', 'high', 'medium', 'low')) "
            "OR (category = 'feedback' AND severity IN ('high_value', 'standard'))",
            name="BugHuntSubmission_severity_pairing_check",
        ),
        CheckConstraint(
            '"reportedSeverity" IS NULL OR "reportedSeverity" IN '
            "('critical', 'high', 'medium', 'low')",
            name="BugHuntSubmission_reported_severity_check",
        ),
        # An accepted finding must be graded and stamped, because that is what the award is computed
        # from. Without this a triage bug could accept a submission with a null severity and pay ₦0
        # while telling the tester they were accepted.
        CheckConstraint(
            "(status <> 'accepted') OR "
            '(category IS NOT NULL AND severity IS NOT NULL AND "triagedAt" IS NOT NULL)',
            name="BugHuntSubmission_accepted_graded_check",
        ),
        CheckConstraint(
            "(status NOT IN ('duplicate', 'known_issue')) OR (\"duplicateOfId\" IS NOT NULL)",
            name="BugHuntSubmission_duplicate_target_check",
        ),
        CheckConstraint('"duplicateOfId" <> id', name="BugHuntSubmission_self_duplicate_check"),
        Index("BugHuntSubmission_programId_status_idx", "programId", "status"),
        Index("BugHuntSubmission_programId_platform_idx", "programId", "platform"),
        Index("BugHuntSubmission_participantId_idx", "participantId"),
        Index("BugHuntSubmission_userId_createdAt_idx", "userId", "createdAt"),
        Index("BugHuntSubmission_duplicateOfId_idx", "duplicateOfId"),
        Index("BugHuntSubmission_status_idx", "status"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntSubmission id={self.id} platform={self.platform} status={self.status}>"


class BugHuntAttachment(Base, TimestampMixin):
    """A screenshot or screen recording on a submission. Stored on BunnyCDN, referenced by URL."""

    __tablename__ = "BugHuntAttachment"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    submission_id: Mapped[str] = mapped_column(
        "submissionId",
        String,
        ForeignKey("BugHuntSubmission.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column("contentType", Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column("sizeBytes", Integer, nullable=False)

    submission: Mapped["BugHuntSubmission"] = relationship(back_populates="attachments")

    __table_args__ = (
        CheckConstraint('"sizeBytes" > 0', name="BugHuntAttachment_size_check"),
        Index("BugHuntAttachment_submissionId_idx", "submissionId"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntAttachment id={self.id} submission={self.submission_id}>"


# ===========================================================================
# Wallet — per user, spanning every season
# ===========================================================================


class BugHuntWallet(Base, TimestampMixin):
    """One tester's wallet. Holds no balance — it exists to be locked.

    Every debit takes a row lock here before summing the ledger, which is what stops a redemption and
    a withdrawal spending the same kobo concurrently. Keeping it as its own table rather than locking
    `User` confines that serialisation to this domain.
    """

    __tablename__ = "BugHuntWallet"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    def __repr__(self) -> str:
        return f"<BugHuntWallet id={self.id} user={self.user_id}>"


class BugHuntLedgerEntry(Base, TimestampMixin):
    """**The money.** Append-only, signed kobo, never updated and never deleted.

    A balance is `SUM(amountKobo)` over a wallet. There is no cached total, no `awardKobo` on the
    submission, and no reconciliation job — because there is nothing to reconcile against.

    Modelled directly on `billing.services.points_service` / `PointsLedgerEntry`, which is the same
    shape solving the same problem: grant, balance, history, redeem, with expiry and reversal written
    as new rows rather than as mutations of old ones. The differences here are that kobo do not
    expire, and that this ledger has a second debit rail (cash) that points does not.
    """

    __tablename__ = "BugHuntLedgerEntry"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)

    wallet_id: Mapped[str] = mapped_column(
        "walletId", String, ForeignKey("BugHuntWallet.id", ondelete="CASCADE"), nullable=False
    )
    #: Denormalised from the wallet so a per-user read needs no join.
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )

    #: **Set on credits, null on wallet-level debits.** An award belongs to the season that earned it,
    #: and that attribution is what the per-season cap and the budget are computed from. A redemption
    #: or a withdrawal belongs to no season: attributing one would count it against a budget it never
    #: spent. Enforced by a CHECK, not by convention.
    program_id: Mapped[str | None] = mapped_column(
        "programId", String, ForeignKey("BugHuntProgram.id"), nullable=True
    )
    participant_id: Mapped[str | None] = mapped_column(
        "participantId",
        String,
        ForeignKey("BugHuntParticipant.id", ondelete="SET NULL"),
        nullable=True,
    )

    kind: Mapped[str] = mapped_column(String, nullable=False)
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer, nullable=False)

    submission_id: Mapped[str | None] = mapped_column(
        "submissionId",
        String,
        ForeignKey("BugHuntSubmission.id", ondelete="SET NULL"),
        nullable=True,
    )
    withdrawal_id: Mapped[str | None] = mapped_column(
        "withdrawalId",
        String,
        ForeignKey("BugHuntWithdrawal.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: `PlusPass.id`, as a plain string rather than a foreign key. A cross-domain constraint would tie
    #: this table's writes to billing's schema for no gain — the id is here to answer "which pass did
    #: this buy", and billing owns the pass's own lifecycle.
    pass_id: Mapped[str | None] = mapped_column("passId", String, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The staff member who caused this entry, or null for one the system wrote.
    created_by_user_id: Mapped[str | None] = mapped_column(
        "createdByUserId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('award', 'adjustment', 'pass_redemption', 'pass_redemption_reversal', "
            "'withdrawal', 'withdrawal_reversal')",
            name="BugHuntLedgerEntry_kind_check",
        ),
        CheckConstraint('"amountKobo" <> 0', name="BugHuntLedgerEntry_nonzero_check"),
        # Sign follows kind. `adjustment` is exempt because a correction may go either way.
        CheckConstraint(
            "(kind = 'adjustment') "
            "OR (kind IN ('award', 'pass_redemption_reversal', 'withdrawal_reversal') "
            'AND "amountKobo" > 0) '
            "OR (kind IN ('pass_redemption', 'withdrawal') AND \"amountKobo\" < 0)",
            name="BugHuntLedgerEntry_sign_check",
        ),
        # An award must say which season and which finding earned it, or the cap and the budget are
        # computed from an incomplete set and nobody notices until the numbers are wrong.
        CheckConstraint(
            '(kind <> \'award\') OR ("programId" IS NOT NULL AND "submissionId" IS NOT NULL)',
            name="BugHuntLedgerEntry_award_attribution_check",
        ),
        # The other half of that rule: a spend is not attributable to a season.
        CheckConstraint(
            "(kind NOT IN ('pass_redemption', 'pass_redemption_reversal', 'withdrawal', "
            "'withdrawal_reversal')) OR (\"programId\" IS NULL)",
            name="BugHuntLedgerEntry_spend_unattributed_check",
        ),
        CheckConstraint(
            "(kind NOT IN ('withdrawal', 'withdrawal_reversal')) OR (\"withdrawalId\" IS NOT NULL)",
            name="BugHuntLedgerEntry_withdrawal_link_check",
        ),
        # One award per finding, enforced by the database. This is what makes triage idempotent: a
        # double-submitted triage form, a retried request, or two staff clicking at once all collapse
        # to a single payment. A check-then-insert in Python would not — there is no lock held between
        # the read and the write.
        Index(
            "BugHuntLedgerEntry_award_once_key",
            "submissionId",
            unique=True,
            postgresql_where=text("kind = 'award'"),
        ),
        Index("BugHuntLedgerEntry_walletId_createdAt_idx", "walletId", "createdAt"),
        Index("BugHuntLedgerEntry_userId_idx", "userId"),
        Index("BugHuntLedgerEntry_programId_kind_idx", "programId", "kind"),
        Index("BugHuntLedgerEntry_submissionId_idx", "submissionId"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntLedgerEntry id={self.id} kind={self.kind} amount={self.amount_kobo}>"


class BugHuntPayoutAccount(Base, TimestampMixin):
    """A tester's Nigerian bank details. Entered once, reused every season.

    **PII, treated as such.** The full account number is stored encrypted and returned by no endpoint;
    `accountNumberLast4` is what every screen shows. The person executing a transfer reads the full
    number from the super-admin withdrawal detail view, which is audited.
    """

    __tablename__ = "BugHuntPayoutAccount"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    bank_code: Mapped[str] = mapped_column("bankCode", Text, nullable=False)
    bank_name: Mapped[str] = mapped_column("bankName", Text, nullable=False)
    #: Ciphertext. Never the plaintext, and never logged.
    account_number_enc: Mapped[str] = mapped_column("accountNumberEnc", Text, nullable=False)
    account_number_last4: Mapped[str] = mapped_column("accountNumberLast4", String, nullable=False)
    account_name: Mapped[str] = mapped_column("accountName", Text, nullable=False)
    #: Set when a provider name-resolution confirmed the account belongs to who the tester says.
    verified_at: Mapped[datetime | None] = mapped_column(
        "verifiedAt", DateTime(timezone=True), nullable=True
    )
    #: Changing bank details holds new withdrawals for a day. An account-takeover that can redirect a
    #: payout instantly is worth more to an attacker than one that cannot.
    changed_at: Mapped[datetime | None] = mapped_column(
        "changedAt", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            'char_length("accountNumberLast4") = 4', name="BugHuntPayoutAccount_last4_check"
        ),
    )

    def __repr__(self) -> str:
        return f"<BugHuntPayoutAccount user={self.user_id} bank={self.bank_code}>"


class BugHuntWithdrawal(Base, TimestampMixin):
    """A cash request, and the record of the transfer that settled it.

    The money moves **by hand**: a super admin approves, transfers from the business account, then
    comes back and records the bank reference. `paidAt` is therefore a record of something that
    already happened, not a trigger — see §5.3 of the plan, and note that the admin control is
    labelled *Record payment* rather than *Pay* for exactly this reason.

    The ledger debit is written when the request is **created**, not when it is paid, so a pending
    request cannot be spent twice over.
    """

    __tablename__ = "BugHuntWithdrawal"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)

    #: **`SET NULL`, not `CASCADE`, on both.** A completed cash payout is a financial record, and
    #: financial records outlive accounts: we transferred real money out of the business account and
    #: have to be able to account for it years later. Deleting the tester therefore anonymises the
    #: payout rather than erasing it — the same treatment `BugHuntSubmission` gets, for a related
    #: reason. The wallet and its ledger *do* cascade away, which is correct: a balance with nobody to
    #: pay is not a balance. The `…Snapshot` columns below are what keep an orphaned row reconcilable.
    wallet_id: Mapped[str | None] = mapped_column(
        "walletId", String, ForeignKey("BugHuntWallet.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    amount_kobo: Mapped[int] = mapped_column("amountKobo", Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="requested", server_default="requested"
    )

    payout_account_id: Mapped[str | None] = mapped_column(
        "payoutAccountId",
        String,
        ForeignKey("BugHuntPayoutAccount.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Snapshotted from the payout account at request time. A historic payout has to stay reconcilable
    #: after the account row is deleted by the retention sweep — and these three fields are enough to
    #: reconcile against a bank statement without holding the full number for years.
    bank_name_snapshot: Mapped[str | None] = mapped_column("bankNameSnapshot", Text, nullable=True)
    account_name_snapshot: Mapped[str | None] = mapped_column(
        "accountNameSnapshot", Text, nullable=True
    )
    account_last4_snapshot: Mapped[str | None] = mapped_column(
        "accountLast4Snapshot", String, nullable=True
    )

    #: The bank's transaction reference. Required to mark a request paid: a payout with no reference
    #: cannot be matched to a statement line, which makes it indistinguishable from one that never
    #: happened.
    provider_reference: Mapped[str | None] = mapped_column("providerReference", Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        "decidedByUserId", Text, ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        "decidedAt", DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        "paidAt", DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column("rejectionReason", Text, nullable=True)
    #: The `finance` expense row this payout was mirrored into, so the programme's cash cost shows up
    #: in the same ledger as every other expense.
    finance_entry_id: Mapped[str | None] = mapped_column("financeEntryId", String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'approved', 'paid', 'rejected')",
            name="BugHuntWithdrawal_status_check",
        ),
        CheckConstraint('"amountKobo" > 0', name="BugHuntWithdrawal_amount_check"),
        CheckConstraint(
            "(status <> 'paid') OR " '("providerReference" IS NOT NULL AND "paidAt" IS NOT NULL)',
            name="BugHuntWithdrawal_paid_evidence_check",
        ),
        CheckConstraint(
            "(status <> 'rejected') OR (\"rejectionReason\" IS NOT NULL)",
            name="BugHuntWithdrawal_rejection_reason_check",
        ),
        # **One open request per tester, enforced by Postgres.** The alternative — count the open
        # requests, then insert — has no lock between the two steps, so two concurrent requests each
        # see zero and both succeed, and the wallet is debited twice for money that will be
        # transferred once.
        Index(
            "BugHuntWithdrawal_one_open_key",
            "userId",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved')"),
        ),
        Index("BugHuntWithdrawal_status_createdAt_idx", "status", "createdAt"),
        Index("BugHuntWithdrawal_userId_idx", "userId"),
    )

    def __repr__(self) -> str:
        return f"<BugHuntWithdrawal id={self.id} amount={self.amount_kobo} status={self.status}>"
