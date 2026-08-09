"""Drop Prisma enum types and convert columns to VARCHAR.

Prisma created custom PostgreSQL enum types for columns like tier, role, status, etc.
SQLAlchemy models use plain String/VARCHAR so we need to convert these columns.

Revision ID: 001_drop_prisma_enums
Revises:
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "001_drop_prisma_enums"
down_revision = None
branch_labels = None
depends_on = None


# Mapping of (table, column) -> enum_type_name
# These are all the enum types Prisma created
ENUM_COLUMNS = [
    # User table
    ("User", "tier", "Tier"),
    ("User", "role", "UserRole"),
    # Goal
    ("Goal", "status", "GoalStatus"),
    # Resource
    ("Resource", "type", "ResourceType"),
    # ScheduleBehaviourLog
    ("ScheduleBehaviourLog", "behaviourType", "ScheduleBehaviourType"),
    # Achievement
    ("Achievement", "achievementType", "AchievementType"),
    # ChatMessage
    ("ChatMessage", "role", "ChatRole"),
    # AIActionLog
    ("AIActionLog", "status", "ActionStatus"),
    # UserInteractionMemory
    ("UserInteractionMemory", "interactionType", "InteractionType"),
    # CircleMember
    ("CircleMember", "role", "CircleRole"),
    ("CircleMember", "seatTier", "SeatTier"),
    # CircleInvite
    ("CircleInvite", "status", "CircleInviteStatus"),
    ("CircleInvite", "role", "CircleRole"),
    ("CircleInvite", "seatTier", "SeatTier"),
    # Circle
    ("Circle", "visibility", "CircleVisibility"),
    # CircleSession
    ("CircleSession", "status", "CircleSessionStatus"),
    # ExamPrep
    ("ExamPrep", "status", "ExamPrepStatus"),
    # ExamPrepMaterial
    ("ExamPrepMaterial", "category", "MaterialCategory"),
    # ExamQuestion
    ("ExamQuestion", "source", "QuestionSource"),
    ("ExamQuestion", "questionType", "QuestionType"),
    ("ExamQuestion", "difficulty", "QuestionDifficulty"),
    # ExamQuizSession
    ("ExamQuizSession", "mode", "QuizMode"),
    # ResourceBankItem
    ("ResourceBankItem", "type", "ResourceBankType"),
    ("ResourceBankItem", "status", "ResourceBankStatus"),
    # ResourceBankReport
    ("ResourceBankReport", "status", "ReportStatus"),
    # Report (moderation)
    ("Report", "targetType", "ReportTargetType"),
    ("Report", "status", "ModerationReportStatus"),
    # CircleOwnershipTransfer
    ("CircleOwnershipTransfer", "status", "OwnershipTransferStatus"),
]


def upgrade() -> None:
    """Convert ALL enum columns to VARCHAR by querying pg_catalog for enum types,
    then dropping them."""
    # Nuclear option: find ALL columns using enum types and convert them
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT
                    c.table_name,
                    c.column_name,
                    c.udt_name
                FROM information_schema.columns c
                JOIN pg_type t ON t.typname = c.udt_name
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE c.table_schema = 'public'
                GROUP BY c.table_name, c.column_name, c.udt_name
            ) LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN %I TYPE VARCHAR USING %I::text',
                    r.table_name, r.column_name, r.column_name
                );
            END LOOP;
        END $$;
    """
    )

    # Now drop ALL enum types
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT t.typname
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typnamespace = 'public'::regnamespace
                GROUP BY t.typname
            ) LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
            END LOOP;
        END $$;
    """
    )


def downgrade() -> None:
    """This migration is not reversible — enum values would need to be recreated."""
    pass
