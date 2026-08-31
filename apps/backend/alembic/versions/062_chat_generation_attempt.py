"""Persist durable Ask generation attempts and cost attribution."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "062_chat_generation_attempt"
down_revision = "061_notification_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ChatGenerationAttempt",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("sessionId", sa.String(), nullable=False),
        sa.Column("userMessageId", sa.String(), nullable=False),
        sa.Column("assistantMessageId", sa.String(), nullable=True),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("failureCode", sa.String(), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("askMode", sa.String(), nullable=False),
        sa.Column(
            "toolSideEffects",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assistantMessageId"], ["ChatMessage.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["sessionId"], ["ChatSession.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["userMessageId"], ["ChatMessage.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ChatGenerationAttempt_sessionId_idx", "ChatGenerationAttempt", ["sessionId"]
    )
    op.create_index(
        "ChatGenerationAttempt_userMessageId_idx",
        "ChatGenerationAttempt",
        ["userMessageId"],
    )
    op.create_index(
        "ChatGenerationAttempt_userId_createdAt_idx",
        "ChatGenerationAttempt",
        ["userId", "createdAt"],
    )
    op.execute(
        sa.text(
            'CREATE UNIQUE INDEX "ChatGenerationAttempt_one_active_per_session" '
            'ON "ChatGenerationAttempt" ("sessionId") '
            "WHERE status IN ('PENDING', 'RUNNING')"
        )
    )
    op.add_column("LlmCostRecord", sa.Column("attemptId", sa.String(), nullable=True))
    op.create_foreign_key(
        "LlmCostRecord_attemptId_fkey",
        "LlmCostRecord",
        "ChatGenerationAttempt",
        ["attemptId"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("LlmCostRecord_attemptId_idx", "LlmCostRecord", ["attemptId"])
    op.add_column(
        "ChatMessage",
        sa.Column(
            "answerScope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ChatMessage", "answerScope")
    op.drop_index("LlmCostRecord_attemptId_idx", table_name="LlmCostRecord")
    op.drop_constraint(
        "LlmCostRecord_attemptId_fkey", "LlmCostRecord", type_="foreignkey"
    )
    op.drop_column("LlmCostRecord", "attemptId")
    op.execute(
        sa.text('DROP INDEX IF EXISTS "ChatGenerationAttempt_one_active_per_session"')
    )
    op.drop_index(
        "ChatGenerationAttempt_userId_createdAt_idx", table_name="ChatGenerationAttempt"
    )
    op.drop_index(
        "ChatGenerationAttempt_userMessageId_idx", table_name="ChatGenerationAttempt"
    )
    op.drop_index(
        "ChatGenerationAttempt_sessionId_idx", table_name="ChatGenerationAttempt"
    )
    op.drop_table("ChatGenerationAttempt")
