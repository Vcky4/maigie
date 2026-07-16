"""
Learning Spaces domain — SQLAlchemy models.

Space, SpaceMember, SpaceChatGroup, SpaceChatGroupMember,
SpaceInvite, SpaceMemberStat, SpaceSession, SpaceJoinRequest,
SpaceSubscription, SpaceSeatAddon, AiUsageRecord.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.shared.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


class Space(Base, TimestampMixin):
    __tablename__ = "Space"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column("avatarUrl", String, nullable=True)
    created_by_id: Mapped[str] = mapped_column("createdById", String, index=True)
    max_members: Mapped[int] = mapped_column("maxMembers", Integer, default=5, server_default="5")
    max_groups: Mapped[int] = mapped_column("maxGroups", Integer, default=5, server_default="5")

    # Credits
    credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    credits_limit: Mapped[Optional[int]] = mapped_column("creditsLimit", Integer, nullable=True)

    # Visibility & branding
    visibility: Mapped[str] = mapped_column(String, default="PRIVATE", server_default="PRIVATE")
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column("bannerUrl", String, nullable=True)
    theme_json: Mapped[Optional[dict]] = mapped_column("themeJson", JSON, nullable=True)

    # Plan & seat
    space_plan_active: Mapped[bool] = mapped_column("spacePlanActive", Boolean, default=False, server_default="false")
    space_plan_current_period_end: Mapped[Optional[datetime]] = mapped_column("spacePlanCurrentPeriodEnd", DateTime(timezone=True), nullable=True)
    seat_pool_size: Mapped[int] = mapped_column("seatPoolSize", Integer, default=0, server_default="0")

    # Moderation
    hidden_by_moderation: Mapped[bool] = mapped_column("hiddenByModeration", Boolean, default=False, server_default="false")
    allow_member_export: Mapped[bool] = mapped_column("allowMemberExport", Boolean, default=False, server_default="false")
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    join_policy: Mapped[str] = mapped_column("joinPolicy", String, default="AUTO_JOIN", server_default="AUTO_JOIN")
    migration_marker: Mapped[Optional[str]] = mapped_column("migrationMarker", String, nullable=True)

    # Relationships
    members: Mapped[list["SpaceMember"]] = relationship("SpaceMember", back_populates="space", lazy="selectin")
    chat_groups: Mapped[list["SpaceChatGroup"]] = relationship("SpaceChatGroup", back_populates="space", lazy="noload")

    __table_args__ = (
        Index("Space_visibility_idx", "visibility"),
        Index("Space_featured_idx", "featured"),
        Index("Space_spacePlanActive_idx", "spacePlanActive"),
    )

    def __repr__(self) -> str:
        return f"<Space id={self.id} name={self.name}>"


# ---------------------------------------------------------------------------
# SpaceMember
# ---------------------------------------------------------------------------


class SpaceMember(Base):
    __tablename__ = "SpaceMember"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String, default="MEMBER", server_default="MEMBER")
    seat_tier: Mapped[str] = mapped_column("seatTier", String, default="FREE_SEAT", server_default="FREE_SEAT")
    joined_at: Mapped[datetime] = mapped_column(
        "joinedAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Relationships
    space: Mapped["Space"] = relationship("Space", back_populates="members")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")

    __table_args__ = (
        Index("SpaceMember_spaceId_userId_key", "spaceId", "userId", unique=True),
        Index("SpaceMember_spaceId_seatTier_idx", "spaceId", "seatTier"),
    )


# ---------------------------------------------------------------------------
# SpaceChatGroup
# ---------------------------------------------------------------------------


class SpaceChatGroup(Base, TimestampMixin):
    __tablename__ = "SpaceChatGroup"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    chat_session_id: Mapped[Optional[str]] = mapped_column("chatSessionId", String, unique=True, nullable=True)
    visibility: Mapped[str] = mapped_column(String, default="PUBLIC", server_default="PUBLIC")
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Relationships
    space: Mapped["Space"] = relationship("Space", back_populates="chat_groups")


# ---------------------------------------------------------------------------
# SpaceChatGroupMember
# ---------------------------------------------------------------------------


class SpaceChatGroupMember(Base):
    __tablename__ = "SpaceChatGroupMember"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    chat_group_id: Mapped[str] = mapped_column("chatGroupId", String, ForeignKey("SpaceChatGroup.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(
        "joinedAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("SpaceChatGroupMember_chatGroupId_userId_key", "chatGroupId", "userId", unique=True),
    )


# ---------------------------------------------------------------------------
# SpaceInvite
# ---------------------------------------------------------------------------


class SpaceInvite(Base):
    __tablename__ = "SpaceInvite"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), index=True)
    inviter_id: Mapped[str] = mapped_column("inviterId", String, nullable=False)
    invitee_email: Mapped[str] = mapped_column("inviteeEmail", String, nullable=False, index=True)
    invitee_id: Mapped[Optional[str]] = mapped_column("inviteeId", String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="PENDING", server_default="PENDING")
    role: Mapped[str] = mapped_column(String, default="MEMBER", server_default="MEMBER")
    seat_tier: Mapped[str] = mapped_column("seatTier", String, default="FREE_SEAT", server_default="FREE_SEAT")
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("SpaceInvite_spaceId_inviteeEmail_key", "spaceId", "inviteeEmail", unique=True),
    )


# ---------------------------------------------------------------------------
# SpaceMemberStat
# ---------------------------------------------------------------------------


class SpaceMemberStat(Base, TimestampMixin):
    __tablename__ = "SpaceMemberStat"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)

    contribution_points: Mapped[int] = mapped_column("contributionPoints", Integer, default=0, server_default="0")
    courses_completed: Mapped[int] = mapped_column("coursesCompleted", Integer, default=0, server_default="0")
    notes_added: Mapped[int] = mapped_column("notesAdded", Integer, default=0, server_default="0")
    resources_shared: Mapped[int] = mapped_column("resourcesShared", Integer, default=0, server_default="0")
    quiz_average: Mapped[float] = mapped_column("quizAverage", Float, default=0.0, server_default="0")

    __table_args__ = (
        Index("SpaceMemberStat_spaceId_userId_key", "spaceId", "userId", unique=True),
    )


# ---------------------------------------------------------------------------
# SpaceSession
# ---------------------------------------------------------------------------


class SpaceSession(Base, TimestampMixin):
    __tablename__ = "SpaceSession"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column("scheduledAt", DateTime(timezone=True), nullable=False, index=True)
    duration: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    status: Mapped[str] = mapped_column(String, default="SCHEDULED", server_default="SCHEDULED", index=True)

    # Links
    chat_group_id: Mapped[Optional[str]] = mapped_column("chatGroupId", String, nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column("topicId", String, nullable=True, index=True)
    goal_id: Mapped[Optional[str]] = mapped_column("goalId", String, nullable=True, index=True)
    created_by_id: Mapped[str] = mapped_column("createdById", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)


# ---------------------------------------------------------------------------
# SpaceJoinRequest
# ---------------------------------------------------------------------------


class SpaceJoinRequest(Base):
    __tablename__ = "SpaceJoinRequest"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String, default="PENDING", server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column("decidedAt", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("SpaceJoinRequest_spaceId_userId_key", "spaceId", "userId", unique=True),
        Index("SpaceJoinRequest_spaceId_status_idx", "spaceId", "status"),
    )


# ---------------------------------------------------------------------------
# SpaceSubscription
# ---------------------------------------------------------------------------


class SpaceSubscription(Base, TimestampMixin):
    __tablename__ = "SpaceSubscription"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_subscription_id: Mapped[str] = mapped_column("providerSubscriptionId", String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    current_period_end: Mapped[datetime] = mapped_column("currentPeriodEnd", DateTime(timezone=True), nullable=False, index=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column("trialEndsAt", DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column("cancelAtPeriodEnd", Boolean, default=False, server_default="false")
    owner_user_id: Mapped[str] = mapped_column("ownerUserId", String, ForeignKey("User.id", ondelete="CASCADE"))


# ---------------------------------------------------------------------------
# SpaceSeatAddon
# ---------------------------------------------------------------------------


class SpaceSeatAddon(Base):
    __tablename__ = "SpaceSeatAddon"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    space_id: Mapped[str] = mapped_column("spaceId", String, ForeignKey("Space.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_subscription_id: Mapped[str] = mapped_column("providerSubscriptionId", String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_period_end: Mapped[datetime] = mapped_column("currentPeriodEnd", DateTime(timezone=True), nullable=False)
    assigned_to_user_id: Mapped[Optional[str]] = mapped_column("assignedToUserId", String, nullable=True, index=True)
    purchased_by_user_id: Mapped[str] = mapped_column("purchasedByUserId", String, ForeignKey("User.id", ondelete="CASCADE"), index=True)
    purchased_at: Mapped[datetime] = mapped_column(
        "purchasedAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    canceled_at: Mapped[Optional[datetime]] = mapped_column("canceledAt", DateTime(timezone=True), nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column("assignedAt", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("SpaceSeatAddon_spaceId_status_idx", "spaceId", "status"),
        Index("SpaceSeatAddon_spaceId_assignedAt_idx", "spaceId", "assignedAt"),
    )


# ---------------------------------------------------------------------------
# AiUsageRecord
# ---------------------------------------------------------------------------


class AiUsageRecord(Base):
    __tablename__ = "AiUsageRecord"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:25])
    user_id: Mapped[str] = mapped_column("userId", String, ForeignKey("User.id", ondelete="CASCADE"))
    usage_scope: Mapped[str] = mapped_column("usageScope", String, nullable=False)
    space_id: Mapped[Optional[str]] = mapped_column("spaceId", String, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    feature: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int] = mapped_column("inputTokens", Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column("outputTokens", Integer, default=0, server_default="0")
    request_count: Mapped[int] = mapped_column("requestCount", Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True),
        default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    __table_args__ = (
        Index("AiUsageRecord_userId_usageScope_idx", "userId", "usageScope"),
        Index("AiUsageRecord_spaceId_userId_idx", "spaceId", "userId"),
        Index("AiUsageRecord_userId_createdAt_idx", "userId", "createdAt"),
        Index("AiUsageRecord_spaceId_createdAt_idx", "spaceId", "createdAt"),
    )


# Import User for relationship resolution
from src.domains.identity.db_models import User  # noqa: E402, F401
