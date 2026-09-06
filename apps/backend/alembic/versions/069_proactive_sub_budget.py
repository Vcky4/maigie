"""Give proactive AI its own counter inside the month.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision M, rule 1. Two Celery tasks call a model per learner per
schedule — nightly discovery recommendations and weekly reflections — and Phase 3b put both on the
meter, so the spend is now charged to the learner's window like everything else. That is the first
half of the rule. The second half is not built by charging it:

    "The sub-cap stops a background task from consuming an allowance the learner was saving for a
    study session."

Charged and *bounded* are different properties. Without a bound, a learner who has not opened the app
since Tuesday can have a meaningful share of their month spent by tasks running on their behalf, and
then sit down on Saturday to find the allowance already gone. Nothing about that is visible to them:
they did not run the operations, and the refusal names a window they did not spend.

**One column, not a second currency.** `usageMonthProactiveUnitsUsed` counts the same units as
`usageMonthUnitsUsed`, over the same month, and every proactive unit is added to *both*. It is a
category tag on existing spend rather than a parallel budget, which is what keeps it clear of Decision
R's rule against a third meter: Decision R forbids a new *currency* with its own counter, and this is
the same currency being asked a second question. A learner cannot exceed their month by using it, and
cannot exceed the sub-cap without also drawing down the month.

The cap itself is not stored. It is 20% of `Entitlement.monthly_backstop`, derived on read for the same
reason the voice allowance is: a stored cap and the entitlement that implies it are two facts that can
disagree, and a subscription upgrade should raise the sub-cap the moment it raises the backstop rather
than at the next boundary.

Reset with the month, by the same `window_state` roll that resets `usageMonthUnitsUsed` — one boundary,
so the two counters cannot land on different months.

No data to carry forward: proactive spend has never been distinguishable from any other, which is the
gap this closes.
"""

import sqlalchemy as sa

from alembic import op

revision = "069_proactive_sub_budget"
down_revision = "068_voice_balance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "User",
        sa.Column(
            "usageMonthProactiveUnitsUsed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("User", "usageMonthProactiveUnitsUsed")
