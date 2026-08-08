"""Promote quiz questions to a preparation-owned question bank.

``QuizQuestion`` rows belonged to a single ``QuizSession``. That made "every
question for this preparation" inexpressible, so the workspace could not offer a
question bank, per-question statistics had nowhere to live, and every session
regenerated questions from scratch even for material already covered.

After this migration:

    PrepQuestion          a question owned by the preparation
    QuizSessionQuestion   which questions a session asked, and in what order

**Existing questions are migrated, not abandoned.** Leaving old rows on a
read-only ``QuizQuestion`` table would have been the lower-risk option, but it
leaves two shapes for the same concept coexisting indefinitely — which is the
technical debt this change exists to remove.

# Why this is safer than it looks

``PrepQuestion`` rows **reuse the id of the ``QuizQuestion`` row they came from.**
``QuizAnswer.questionId`` therefore keeps pointing at the same value, and not a
single answer row has to be rewritten: only the foreign key target changes. That
also means the migration is a copy plus a constraint swap, with no id remapping
table and no window where answers dangle.

The consequence is that **questions are deliberately not de-duplicated here.**
Collapsing identical question text across sessions would change ids and break the
answer references. De-duplication, if ever wanted, is a separate change made
against a schema that can express it.

# Safety

- Row counts are asserted on both sides; a mismatch raises and rolls back rather
  than silently losing questions.
- Orphaned questions (no surviving session) are counted and reported before the
  copy, because an inner join would otherwise drop them without a word.
- ``downgrade`` rebuilds ``QuizQuestion`` from the bank and the links, restoring
  ids and per-session order.

# Applying it

Run over a **direct** connection, not a transaction-mode pooler. On Supabase that
means port 5432, not 6543: DDL over pgbouncer's transaction mode is unreliable.

Revision ID: 008_promote_questions_to_bank
Revises: 007_add_prep_intent_fields
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "008_promote_questions_to_bank"
down_revision = "007_add_prep_intent_fields"
branch_labels = None
depends_on = None


# A 25-character lowercase hex id, matching `uuid4().hex[:25]` as used by the
# models. Built from md5 rather than gen_random_uuid() so the migration does not
# depend on pgcrypto being installed.
_NEW_ID = "substr(md5(random()::text || clock_timestamp()::text), 1, 25)"


def upgrade() -> None:
    connection = op.get_bind()

    # --- Pre-flight: understand what we are about to move -------------------
    total_questions = connection.execute(
        sa.text('SELECT COUNT(*) FROM "QuizQuestion"')
    ).scalar_one()

    # Questions whose session no longer exists. The FK is ON DELETE CASCADE so
    # there should be none, but an inner join would drop any that do exist
    # silently, and silently losing a learner's question history is not
    # acceptable even for one row.
    orphaned = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM "QuizQuestion" q
            LEFT JOIN "QuizSession" s ON s.id = q."quizSessionId"
            WHERE s.id IS NULL
            """
        )
    ).scalar_one()

    print(f"[008] QuizQuestion rows: {total_questions}, orphaned (no session): {orphaned}")

    # --- Schema ------------------------------------------------------------
    op.create_table(
        "PrepQuestion",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "prepId",
            sa.String(),
            sa.ForeignKey("ExamPrep.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prepTopicId",
            sa.String(),
            sa.ForeignKey("PrepTopic.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("questionText", sa.Text(), nullable=False),
        sa.Column("questionType", sa.String(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correctAnswer", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("timesAnswered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timesCorrect", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("PrepQuestion_prepId_idx", "PrepQuestion", ["prepId"])
    op.create_index(
        "PrepQuestion_prepId_prepTopicId_idx", "PrepQuestion", ["prepId", "prepTopicId"]
    )

    op.create_table(
        "QuizSessionQuestion",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "quizSessionId",
            sa.String(),
            sa.ForeignKey("QuizSession.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prepQuestionId",
            sa.String(),
            sa.ForeignKey("PrepQuestion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("orderIndex", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("quizSessionId", "prepQuestionId", name="QuizSessionQuestion_unique"),
    )
    op.create_index(
        "QuizSessionQuestion_quizSessionId_idx", "QuizSessionQuestion", ["quizSessionId"]
    )
    op.create_index(
        "QuizSessionQuestion_prepQuestionId_idx", "QuizSessionQuestion", ["prepQuestionId"]
    )
    op.create_index(
        "QuizSessionQuestion_quizSessionId_orderIndex_idx",
        "QuizSessionQuestion",
        ["quizSessionId", "orderIndex"],
    )

    # --- Data: bank the questions, preserving ids --------------------------
    connection.execute(
        sa.text(
            """
            INSERT INTO "PrepQuestion" (
                id, "prepId", "prepTopicId", "questionText", "questionType",
                options, "correctAnswer", explanation,
                "timesAnswered", "timesCorrect", "createdAt", "updatedAt"
            )
            SELECT
                q.id,
                s."prepId",
                q."prepTopicId",
                q."questionText",
                q."questionType",
                q.options,
                q."correctAnswer",
                q.explanation,
                0,
                0,
                q."createdAt",
                q."updatedAt"
            FROM "QuizQuestion" q
            JOIN "QuizSession" s ON s.id = q."quizSessionId"
            """
        )
    )

    connection.execute(
        sa.text(
            f"""
            INSERT INTO "QuizSessionQuestion" (
                id, "quizSessionId", "prepQuestionId", "orderIndex",
                "createdAt", "updatedAt"
            )
            SELECT
                {_NEW_ID},
                q."quizSessionId",
                q.id,
                COALESCE(q."orderIndex", 0),
                q."createdAt",
                q."updatedAt"
            FROM "QuizQuestion" q
            JOIN "QuizSession" s ON s.id = q."quizSessionId"
            """
        )
    )

    # Backfill lifetime statistics from answers that already exist.
    connection.execute(
        sa.text(
            """
            UPDATE "PrepQuestion" pq
            SET "timesAnswered" = stats.answered,
                "timesCorrect" = stats.correct
            FROM (
                SELECT "questionId" AS qid,
                       COUNT(*) AS answered,
                       COUNT(*) FILTER (WHERE "isCorrect") AS correct
                FROM "QuizAnswer"
                GROUP BY "questionId"
            ) AS stats
            WHERE pq.id = stats.qid
            """
        )
    )

    # --- Verify before destroying anything ---------------------------------
    expected = total_questions - orphaned
    banked = connection.execute(sa.text('SELECT COUNT(*) FROM "PrepQuestion"')).scalar_one()
    linked = connection.execute(sa.text('SELECT COUNT(*) FROM "QuizSessionQuestion"')).scalar_one()

    if banked != expected or linked != expected:
        raise RuntimeError(
            "008 aborted: question counts do not match. "
            f"expected={expected} banked={banked} linked={linked} "
            f"(QuizQuestion={total_questions}, orphaned={orphaned}). "
            "Nothing has been dropped; the transaction will roll back."
        )

    # Any answer whose question did not survive the move would dangle once the FK
    # is repointed. Catch it here rather than at constraint-creation time.
    dangling = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM "QuizAnswer" a
            LEFT JOIN "PrepQuestion" pq ON pq.id = a."questionId"
            WHERE pq.id IS NULL
            """
        )
    ).scalar_one()
    if dangling:
        raise RuntimeError(
            f"008 aborted: {dangling} QuizAnswer rows reference a question that was "
            "not migrated. Nothing has been dropped; the transaction will roll back."
        )

    print(f"[008] banked {banked} questions, linked {linked} session placements")

    # --- Repoint the answer foreign key and drop the old table -------------
    op.drop_constraint("QuizAnswer_questionId_fkey", "QuizAnswer", type_="foreignkey")
    op.create_foreign_key(
        "QuizAnswer_questionId_fkey",
        "QuizAnswer",
        "PrepQuestion",
        ["questionId"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_table("QuizQuestion")


def downgrade() -> None:
    """Rebuild ``QuizQuestion`` from the bank and its session links.

    Questions that were banked but never asked in a session cannot be represented
    by the old schema, which required a ``quizSessionId``. They are dropped, and
    the count is reported. That is a genuine loss of data the old shape has no
    room for, and is the reason this downgrade is a recovery path rather than a
    routine reversal.
    """
    connection = op.get_bind()

    unasked = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM "PrepQuestion" pq
            LEFT JOIN "QuizSessionQuestion" sq ON sq."prepQuestionId" = pq.id
            WHERE sq.id IS NULL
            """
        )
    ).scalar_one()
    if unasked:
        print(
            f"[008 downgrade] {unasked} banked questions were never asked in a "
            "session and cannot be represented by QuizQuestion. They will be lost."
        )

    op.create_table(
        "QuizQuestion",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "quizSessionId",
            sa.String(),
            sa.ForeignKey("QuizSession.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prepTopicId",
            sa.String(),
            sa.ForeignKey("PrepTopic.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("questionText", sa.Text(), nullable=False),
        sa.Column("questionType", sa.String(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correctAnswer", sa.String(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("orderIndex", sa.Integer(), nullable=True, server_default="0"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("QuizQuestion_quizSessionId_idx", "QuizQuestion", ["quizSessionId"])

    # One row per session placement, keeping the original question id so answers
    # continue to resolve.
    connection.execute(
        sa.text(
            """
            INSERT INTO "QuizQuestion" (
                id, "quizSessionId", "prepTopicId", "questionText", "questionType",
                options, "correctAnswer", explanation, "orderIndex",
                "createdAt", "updatedAt"
            )
            SELECT
                pq.id,
                sq."quizSessionId",
                pq."prepTopicId",
                pq."questionText",
                pq."questionType",
                pq.options,
                pq."correctAnswer",
                pq.explanation,
                sq."orderIndex",
                pq."createdAt",
                pq."updatedAt"
            FROM "PrepQuestion" pq
            JOIN "QuizSessionQuestion" sq ON sq."prepQuestionId" = pq.id
            """
        )
    )

    # Answers to questions that could not be restored would dangle.
    connection.execute(
        sa.text(
            """
            DELETE FROM "QuizAnswer"
            WHERE "questionId" NOT IN (SELECT id FROM "QuizQuestion")
            """
        )
    )

    op.drop_constraint("QuizAnswer_questionId_fkey", "QuizAnswer", type_="foreignkey")
    op.create_foreign_key(
        "QuizAnswer_questionId_fkey",
        "QuizAnswer",
        "QuizQuestion",
        ["questionId"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_table("QuizSessionQuestion")
    op.drop_table("PrepQuestion")
