"""Let the learner answer the goal nudge, and store what they said.

**Nothing has ever recorded whether an intervention worked.** The nightly ladder decides that a goal is
slipping, extends a deadline or asks a question, and writes down what it did — and then the story ends. There
is no column anywhere holding the learner's reply, so every future version of the ladder is guessing at
exactly the same rate as the first one. `retention_service.record_intervention_outcome` was written for this
job and has zero callers anywhere in `src` or `tests`.

These two columns are the feedback loop. Three answers, each meaning something the system cannot infer:

- `keep_going` — they still want it. The system was right to chase, and the goal stays as it is.
- `set_aside` — stop chasing them. The goal becomes `ARCHIVED`.
- `already_done` — the work happened and the measurement did not see it. The goal becomes `COMPLETED`, and
  this is the answer worth the most, because it says the measurement is wrong rather than the learner.

**Null is the most common value and the most informative.** It is how "we asked and heard nothing" is told
apart from "we never asked", and the two justify completely different next moves — silence after three asks
is an answer, while never having asked is a bug. Which is why the response is nullable rather than defaulted
to anything.

**`respondedAt` is stored rather than inferred**, and paired with the response by a CHECK. "Answered
immediately" and "answered six days later" are different facts about how well the ask worked, and a reply
time without a reply is a row that contradicts itself.

**Withheld deliberately until now.** Migration 053 added this table without these columns and said why:
nothing could answer yet, and a column the schema offers that nothing can fill is the accept-and-ignore
defect this codebase keeps closing. They arrive with the endpoint that writes them.

**`record_intervention_outcome` still has no callers, and that is deliberate.** It writes to
`RetentionIntervention`, whose whole subsystem is unreachable — `tasks/retention_check.py` is not imported by
`tasks/__init__.py`, has no beat entry, and imports `src.workers.celery_app` rather than
`src.core.celery_app`. Routing goal answers into a table nothing reads, to satisfy the letter of a plan,
would have put the feedback loop somewhere it could not be used. It is recorded here, next to the decision it
is a reply to. Reviving retention is separate work.

No backfill. Every existing row is unanswered, which is true, and inventing replies would poison the only
data this table exists to collect.

Revision ID: 055_goal_action_answer
Revises: 054_notification_push
Create Date: 2026-08-27

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the DDL
then fails the version bump, rolling the whole transaction back with a `StringDataRightTruncationError` about
a value nobody wrote — see `046_schedule_block_completion`. This one is 21 characters.
"""

import sqlalchemy as sa

from alembic import op

revision = "055_goal_action_answer"
down_revision = "054_notification_push"
branch_labels = None
depends_on = None

TABLE = "GoalLifecycleAction"
RESPONSE_CHECK = "GoalLifecycleAction_learnerResponse_check"
PAIR_CHECK = "GoalLifecycleAction_response_pair_check"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("learnerResponse", sa.String(), nullable=True))
    op.add_column(TABLE, sa.Column("respondedAt", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        RESPONSE_CHECK,
        TABLE,
        '"learnerResponse" IS NULL OR "learnerResponse" IN '
        "('keep_going', 'set_aside', 'already_done')",
    )
    # Neither half of an answer is meaningful without the other.
    op.create_check_constraint(
        PAIR_CHECK, TABLE, '("learnerResponse" IS NULL) = ("respondedAt" IS NULL)'
    )


def downgrade() -> None:
    op.drop_constraint(PAIR_CHECK, TABLE, type_="check")
    op.drop_constraint(RESPONSE_CHECK, TABLE, type_="check")
    op.drop_column(TABLE, "respondedAt")
    op.drop_column(TABLE, "learnerResponse")
