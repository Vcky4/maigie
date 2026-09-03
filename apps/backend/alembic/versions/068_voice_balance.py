"""Give live voice its own balance, off the unit window.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.3. At 200 units/minute a voice minute costs 40× a Flash-Lite chat
turn, so a single allowance covering both text and voice had to be priced for the voice case and was
spent almost entirely on the text case. A learner who spent their window on voice hit the COGS
ceiling; one who spent it on text did not come close. Unbundling is what lets the NGN ladder be
affordable and what makes the marketing claim checkable — "5 hours of full Plus including 10 minutes
of live voice tutoring" is a promise a counter can keep, where "about 15 minutes" was an
allowance-division artefact nothing enforced.

Three columns, and the split between the first two is the whole design.

`voiceSecondsRemaining` is a **granted** balance. It belongs to whatever entitlement granted it, and
it is re-derived on read: when `voiceAllowanceSourceId` stops matching the source the entitlement
currently names, the balance resets to that source's allowance. So a subscription period rolling over
re-grants, and a pass expiring takes its minutes with it, with no sweep job in the middle. The plan
called for a sweep — "the sweep must zero the balance when its source pass or subscription period
ends, or a pass's voice minutes outlive the pass" — and a lazy re-derivation is strictly better than
one: there is no job to fail, and no interval between a pass ending and a sweep noticing. It also
matches how the rest of this domain already behaves, since `window_state` rolls the window over on
read and `_subscription_lapsed` expires a subscription on read.

`voiceSecondsPurchased` is **bought** and never reset. A learner who pays $1.49 for 30 minutes owns
them, and a period boundary that swallowed them would be a refund request with a good argument
behind it. Spending draws granted seconds first, because the granted ones are the ones that expire.

`voiceAllowanceSourceId` is what the granted balance was granted by — `"subscription:{period_end}"`,
`"pass:{pass_id}"`, or null for a tier with no voice at all. It exists so that "has this already been
granted?" is answerable without a second table, and so that a renewal is idempotent within a period.

Seconds rather than minutes because the billing loop measures seconds — a 2-second tick against a
minute-denominated counter would either round every tick up to a minute or round it down to nothing.
No data to carry forward: nobody has a voice balance today because voice is drawn from the unit
window, and there are no paying learners to grandfather.
"""

import sqlalchemy as sa

from alembic import op

revision = "068_voice_balance"
down_revision = "067_usage_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "User",
        sa.Column(
            "voiceSecondsRemaining",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "User",
        sa.Column(
            "voiceSecondsPurchased",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Nullable, and null is meaningful: it says no voice allowance has ever been granted to this
    # learner. Every FREE learner stays null forever, which is correct — Free gets no voice at all,
    # so there is nothing for a source to identify.
    op.add_column(
        "User",
        sa.Column("voiceAllowanceSourceId", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("User", "voiceAllowanceSourceId")
    op.drop_column("User", "voiceSecondsPurchased")
    op.drop_column("User", "voiceSecondsRemaining")
