"""Persist the rhythm the create wizard asks the learner to choose.

Migration 022 closed step 3 of the wizard. This closes steps 1 and 2, which were never
audited and turned out to collect four more things with nowhere to go:

| Wizard asks | Went where |
| ----------- | ---------- |
| Pace — "5 sessions each week" | nowhere |
| Focused time per session — "35 minutes" | nowhere; the day budget came from observed behaviour |
| Preferred days — Mon/Wed/Fri/Sat | nowhere; items landed on consecutive calendar days |
| Path shape — "Master a complex skill", four phases | nowhere; the generator invented its own phases |

The last two are the ones that produce a visibly wrong plan rather than a merely
forgotten preference. `_distribute_items` walks day 0, 1, 2, … and `_redistribute_plan`
does the same, so a learner who said they study Mondays, Wednesdays, Fridays and
Saturdays got work scheduled on the Tuesday. And step 4 of the wizard renders the chosen
template's phases under the heading "Generated roadmap", while the plan that gets created
is grouped by whatever phases the model returned — the preview and the plan disagreed.

**`sessionsPerWeek` and `sessionMinutes`** are the two facts behind the pace, kept
separately rather than only as their product. `weeklyGoalMinutes` already stores the
product, but it cannot be taken apart again: 175 minutes a week is 5×35 or 7×25, and the
plan detail page prints "35 min · 5× week". Storing only the total would mean either
dropping that line or guessing at a factorisation.

**`preferredDays`** is a JSON array of ISO weekday numbers (1 = Monday … 7 = Sunday).
Numbers, not the UI's "Mon"/"Tue" labels, because the labels are English and the
scheduler compares against `date.isoweekday()`. An empty array and null mean different
things and both occur: null is "never asked", and the scheduler falls back to every day;
empty would mean "no day is acceptable", which is not a plan, so it is rejected at the
contract rather than stored.

**`shape`** is the template the learner picked, e.g. `skill-mastery`. Stored so the
generator can be told which phase structure to follow — the point is not to record the
choice, it is to honour it, so that the roadmap previewed on step 4 is the roadmap built.

All four are nullable. An existing plan was created before the questions were asked, and
a default would assert a pace, a session length or an availability that its learner never
stated — the same reasoning that left `strategy` nullable in 021 rather than backfilling
every old plan to `EVEN`.

Revision ID: 023_add_plan_rhythm
Revises: 022_add_plan_connections
Create Date: 2026-08-14

Note on the identifier: `alembic_version.version_num` is `varchar(32)`, so an over-long
revision id fails at the last statement of the upgrade, after the DDL, and rolls it back.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "023_add_plan_rhythm"
down_revision = "022_add_plan_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("StudyPlan", sa.Column("sessionsPerWeek", sa.Integer(), nullable=True))
    op.add_column("StudyPlan", sa.Column("sessionMinutes", sa.Integer(), nullable=True))
    # JSON rather than an integer array: every other list on this model is JSON
    # (`StudyPlan.skills`, `LearningProfile.subjects`), and a seven-element list of small
    # integers gains nothing from a Postgres-specific type the SQLite test engine cannot
    # take.
    op.add_column("StudyPlan", sa.Column("preferredDays", sa.JSON(), nullable=True))
    op.add_column("StudyPlan", sa.Column("shape", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("StudyPlan", "shape")
    op.drop_column("StudyPlan", "preferredDays")
    op.drop_column("StudyPlan", "sessionMinutes")
    op.drop_column("StudyPlan", "sessionsPerWeek")
