"""Add personal learning models.

Creates all new tables for the Personal Learning Experience feature:
  FlashcardDeck
  Flashcard
  SavedResource
  LearningProfile
  Notification
  PrepTopic
  PrepMaterial
  QuizSession
  QuizQuestion
  QuizAnswer
  StudyPlan
  StudyPlanItem
  Reflection
  DiscoveryRecommendation
  ActivityFeedEntry

Revision ID: 003_add_personal_learning_models
Revises: 002_rename_circle_to_space
Create Date: 2026-07-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "003_add_personal_learning_models"
down_revision = "002_rename_circle_to_space"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # FlashcardDeck
    # -----------------------------------------------------------------------
    op.create_table(
        "FlashcardDeck",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("courseId", sa.String(), nullable=True, index=True),
        sa.Column("topicId", sa.String(), nullable=True, index=True),
        sa.Column(
            "prepId", sa.String(), sa.ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # -----------------------------------------------------------------------
    # Flashcard
    # -----------------------------------------------------------------------
    op.create_table(
        "Flashcard",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "deckId",
            sa.String(),
            sa.ForeignKey("FlashcardDeck.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("intervalDays", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repetitionCount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("easeFactor", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("nextReviewAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lastReviewedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lastQuality", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("lapseCount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sourceType", sa.String(), nullable=True),
        sa.Column("sourceId", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("Flashcard_userId_nextReviewAt_idx", "Flashcard", ["userId", "nextReviewAt"])
    op.create_index("Flashcard_deckId_idx", "Flashcard", ["deckId"])

    # -----------------------------------------------------------------------
    # SavedResource
    # -----------------------------------------------------------------------
    op.create_table(
        "SavedResource",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("sourceType", sa.String(), nullable=False),
        sa.Column("sourceId", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("lastAccessedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "SavedResource_userId_sourceType_idx", "SavedResource", ["userId", "sourceType"]
    )

    # -----------------------------------------------------------------------
    # LearningProfile
    # -----------------------------------------------------------------------
    op.create_table(
        "LearningProfile",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("purpose", sa.String(), nullable=True),
        sa.Column("subjects", sa.JSON(), nullable=True),
        sa.Column("goalsText", sa.Text(), nullable=True),
        sa.Column("preferredExplanationStyle", sa.String(), nullable=True),
        sa.Column("proficiencyMap", sa.JSON(), nullable=True),
        sa.Column("onboardingCompletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maturityDays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quietHoursStart", sa.String(), nullable=True),
        sa.Column("quietHoursEnd", sa.String(), nullable=True),
        sa.Column("maxDailyNotifications", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("preferredStudyTimes", sa.JSON(), nullable=True),
        sa.Column("avgSessionMinutes", sa.Float(), nullable=True),
        sa.Column("consistencyScore", sa.Float(), nullable=True),
        sa.Column("bestDayOfWeek", sa.String(), nullable=True),
        sa.Column("dropoutRisk", sa.Float(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("LearningProfile_userId_idx", "LearningProfile", ["userId"])

    # -----------------------------------------------------------------------
    # Notification
    # -----------------------------------------------------------------------
    op.create_table(
        "Notification",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("actionData", sa.JSON(), nullable=True),
        sa.Column("scheduledAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deliveredAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("Notification_userId_status_idx", "Notification", ["userId", "status"])
    op.create_index("Notification_scheduledAt_idx", "Notification", ["scheduledAt"])

    # -----------------------------------------------------------------------
    # PrepTopic
    # -----------------------------------------------------------------------
    op.create_table(
        "PrepTopic",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("estimatedMinutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("orderIndex", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("masteryScore", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(), nullable=False, server_default="NOT_STARTED"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("PrepTopic_prepId_order_idx", "PrepTopic", ["prepId", "orderIndex"])

    # -----------------------------------------------------------------------
    # PrepMaterial
    # -----------------------------------------------------------------------
    op.create_table(
        "PrepMaterial",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("fileType", sa.String(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("extractedText", sa.Text(), nullable=True),
        sa.Column("category", sa.String(), nullable=False, server_default="OTHER"),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # -----------------------------------------------------------------------
    # QuizSession
    # -----------------------------------------------------------------------
    op.create_table(
        "QuizSession",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("topicId", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("totalQuestions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correctCount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scorePercentage", sa.Float(), nullable=True),
        sa.Column("durationSeconds", sa.Integer(), nullable=True),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("QuizSession_userId_prepId_idx", "QuizSession", ["userId", "prepId"])

    # -----------------------------------------------------------------------
    # QuizQuestion
    # -----------------------------------------------------------------------
    op.create_table(
        "QuizQuestion",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "quizSessionId",
            sa.String(),
            sa.ForeignKey("QuizSession.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "prepTopicId",
            sa.String(),
            sa.ForeignKey("PrepTopic.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("questionText", sa.Text(), nullable=False),
        sa.Column("questionType", sa.String(), nullable=False, server_default="MULTIPLE_CHOICE"),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correctAnswer", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("orderIndex", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # -----------------------------------------------------------------------
    # QuizAnswer
    # -----------------------------------------------------------------------
    op.create_table(
        "QuizAnswer",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "quizSessionId",
            sa.String(),
            sa.ForeignKey("QuizSession.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "questionId",
            sa.String(),
            sa.ForeignKey("QuizQuestion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("userAnswer", sa.String(), nullable=False),
        sa.Column("isCorrect", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timeTakenSeconds", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # -----------------------------------------------------------------------
    # StudyPlan
    # -----------------------------------------------------------------------
    op.create_table(
        "StudyPlan",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("goalDescription", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "prepId", sa.String(), sa.ForeignKey("ExamPrep.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column("totalItems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completedItems", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("StudyPlan_userId_status_idx", "StudyPlan", ["userId", "status"])

    # -----------------------------------------------------------------------
    # StudyPlanItem
    # -----------------------------------------------------------------------
    op.create_table(
        "StudyPlanItem",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "planId",
            sa.String(),
            sa.ForeignKey("StudyPlan.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scheduledDate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimatedMinutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("itemType", sa.String(), nullable=False, server_default="STUDY"),
        sa.Column("topicId", sa.String(), nullable=True),
        sa.Column("prepTopicId", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "StudyPlanItem_planId_scheduledDate_idx", "StudyPlanItem", ["planId", "scheduledDate"]
    )

    # -----------------------------------------------------------------------
    # Reflection
    # -----------------------------------------------------------------------
    op.create_table(
        "Reflection",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("periodStart", sa.DateTime(timezone=True), nullable=False),
        sa.Column("periodEnd", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("activitiesLayer", sa.JSON(), nullable=True),
        sa.Column("progressLayer", sa.JSON(), nullable=True),
        sa.Column("achievementsLayer", sa.JSON(), nullable=True),
        sa.Column("recommendations", sa.JSON(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("Reflection_userId_type_idx", "Reflection", ["userId", "type"])
    op.create_index("Reflection_periodEnd_idx", "Reflection", ["periodEnd"])

    # -----------------------------------------------------------------------
    # DiscoveryRecommendation
    # -----------------------------------------------------------------------
    op.create_table(
        "DiscoveryRecommendation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("itemType", sa.String(), nullable=False),
        sa.Column("itemId", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("relevanceScore", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column("dismissedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("followedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "DiscoveryRecommendation_userId_status_idx", "DiscoveryRecommendation", ["userId", "status"]
    )

    # -----------------------------------------------------------------------
    # ActivityFeedEntry (no TimestampMixin — only occurredAt)
    # -----------------------------------------------------------------------
    op.create_table(
        "ActivityFeedEntry",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("activityType", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("occurredAt", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ActivityFeedEntry_userId_occurredAt_idx", "ActivityFeedEntry", ["userId", "occurredAt"]
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("ActivityFeedEntry")
    op.drop_table("DiscoveryRecommendation")
    op.drop_table("Reflection")
    op.drop_table("StudyPlanItem")
    op.drop_table("StudyPlan")
    op.drop_table("QuizAnswer")
    op.drop_table("QuizQuestion")
    op.drop_table("QuizSession")
    op.drop_table("PrepMaterial")
    op.drop_table("PrepTopic")
    op.drop_table("Notification")
    op.drop_table("LearningProfile")
    op.drop_table("SavedResource")
    op.drop_table("Flashcard")
    op.drop_table("FlashcardDeck")
