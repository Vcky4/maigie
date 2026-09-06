"""Add commercial models for personal learning.

Creates new tables:
  ConversionTriggerLog
  LearningMilestone
  RetentionIntervention
  ValueSummaryRecord

Adds commercial fields to LearningProfile:
  Trial tracking, conversion tracking, educator transition, value tracking.

Revision ID: 005_add_commercial_models
Revises: 004_add_preferred_llm_provider
Create Date: 2026-07-22
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "005_add_commercial_models"
down_revision = "004_add_preferred_llm_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # LearningProfile — new commercial fields
    # -----------------------------------------------------------------------
    op.add_column(
        "LearningProfile",
        sa.Column("trialStartedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("trialEndsAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("lastTrialEndedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("lastTriggerShownAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("triggerDismissalCount", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("lastTriggerDismissedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("educatorReadinessMetAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("educatorSuggestionShownAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("spaceTrialStartedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("lastValueSummaryAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "LearningProfile",
        sa.Column("plusFeaturesUsedThisPeriod", sa.JSON(), nullable=True),
    )

    # -----------------------------------------------------------------------
    # ConversionTriggerLog
    # -----------------------------------------------------------------------
    op.create_table(
        "ConversionTriggerLog",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("triggerId", sa.String(), nullable=False),
        sa.Column("shownAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("convertedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capabilityHighlighted", sa.String(), nullable=False),
    )
    op.create_index(
        "ConversionTriggerLog_userId_shownAt_idx",
        "ConversionTriggerLog",
        ["userId", "shownAt"],
    )

    # -----------------------------------------------------------------------
    # LearningMilestone
    # -----------------------------------------------------------------------
    op.create_table(
        "LearningMilestone",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("milestoneId", sa.String(), nullable=False),
        sa.Column("achievedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sharedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shareCardUrl", sa.String(), nullable=True),
        sa.Column("referralLink", sa.String(), nullable=True),
    )
    op.create_index(
        "LearningMilestone_userId_milestoneId_idx",
        "LearningMilestone",
        ["userId", "milestoneId"],
        unique=True,
    )

    # -----------------------------------------------------------------------
    # RetentionIntervention
    # -----------------------------------------------------------------------
    op.create_table(
        "RetentionIntervention",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("churnRiskScore", sa.Float(), nullable=False),
        sa.Column("interventionType", sa.String(), nullable=False),
        sa.Column("deliveredAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("outcomeAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "RetentionIntervention_userId_deliveredAt_idx",
        "RetentionIntervention",
        ["userId", "deliveredAt"],
    )

    # -----------------------------------------------------------------------
    # ValueSummaryRecord
    # -----------------------------------------------------------------------
    op.create_table(
        "ValueSummaryRecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("periodStart", sa.DateTime(timezone=True), nullable=False),
        sa.Column("periodEnd", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summaryData", sa.JSON(), nullable=False),
        sa.Column("deliveredAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deliveryMethod", sa.String(), server_default="notification", nullable=False),
    )
    op.create_index(
        "ValueSummaryRecord_userId_periodEnd_idx",
        "ValueSummaryRecord",
        ["userId", "periodEnd"],
    )


def downgrade() -> None:
    op.drop_table("ValueSummaryRecord")
    op.drop_table("RetentionIntervention")
    op.drop_table("LearningMilestone")
    op.drop_table("ConversionTriggerLog")

    op.drop_column("LearningProfile", "plusFeaturesUsedThisPeriod")
    op.drop_column("LearningProfile", "lastValueSummaryAt")
    op.drop_column("LearningProfile", "spaceTrialStartedAt")
    op.drop_column("LearningProfile", "educatorSuggestionShownAt")
    op.drop_column("LearningProfile", "educatorReadinessMetAt")
    op.drop_column("LearningProfile", "lastTriggerDismissedAt")
    op.drop_column("LearningProfile", "triggerDismissalCount")
    op.drop_column("LearningProfile", "lastTriggerShownAt")
    op.drop_column("LearningProfile", "lastTrialEndedAt")
    op.drop_column("LearningProfile", "trialEndsAt")
    op.drop_column("LearningProfile", "trialStartedAt")
