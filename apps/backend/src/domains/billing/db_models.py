"""
Billing domain — SQLAlchemy models.

ReferralReward, ReferralRewardClaim, AdRewardClaim, ResourceBankItem,
ResourceBankFile, ResourceBankReport, ResourceUploadReward,
ResourceUploadRewardClaim, CreditPack, CreditPurchaseTransaction.

Maps to existing PostgreSQL tables created by Prisma.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# ReferralReward
# ---------------------------------------------------------------------------


class ReferralReward(Base, TimestampMixin):
    __tablename__ = "ReferralReward"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    referrer_id: Mapped[str] = mapped_column("referrerId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    referred_user_id: Mapped[str] = mapped_column("referredUserId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    reward_type: Mapped[str] = mapped_column("rewardType", String, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_claimed: Mapped[bool] = mapped_column("isClaimed", Boolean, default=False, server_default="false", index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column("claimedAt", DateTime(timezone=True), nullable=True)
    claim_date: Mapped[Optional[datetime]] = mapped_column("claimDate", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ReferralReward_referrerId_referredUserId_rewardType_key", "referrerId", "referredUserId", "rewardType", unique=True),
    )


# ---------------------------------------------------------------------------
# ReferralRewardClaim
# ---------------------------------------------------------------------------


class ReferralRewardClaim(Base):
    __tablename__ = "ReferralRewardClaim"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    reward_id: Mapped[str] = mapped_column("rewardId", String, index=True)
    tokens_claimed: Mapped[int] = mapped_column("tokensClaimed", Integer, nullable=False)
    claim_date: Mapped[datetime] = mapped_column("claimDate", DateTime(timezone=True), nullable=False)
    daily_limit_increase: Mapped[int] = mapped_column("dailyLimitIncrease", Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("ReferralRewardClaim_claimDate_idx", "claimDate"),
    )


# ---------------------------------------------------------------------------
# AdRewardClaim
# ---------------------------------------------------------------------------


class AdRewardClaim(Base):
    __tablename__ = "AdRewardClaim"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    ad_type: Mapped[str] = mapped_column("adType", String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    ad_unit_id: Mapped[Optional[str]] = mapped_column("adUnitId", String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("AdRewardClaim_userId_createdAt_idx", "userId", "createdAt"),
    )


# ---------------------------------------------------------------------------
# CreditPack
# ---------------------------------------------------------------------------


class CreditPack(Base, TimestampMixin):
    __tablename__ = "CreditPack"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    name: Mapped[str] = mapped_column(String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_credits: Mapped[int] = mapped_column("bonusCredits", Integer, default=0, server_default="0")
    price_usd_cents: Mapped[int] = mapped_column("priceUsdCents", Integer, nullable=False)
    price_ngn_kobo: Mapped[int] = mapped_column("priceNgnKobo", Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column("sortOrder", Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, server_default="true")

    # Relationships
    transactions: Mapped[list["CreditPurchaseTransaction"]] = relationship("CreditPurchaseTransaction", back_populates="credit_pack", lazy="noload")

    __table_args__ = (
        Index("CreditPack_isActive_sortOrder_idx", "isActive", "sortOrder"),
    )


# ---------------------------------------------------------------------------
# CreditPurchaseTransaction
# ---------------------------------------------------------------------------


class CreditPurchaseTransaction(Base):
    __tablename__ = "CreditPurchaseTransaction"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    credit_pack_id: Mapped[str] = mapped_column("creditPackId", String, ForeignKey("CreditPack.id"))
    credits_granted: Mapped[int] = mapped_column("creditsGranted", Integer, nullable=False)

    amount_paid: Mapped[int] = mapped_column("amountPaid", Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    payment_provider: Mapped[str] = mapped_column("paymentProvider", String, nullable=False)
    provider_reference: Mapped[str] = mapped_column("providerReference", String, unique=True, nullable=False)

    session_id: Mapped[Optional[str]] = mapped_column("sessionId", String, nullable=True)
    session_expires_at: Mapped[Optional[datetime]] = mapped_column("sessionExpiresAt", DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    completed_at: Mapped[Optional[datetime]] = mapped_column("completedAt", DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        onupdate=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    credit_pack: Mapped["CreditPack"] = relationship("CreditPack", back_populates="transactions")

    __table_args__ = (
        Index("CreditPurchaseTransaction_userId_idx", "userId"),
        Index("CreditPurchaseTransaction_providerReference_idx", "providerReference"),
    )


# ---------------------------------------------------------------------------
# ResourceBankItem
# ---------------------------------------------------------------------------


class ResourceBankItem(Base, TimestampMixin):
    __tablename__ = "ResourceBankItem"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    uploader_id: Mapped[str] = mapped_column("uploaderId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, default="OTHER", server_default="OTHER")
    university_name: Mapped[str] = mapped_column("universityName", String, nullable=False, index=True)
    course_name: Mapped[Optional[str]] = mapped_column("courseName", String, nullable=True)
    course_code: Mapped[Optional[str]] = mapped_column("courseCode", String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="PENDING_REVIEW", server_default="PENDING_REVIEW", index=True)
    moderation_notes: Mapped[Optional[str]] = mapped_column("moderationNotes", String, nullable=True)
    download_count: Mapped[int] = mapped_column("downloadCount", Integer, default=0, server_default="0")
    view_count: Mapped[int] = mapped_column("viewCount", Integer, default=0, server_default="0")
    report_count: Mapped[int] = mapped_column("reportCount", Integer, default=0, server_default="0")

    __table_args__ = (
        Index("ResourceBankItem_universityName_courseCode_idx", "universityName", "courseCode"),
        Index("ResourceBankItem_universityName_type_idx", "universityName", "type"),
    )


# ---------------------------------------------------------------------------
# ResourceBankFile
# ---------------------------------------------------------------------------


class ResourceBankFile(Base):
    __tablename__ = "ResourceBankFile"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    resource_bank_item_id: Mapped[str] = mapped_column("resourceBankItemId", String, ForeignKey("ResourceBankItem.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column("mimeType", String, nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column("extractedText", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


# ---------------------------------------------------------------------------
# ResourceBankReport
# ---------------------------------------------------------------------------


class ResourceBankReport(Base, TimestampMixin):
    __tablename__ = "ResourceBankReport"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    resource_bank_item_id: Mapped[str] = mapped_column("resourceBankItemId", String, ForeignKey("ResourceBankItem.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[str] = mapped_column("reporterId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="REPORT_PENDING", server_default="REPORT_PENDING", index=True)

    __table_args__ = (
        Index("ResourceBankReport_resourceBankItemId_reporterId_key", "resourceBankItemId", "reporterId", unique=True),
    )


# ---------------------------------------------------------------------------
# ResourceUploadReward
# ---------------------------------------------------------------------------


class ResourceUploadReward(Base, TimestampMixin):
    __tablename__ = "ResourceUploadReward"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    uploader_id: Mapped[str] = mapped_column("uploaderId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    resource_bank_item_id: Mapped[str] = mapped_column("resourceBankItemId", String, ForeignKey("ResourceBankItem.id", ondelete="CASCADE"), index=True)
    tokens: Mapped[int] = mapped_column(Integer, default=1500, server_default="1500")
    is_claimed: Mapped[bool] = mapped_column("isClaimed", Boolean, default=False, server_default="false", index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column("claimedAt", DateTime(timezone=True), nullable=True)
    claim_date: Mapped[Optional[datetime]] = mapped_column("claimDate", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ResourceUploadReward_uploaderId_resourceBankItemId_key", "uploaderId", "resourceBankItemId", unique=True),
    )
