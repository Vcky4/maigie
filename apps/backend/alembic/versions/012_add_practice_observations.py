"""Record what practice reveals about a learner, and add requestable hints.

Phase A of the learning-intelligence design. This migration only makes the system
*observe*. Nothing consumes these signals yet, deliberately — the reasoning layer
lands in Phase B, and shipping the observation first means Phase B can be built
against real data instead of guesses.

# Why a separate observation table

`QuizAnswer` already records correctness and time taken, and would have been the
obvious place. It is the wrong place, for one reason: it is `ON DELETE CASCADE`
from `QuizSession`. Deleting a practice session would erase what the system had
learned about the learner, which makes this impossible:

    Earlier observations should be revisited in light of new evidence.
    -- content/intelligence/ch23-memory.mdx

So observations hold `quizSessionId` and `prepQuestionId` as `SET NULL`: the
evidence outlives the session that produced it. It still cascades from `User` and
from `ExamPrep`, because deleting your account or deleting a preparation is a
request to forget, and the same chapter is explicit that memory must be
forgettable when asked.

`difficulty` is **copied, not joined**. A question's difficulty may be
recalibrated later, and an observation has to record what was true at the moment
it happened rather than what we believe now.

# Hints

`PrepQuestion.hintNudge` holds a hint that points at the approach without giving
the answer away. Only one column: the second hint level (eliminating a wrong
multiple-choice option) is computed deterministically at request time, so it needs
no storage and cannot drift.

`QuizSessionQuestion.hintCount` counts hints taken for one question in one
session. It lives on the link rather than the question because the same banked
question may be met again in a later session with no hint needed.

Revision ID: 012_add_practice_observations
Revises: 011_add_readiness_snapshots
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "012_add_practice_observations"
down_revision = "011_add_readiness_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Hints ---------------------------------------------------------------
    op.add_column("PrepQuestion", sa.Column("hintNudge", sa.Text(), nullable=True))
    op.add_column(
        "QuizSessionQuestion",
        sa.Column("hintCount", sa.Integer(), nullable=False, server_default="0"),
    )

    # --- Observations --------------------------------------------------------
    op.create_table(
        "PracticeObservation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "userId",
            sa.String(),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: attribution can be lost without the observation
        # becoming worthless.
        sa.Column(
            "prepTopicId",
            sa.String(),
            sa.ForeignKey("PrepTopic.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "prepQuestionId",
            sa.String(),
            sa.ForeignKey("PrepQuestion.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # SET NULL is the whole reason this table exists separately from QuizAnswer.
        sa.Column(
            "quizSessionId",
            sa.String(),
            sa.ForeignKey("QuizSession.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("isCorrect", sa.Boolean(), nullable=False),
        # Null when the client did not report it, which must stay distinguishable
        # from "answered instantly".
        sa.Column("responseMs", sa.Integer(), nullable=True),
        sa.Column("hintUsed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hintCount", sa.Integer(), nullable=False, server_default="0"),
        # Copied at answer time. See the note above.
        sa.Column("difficulty", sa.String(), nullable=True),
        sa.Column("observedAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # The Phase B read pattern: one learner, one topic, most recent first, because
    # recent evidence is weighted more heavily.
    op.create_index(
        "PracticeObservation_userId_prepTopicId_observedAt_idx",
        "PracticeObservation",
        ["userId", "prepTopicId", "observedAt"],
    )
    op.create_index(
        "PracticeObservation_userId_prepId_observedAt_idx",
        "PracticeObservation",
        ["userId", "prepId", "observedAt"],
    )
    op.create_index(
        "PracticeObservation_prepQuestionId_idx",
        "PracticeObservation",
        ["prepQuestionId"],
    )

    # --- Backfill from existing answers -------------------------------------
    # Every answer already recorded is a real observation, and the model is better
    # off knowing about them. `hintUsed` is false because hints did not exist, and
    # `responseMs` converts the seconds already stored. This states facts rather
    # than inferring any.
    op.execute(
        """
        INSERT INTO "PracticeObservation" (
            id, "userId", "prepId", "prepTopicId", "prepQuestionId", "quizSessionId",
            "isCorrect", "responseMs", "hintUsed", "hintCount", difficulty,
            "observedAt", "createdAt", "updatedAt"
        )
        SELECT
            substr(md5(random()::text || clock_timestamp()::text || a.id), 1, 25),
            s."userId",
            pq."prepId",
            pq."prepTopicId",
            pq.id,
            a."quizSessionId",
            COALESCE(a."isCorrect", false),
            CASE WHEN a."timeTakenSeconds" IS NULL THEN NULL
                 ELSE a."timeTakenSeconds" * 1000 END,
            false,
            0,
            pq.difficulty,
            a."createdAt",
            NOW(),
            NOW()
        FROM "QuizAnswer" a
        JOIN "PrepQuestion" pq ON pq.id = a."questionId"
        JOIN "QuizSession" s ON s.id = a."quizSessionId"
        """
    )


def downgrade() -> None:
    op.drop_index("PracticeObservation_prepQuestionId_idx", table_name="PracticeObservation")
    op.drop_index(
        "PracticeObservation_userId_prepId_observedAt_idx", table_name="PracticeObservation"
    )
    op.drop_index(
        "PracticeObservation_userId_prepTopicId_observedAt_idx",
        table_name="PracticeObservation",
    )
    op.drop_table("PracticeObservation")
    op.drop_column("QuizSessionQuestion", "hintCount")
    op.drop_column("PrepQuestion", "hintNudge")
