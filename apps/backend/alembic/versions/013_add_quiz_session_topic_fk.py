"""Give QuizSession.topicId a foreign key.

`QuizSession.topicId` drives per-topic mastery, and therefore readiness, but it has
never been constrained: the table has foreign keys for `prepId` and `userId` only.
A topic id pointing at a deleted `PrepTopic` would silently attribute practice to
nothing, and the mastery aggregate would quietly lose the rows rather than fail.

# Safe to apply

Audited against the live database before writing this:

    SELECT COUNT(*) FROM "QuizSession" s
    LEFT JOIN "PrepTopic" t ON t.id = s."topicId"
    WHERE s."topicId" IS NOT NULL AND t.id IS NULL;
    -- orphans = 0

So the constraint can be added without repairing any rows first. The column stays
nullable: a session that is not scoped to a single topic is legitimate, and 6 of 6
existing rows have `topicId` NULL.

# Why SET NULL rather than CASCADE

Deleting a topic must not delete the practice history that happened under it. The
learner did that work, and `PracticeObservation` (migration 012) deliberately keeps
evidence alive past the session that produced it for the same reason. `SET NULL`
detaches the attribution and keeps the session.

Revision ID: 013_add_quiz_session_topic_fk
Revises: 012_add_practice_observations
Create Date: 2026-08-09
"""

from alembic import op

# revision identifiers
revision = "013_add_quiz_session_topic_fk"
down_revision = "012_add_practice_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_foreign_key(
        "QuizSession_topicId_fkey",
        "QuizSession",
        "PrepTopic",
        ["topicId"],
        ["id"],
        ondelete="SET NULL",
    )
    # Per-topic mastery reads sessions by topic; without this the aggregate is a scan.
    op.create_index("QuizSession_topicId_idx", "QuizSession", ["topicId"])


def downgrade() -> None:
    op.drop_index("QuizSession_topicId_idx", table_name="QuizSession")
    op.drop_constraint("QuizSession_topicId_fkey", "QuizSession", type_="foreignkey")
