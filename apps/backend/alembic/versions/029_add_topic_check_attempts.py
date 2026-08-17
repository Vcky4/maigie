"""Record every answer to a lesson's knowledge check.

The end-of-lesson check was answered entirely in the browser. `Check answer` set a local flag, the
verdict was computed from the `correct` field the API had already sent down with the choices, and
nothing was written anywhere. The check gated Continue — a wrong answer held the learner on the
section — but the gate recorded only its own outcome, "this section was completed". A learner who got
it wrong four times and a learner who got it right immediately produced identical data.

That is the signal a review needs. Understanding is what the learner knew *before* being told, so the
question worth asking later is not "did they eventually pass" but "did they pass first time". This
table makes that answerable.

## Why every attempt, not a latest-answer column

Changing the answer is allowed, and deliberately so: the reader reveals the correct choice and the
explanation, then lets the learner pick again. A single column holding the current answer would
therefore record the last thing they clicked after being shown the answer — which is nearly always
correct, and says nothing about what they understood. One row per press keeps the first attempt
intact, so a topic failed and then passed is still visibly a topic that was failed.

## Why the question and the chosen label are snapshotted

`Topic.knowledgeCheck` is JSON on the topic and is replaced wholesale when a lesson is regenerated —
the same reason section completion is discarded on regeneration. Without a snapshot, an attempt would
point at a `choiceId` that no longer exists, on a question that may no longer be asked, and the
history would decay into unreadable ids. Storing the question as asked and the label as chosen makes
each row self-describing and independent of later rewrites.

The *correct* label is deliberately not snapshotted. A revisit wants the current right answer, and if
the check has since been rewritten then the answer recorded against the old wording would be worse
than none.

## Why not QuizSession

The same reason the check is JSON rather than a quiz in the first place: `QuizSession` models a timed,
scored, multi-question attempt that feeds readiness scoring, and one formative question at the end of a
lesson is not that. Borrowing it would put an abandoned scored session into preparation analytics for
every lesson opened.

Revision ID: 029_add_check_attempts
Revises: 028_add_teaching_style
Create Date: 2026-08-17

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long revision id
fails at the last statement of the upgrade, after the DDL, and rolls the whole thing back. The id
below is 21 characters.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "029_add_check_attempts"
down_revision = "028_add_teaching_style"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "TopicCheckAttempt",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("topicId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        # The choice as identified in `Topic.knowledgeCheck` at the time of the attempt.
        sa.Column("choiceId", sa.String(), nullable=False),
        # Snapshots, so the row still means something after the lesson is rewritten.
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("choiceLabel", sa.Text(), nullable=False),
        # Graded on the server from the stored check, never accepted from the client. The answer key
        # ships to the browser so the reader can reveal the verdict without a round trip, which means
        # a client-supplied verdict would be a number the learner's own page got to choose.
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # No unique constraint on (userId, topicId): repeated attempts are the entire point.
    )
    # Every read is "this learner's attempts on this topic, oldest first", because the first attempt
    # is the one that carries the meaning. The index carries that sort.
    op.create_index(
        "TopicCheckAttempt_userId_topicId_createdAt_idx",
        "TopicCheckAttempt",
        ["userId", "topicId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index("TopicCheckAttempt_userId_topicId_createdAt_idx", table_name="TopicCheckAttempt")
    op.drop_table("TopicCheckAttempt")
