"""Bug Hunt: the pass premium becomes a duration bonus rather than a price discount.

`passUpliftPercent` meant *a discount off the pass price*: at 25, a ₦1,500 pass cost ₦1,125 of balance.
That is the wrong shape of reward, and the reason is arithmetic rather than taste.

A tester with ₦1,500 who wants a week of Plus spends ₦1,125 and **keeps ₦375**. We hand over the pass,
which costs us compute, and we still owe ₦375, which costs us naira. Every discounted redemption converts
a compute cost into a cash cost, and it also leaves a stub below the ₦1,000 withdrawal minimum that reads
to the tester as money they earned and cannot reach.

Some premium is necessary: cash is fungible and a pass is not, so at a 1:1 rate a rational tester always
takes cash and the programme pays real money on every accepted finding. But the premium belongs in
**duration**. Charge the full catalogue price and grant more than the catalogue gives: ₦1,500 buys 9 days
instead of 7. The balance is fully extinguished, the tester gets more than anyone paying Paystack, and the
extra days cost compute rather than cash.

**Renamed rather than added-and-dropped.** The magnitude is unchanged (25 stays 25) and only its meaning
moves, from "25% off the price" to "25% more pass". Normally this domain refuses to reinterpret a column a
closed season claims it honoured — that is why the reward matrix lives on the row — but nothing is being
misrepresented here: no season has closed, no pass has been redeemed, and `BugHuntLedgerEntry` holds zero
`pass_redemption` rows on any environment. Verified before writing this, and the check is repeatable with
`scripts/check_bug_hunt_season.py`. A rename keeps one column meaning one thing instead of leaving a dead
one behind for somebody to read as still authoritative.

The ceiling moves from 90 to 100. Under the old meaning 90 was a hard limit because 100% off is a free
pass; under the new one 100% means a doubled pass, which is generous but not incoherent. Above that the
allowance stops resembling the product being tested.

Revision ID: 084_bug_hunt_pass_bonus
Revises: 083_bug_hunt
"""

from alembic import op

revision = "084_bug_hunt_pass_bonus"
down_revision = "083_bug_hunt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the constraint before the rename: it names the old column, and Postgres would carry the
    # expression across silently, leaving a check called `uplift_check` bounding a bonus at 90.
    op.drop_constraint("BugHuntProgram_uplift_check", "BugHuntProgram", type_="check")
    op.alter_column("BugHuntProgram", "passUpliftPercent", new_column_name="passBonusPercent")
    op.create_check_constraint(
        "BugHuntProgram_bonus_check",
        "BugHuntProgram",
        '"passBonusPercent" >= 0 AND "passBonusPercent" <= 100',
    )


def downgrade() -> None:
    op.drop_constraint("BugHuntProgram_bonus_check", "BugHuntProgram", type_="check")
    op.alter_column("BugHuntProgram", "passBonusPercent", new_column_name="passUpliftPercent")
    # Back to 90, because under the restored meaning 100 would be a free pass. A season sitting at
    # 91–100 when this runs would violate it, which is the correct outcome: the downgrade should refuse
    # rather than quietly clamp somebody's configuration into a different offer.
    op.create_check_constraint(
        "BugHuntProgram_uplift_check",
        "BugHuntProgram",
        '"passUpliftPercent" >= 0 AND "passUpliftPercent" <= 90',
    )
