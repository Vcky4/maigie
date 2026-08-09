"""Add onboarding state tracking fields to LearningProfile

Revision ID: 015
Revises: 014
Create Date: 2026-08-09

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "015_add_onboarding_state_fields"
down_revision = "014_drop_embedding_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to LearningProfile for onboarding state machine
    op.add_column("LearningProfile", sa.Column("onboardingState", sa.String(), nullable=True))
    op.add_column("LearningProfile", sa.Column("examName", sa.String(), nullable=True))
    op.add_column("LearningProfile", sa.Column("examDate", sa.Date(), nullable=True))
    op.add_column("LearningProfile", sa.Column("skillName", sa.String(), nullable=True))
    op.add_column("LearningProfile", sa.Column("currentLevel", sa.String(), nullable=True))

    # Backfill existing profiles to 'completed' state if they have onboarding_completed_at
    # Otherwise set to 'not_started'
    op.execute(
        """
        UPDATE "LearningProfile"
        SET "onboardingState" = CASE
            WHEN "onboardingCompletedAt" IS NOT NULL THEN 'completed'
            WHEN purpose IS NOT NULL THEN 'purpose_set'
            ELSE 'not_started'
        END
        WHERE "onboardingState" IS NULL
    """
    )

    # Make onboardingState non-nullable with default
    op.alter_column(
        "LearningProfile",
        "onboardingState",
        existing_type=sa.String(),
        nullable=False,
        server_default="not_started",
    )


def downgrade() -> None:
    op.drop_column("LearningProfile", "currentLevel")
    op.drop_column("LearningProfile", "skillName")
    op.drop_column("LearningProfile", "examDate")
    op.drop_column("LearningProfile", "examName")
    op.drop_column("LearningProfile", "onboardingState")
