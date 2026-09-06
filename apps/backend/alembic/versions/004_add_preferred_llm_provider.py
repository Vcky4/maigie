"""Add preferredLlmProvider to LearningProfile

Revision ID: 004
Revises: 003
Create Date: 2026-07-21
"""

import sqlalchemy as sa

from alembic import op

revision = "004_add_preferred_llm_provider"
down_revision = "003_add_personal_learning_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "LearningProfile",
        sa.Column("preferredLlmProvider", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("LearningProfile", "preferredLlmProvider")
