"""Refuse, in the database, to complete a preparation nobody answered.

**This is the guard that would have prevented 22 wrong rows.**

A stale deployment sharing this database ran the pre-review version of
`learning.mark_completed_preparations` at 01:00 UTC and set `status = COMPLETED` on every preparation whose
exam date had passed — including four the review sweep had asked about seven hours earlier. Every one of the
22 completed preparations in this database has **no `PrepOutcome` behind it**. The single datum the whole
post-exam review exists to collect was destroyed, silently, by a cron.

## Why this has to be in the database

The invariant was already documented in four places and enforced in Python: `mark_completed` raises a 409 for
an `AWAITING_REVIEW` preparation, and both clients hide the control. **None of that helped**, because the
writer was old code talking to the same tables with its own old repository. A Python guard protects the
version that contains it and nothing else — and *every deploy* is a window where two versions coexist.

So the rule moves to the one place both versions share.

## The rule

Refuse `AWAITING_REVIEW → COMPLETED` when no `PrepOutcome` exists for the sitting the row now describes.
Deliberately narrow:

- **Only that transition.** `SETUP`/`IN_PROGRESS → COMPLETED` is a learner abandoning a preparation before
  the exam, which is legitimate and stays allowed.
- **`AWAITING_REVIEW → IN_PROGRESS` is untouched**, which is the postponed-exam path.
- **`COMPLETED → AWAITING_REVIEW` is untouched**, which is how `repair_prep_review_state.py` works.
- Matched on `(prepId, examDate)` like the service does, so a postponed preparation's earlier sittings cannot
  satisfy the check for a later one.

## The `AT TIME ZONE 'UTC'` is not decoration

`ExamPrep."examDate"` is `timestamp without time zone` and `PrepOutcome."examDate"` is `timestamp with time
zone` — the same split the ORM hides and that `prep_outcome_service._as_utc` and `goal_metrics._utc` both
exist to paper over. Comparing them directly makes Postgres interpret the naive side **in the session's
timezone**, so the result depends on a connection setting:

    tz=UTC              implicit=True
    tz=Africa/Lagos     implicit=False
    tz=America/New_York implicit=False

The first version of this migration did exactly that. It passed against the production pooler, which happens
to be UTC, and would have **refused every legitimate completion** on any connection that was not — turning a
guard against data loss into an outage on the one path it is supposed to protect. Reading the naive side as
UTC explicitly is timezone-independent and matches the convention the Python side already applies.

Caught by `scripts/debug/verify_056_guard.py`, which asserts the transitions the guard must *allow* as well as
the ones it must refuse. Only the allow cases failed; a probe that checked refusals alone would have declared
this correct.

The legitimate path is safe: `record_outcome` commits the outcome through `upsert_prep_outcome` **before** it
writes the status, in a separate session, so the row is visible to the trigger. Verified against the source
rather than assumed — reversing those two statements would deadlock this guard against the only writer allowed
through it, so the ordering is now load-bearing and a comment says so at the call site.

## What it does to a stale deployment

Makes it fail, loudly, every night, instead of destroying data quietly. That is the point and not a
side-effect: a rollback that reintroduces the old sweep should be impossible to miss.

`ERRCODE` is a custom `MG001` so a caller can recognise this specific refusal rather than parsing the message.

Escape hatch, should a genuine backfill ever need one — deliberately not a config flag, because a flag that
can be left on is a guard that is off:

    ALTER TABLE "ExamPrep" DISABLE TRIGGER exam_prep_completion_guard;
    -- ... the backfill ...
    ALTER TABLE "ExamPrep" ENABLE TRIGGER exam_prep_completion_guard;

Note on the revision id: `alembic_version.version_num` is `varchar(32)`, and an over-long id applies the DDL
and then fails to record it — so this is kept short, as 055's note explains.
"""

from alembic import op

revision = "056_prep_completion_guard"
down_revision = "055_goal_action_answer"
branch_labels = None
depends_on = None


# `BEFORE UPDATE` with a `WHEN` clause, so the function body runs only on the one transition it polices
# rather than on every write to a hot table.
_FUNCTION = """
CREATE OR REPLACE FUNCTION exam_prep_completion_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM "PrepOutcome" o
        WHERE o."prepId" = NEW.id
          -- `ExamPrep."examDate"` is naive and `PrepOutcome."examDate"` is not. Read the naive side as UTC
          -- explicitly; the implicit comparison uses the session timezone and is False outside UTC.
          AND o."examDate" = (NEW."examDate" AT TIME ZONE 'UTC')
    ) THEN
        RAISE EXCEPTION
            'Preparation % is awaiting review and has no recorded outcome for its %s sitting; '
            'completing it would assert an outcome nobody reported',
            NEW.id, NEW."examDate"
            USING ERRCODE = 'MG001';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER exam_prep_completion_guard
BEFORE UPDATE ON "ExamPrep"
FOR EACH ROW
WHEN (OLD.status = 'AWAITING_REVIEW' AND NEW.status = 'COMPLETED')
EXECUTE FUNCTION exam_prep_completion_guard();
"""


def upgrade() -> None:
    op.execute(_FUNCTION)
    # Dropped first so the migration is safe to re-run against a database where a partial apply left the
    # trigger behind — the same defensiveness 050–055 use.
    op.execute('DROP TRIGGER IF EXISTS exam_prep_completion_guard ON "ExamPrep";')
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS exam_prep_completion_guard ON "ExamPrep";')
    op.execute("DROP FUNCTION IF EXISTS exam_prep_completion_guard();")
