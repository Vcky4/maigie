"""
Billing domain — SQLAlchemy models.

ReferralReward, ReferralRewardClaim, AdRewardClaim, ResourceBankItem,
ResourceBankFile, ResourceBankReport, ResourceUploadReward,
ResourceUploadRewardClaim, UsageEvent, PlusPurchase, PlusPass.

Maps to existing PostgreSQL tables created by Prisma.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# ReferralReward
# ---------------------------------------------------------------------------


class ReferralReward(Base, TimestampMixin):
    __tablename__ = "ReferralReward"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    referrer_id: Mapped[str] = mapped_column(
        "referrerId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    referred_user_id: Mapped[str] = mapped_column(
        "referredUserId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    reward_type: Mapped[str] = mapped_column("rewardType", String, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_claimed: Mapped[bool] = mapped_column(
        "isClaimed", Boolean, default=False, server_default="false", index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        "claimedAt", DateTime(timezone=True), nullable=True
    )
    claim_date: Mapped[datetime | None] = mapped_column(
        "claimDate", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ReferralReward_referrerId_referredUserId_rewardType_key",
            "referrerId",
            "referredUserId",
            "rewardType",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# ReferralRewardClaim
# ---------------------------------------------------------------------------


class ReferralRewardClaim(Base):
    __tablename__ = "ReferralRewardClaim"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    reward_id: Mapped[str] = mapped_column("rewardId", String, index=True)
    tokens_claimed: Mapped[int] = mapped_column("tokensClaimed", Integer, nullable=False)
    claim_date: Mapped[datetime] = mapped_column(
        "claimDate", DateTime(timezone=True), nullable=False
    )
    daily_limit_increase: Mapped[int] = mapped_column(
        "dailyLimitIncrease", Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (Index("ReferralRewardClaim_claimDate_idx", "claimDate"),)


# ---------------------------------------------------------------------------
# AdRewardClaim
# ---------------------------------------------------------------------------


class AdRewardClaim(Base):
    __tablename__ = "AdRewardClaim"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    ad_type: Mapped[str] = mapped_column("adType", String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    ad_unit_id: Mapped[str | None] = mapped_column("adUnitId", String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (Index("AdRewardClaim_userId_createdAt_idx", "userId", "createdAt"),)


# `CreditPack` and `CreditPurchaseTransaction` are dropped by migration 072 (Decision H). Credit packs
# are the withdrawn product, nobody ever bought one, and `PlusPurchase` records every pass and
# subscription purchase instead. `get_purchase_history` reads that table now.


# ---------------------------------------------------------------------------
# ResourceBankItem
# ---------------------------------------------------------------------------


class ResourceBankItem(Base, TimestampMixin):
    __tablename__ = "ResourceBankItem"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    uploader_id: Mapped[str] = mapped_column(
        "uploaderId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, default="OTHER", server_default="OTHER")
    university_name: Mapped[str] = mapped_column(
        "universityName", String, nullable=False, index=True
    )
    course_name: Mapped[str | None] = mapped_column("courseName", String, nullable=True)
    course_code: Mapped[str | None] = mapped_column("courseCode", String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String, default="PENDING_REVIEW", server_default="PENDING_REVIEW", index=True
    )
    moderation_notes: Mapped[str | None] = mapped_column("moderationNotes", String, nullable=True)
    download_count: Mapped[int] = mapped_column(
        "downloadCount", Integer, default=0, server_default="0"
    )
    view_count: Mapped[int] = mapped_column("viewCount", Integer, default=0, server_default="0")
    report_count: Mapped[int] = mapped_column("reportCount", Integer, default=0, server_default="0")

    __table_args__ = (
        Index(
            "ResourceBankItem_universityName_courseCode_idx",
            "universityName",
            "courseCode",
        ),
        Index("ResourceBankItem_universityName_type_idx", "universityName", "type"),
    )


# ---------------------------------------------------------------------------
# ResourceBankFile
# ---------------------------------------------------------------------------


class ResourceBankFile(Base):
    __tablename__ = "ResourceBankFile"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    resource_bank_item_id: Mapped[str] = mapped_column(
        "resourceBankItemId",
        String,
        ForeignKey("ResourceBankItem.id", ondelete="CASCADE"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column("mimeType", String, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column("extractedText", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


# ---------------------------------------------------------------------------
# ResourceBankReport
# ---------------------------------------------------------------------------


class ResourceBankReport(Base, TimestampMixin):
    __tablename__ = "ResourceBankReport"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25]
    )
    resource_bank_item_id: Mapped[str] = mapped_column(
        "resourceBankItemId",
        String,
        ForeignKey("ResourceBankItem.id", ondelete="CASCADE"),
        index=True,
    )
    reporter_id: Mapped[str] = mapped_column(
        "reporterId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default="REPORT_PENDING", server_default="REPORT_PENDING", index=True
    )

    __table_args__ = (
        Index(
            "ResourceBankReport_resourceBankItemId_reporterId_key",
            "resourceBankItemId",
            "reporterId",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# ResourceUploadReward
# ---------------------------------------------------------------------------


class ResourceUploadReward(Base, TimestampMixin):
    __tablename__ = "ResourceUploadReward"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    uploader_id: Mapped[str] = mapped_column(
        "uploaderId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    resource_bank_item_id: Mapped[str] = mapped_column(
        "resourceBankItemId",
        String,
        ForeignKey("ResourceBankItem.id", ondelete="CASCADE"),
        index=True,
    )
    tokens: Mapped[int] = mapped_column(Integer, default=1500, server_default="1500")
    is_claimed: Mapped[bool] = mapped_column(
        "isClaimed", Boolean, default=False, server_default="false", index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        "claimedAt", DateTime(timezone=True), nullable=True
    )
    claim_date: Mapped[datetime | None] = mapped_column(
        "claimDate", DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ResourceUploadReward_uploaderId_resourceBankItemId_key",
            "uploaderId",
            "resourceBankItemId",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# UsageEvent
# ---------------------------------------------------------------------------


class UsageEvent(Base):
    """One row per metered operation. What `record_units` spends, itemised.

    **The window counters say how much; this says on what.** §6.5 estimates a unit cost for each of 27
    operations and Decision P draws its model-quality threshold at 500 of them, but nothing in the
    database could check either figure: `record_units` advances two aggregates and logs the label, and
    `LlmCostRecord` has no operation column and is written on the chat path alone. So the paywall's
    threshold was enforced against numbers no query could contradict.

    An addition to Decision L rather than an unfinished half of it. L asked for cost to be measured
    rather than tabulated, and it is — `units_for_tokens` prices every generation from real tokens.
    Per-operation persistence is a new requirement that follows from Decision P needing a checkable
    threshold.

    `units` is stored without the token counts it came from. `units_for_tokens` has already applied the
    rate, and keeping both invites the two disagreeing — which is the failure this whole denomination
    exists to avoid. `model` is kept because under Decision P the same operation costs different units
    on different tiers, so which model ran is part of the answer rather than a restatement of it.

    No `userTier` column: the tier is derivable from the model, and a denormalised tier would be a
    second opinion about entitlement, which Decision B exists to prevent. No foreign key to `User`
    either — a deleted learner's spend still happened, and a cascade would quietly rewrite history to
    make the cost model look better than it was.
    """

    __tablename__ = "UsageEvent"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    user_id: Mapped[str] = mapped_column("userId", String, nullable=False)
    #: The `operation` label the call site passed to `llm_resilient`. Deliberately a free string
    #: rather than an enum: a newly labelled call site must not need a migration to start recording.
    operation: Mapped[str] = mapped_column(String, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Nullable because a provider reply can carry usage without a model name, and a null is more
    #: honest than a guess about which model was charged for.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Decision M rule 1's category tag, per operation as well as in the month aggregate, so the
    #: proactive share can be attributed to the tasks that spent it.
    proactive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        # The aggregation this table exists for: units by operation over a period.
        Index("UsageEvent_operation_createdAt_idx", "operation", "createdAt"),
        # Per-learner, for the distribution questions §6.7 leaves open.
        Index("UsageEvent_userId_createdAt_idx", "userId", "createdAt"),
    )


# ---------------------------------------------------------------------------
# PlusPurchase / PlusPass
# ---------------------------------------------------------------------------


class PlusPurchase(Base):
    """What was bought, on which rail, with the provider's own reference as the idempotency key.

    Decision G: **verify, persist, then grant, in that order.** StoreKit does not return finished
    consumables from `Transaction.currentEntitlements`, so a reinstalled app cannot recover a
    purchased-but-unactivated pass from the device. If the server did not persist it at verification
    time it is gone and the learner is owed a refund. "Restore" for a pass therefore means reading
    inventory from our API, not a StoreKit operation.

    `provider_reference` is unique, and that single constraint is the whole idempotency story: a webhook
    replay, a client retry and a `restore()` re-presenting the same token all collapse onto one row. A
    token already bound to a different learner is `409 PURCHASE_ALREADY_CLAIMED` — the standard
    cross-account IAP abuse vector, defended by the constraint rather than by a check that can be
    forgotten.

    Replaces `CreditPurchaseTransaction` (Decision H) rather than extending it, because that table is
    `NOT NULL` on `creditPackId` and `creditsGranted`, both meaningless for a pass or a subscription.
    """

    __tablename__ = "PlusPurchase"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column("productId", String, nullable=False)
    #: `pass` | `subscription`. A `plus_voice_30` purchase is `pass`-kind with no `PlusPass` behind it —
    #: the one purchase in the plan that grants no entitlement (Decision R).
    product_kind: Mapped[str] = mapped_column("productKind", String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_reference: Mapped[str] = mapped_column(
        "providerReference", String, unique=True, nullable=False
    )
    #: As charged, in the learner's own currency. Not converted — §6.8 sets NGN prices independently of
    #: FX, so a stored USD equivalent would be a number nobody agreed to pay.
    amount_minor: Mapped[int] = mapped_column("amountMinor", Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        "refundedAt", DateTime(timezone=True), nullable=True
    )
    #: The verification response, for disputes. A refund argued six months later is argued against what
    #: the provider actually said rather than against our summary of it.
    raw_payload: Mapped[dict | None] = mapped_column("rawPayload", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (Index("PlusPurchase_userId_createdAt_idx", "userId", "createdAt"),)


class PlusPass(Base):
    """A pass. Inventory when bought, running when activated, over when its clock or its allowance ends.

    Decision A: **the clock starts on activation, not on purchase.** A $0.99 five-hour pass bought on
    Tuesday and spent on Saturday's revision session is worth buying; one whose clock starts at the
    checkout screen is not, and would generate refunds at a rate that threatens store standing.

    Decision E: **two ways to end, and both are real.** `expires_at <= now` is the wall clock.
    `units_used >= units_allowance` is the allowance, and it is what stops a pass being a product that
    loses money faster the more it is used — five hours of continuous live voice is about $6.00 of
    inference against $0.75 of net revenue. The promise is capabilities without limit and usage with a
    stated ceiling, and the ceiling is on the purchase screen.

    `duration_minutes` and `units_allowance` are **snapshotted** rather than read from the product at
    use time. Re-pricing or re-timing a product must not change a pass already sold, and it also lets a
    market carry its own allowance: §6.8 gives an NGN learner 1 800 units on the 5-hour pass where §6.3
    gives 2 000, which a snapshot makes a property of the purchase rather than a branch in every reader.
    """

    __tablename__ = "PlusPass"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(__import__("uuid").uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        "userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True
    )
    #: `plus_pass_5h` | `plus_pass_7d` | `plus_pass_term`. **Never `plus_voice_30`**: a voice pack is a
    #: balance on `User`, not a pass, and a row here for one would grant entitlement it did not sell.
    product_id: Mapped[str] = mapped_column("productId", String, nullable=False)
    duration_minutes: Mapped[int] = mapped_column("durationMinutes", Integer, nullable=False)
    units_allowance: Mapped[int] = mapped_column("unitsAllowance", Integer, nullable=False)
    #: A third counter, beside the window and the month on `User`, and Decision E is why it exists:
    #: those two reset and a pass total does not.
    units_used: Mapped[int] = mapped_column(
        "unitsUsed", Integer, default=0, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String, default="inventory", server_default="inventory", nullable=False
    )
    #: Null when `source='points'` — nothing was purchased (Decision O).
    purchase_id: Mapped[str | None] = mapped_column(
        "purchaseId",
        String,
        ForeignKey("PlusPurchase.id", ondelete="SET NULL"),
        nullable=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        "activatedAt", DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        "expiresAt", DateTime(timezone=True), nullable=True
    )
    #: null | `expired` | `exhausted` | `refund`. Kept apart from `status` because "your five hours are
    #: up" and "you've used this pass's allowance" are different facts and need different copy.
    ended_reason: Mapped[str | None] = mapped_column("endedReason", String, nullable=True)
    source: Mapped[str] = mapped_column(
        String, default="purchase", server_default="purchase", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt",
        DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        onupdate=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("PlusPass_userId_status_idx", "userId", "status"),
        Index("PlusPass_status_expiresAt_idx", "status", "expiresAt"),
        # Decision A's one-active invariant, owned by the database so that two concurrent activations
        # produce one winner and one `409` rather than a race. `pass_service` catches the
        # `IntegrityError`; it does not try to prevent it.
        Index(
            "PlusPass_oneActivePerUser_idx",
            "userId",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
