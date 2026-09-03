"""One row per metered operation, so §6.5's estimates can be checked rather than trusted.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.5 lists 27 operations with a unit cost each, and Decision P draws
its model-quality threshold at 500 of those units. Both are **estimates**, and until now nothing in
the database could contradict them: `record_units` advances two aggregate counters and logs the
operation label, so the total spend is known and its composition is not. `LlmCostRecord` is no help —
it has no operation column and is written only on the chat path, by `cost_tracker`, alongside a
`ChatGenerationAttempt` foreign key.

So the paywall's threshold is currently enforced against numbers no query can verify. That is the gap
this closes: one row per charge, carrying the operation, the units and the model that produced them.

**Recorded as an addition to Decision L rather than an unfinished part of it.** Decision L asked for
cost to be *measured rather than tabulated*, and it is — `units_for_tokens` prices every generation
from real token counts. It did not ask for per-operation persistence. This is a new requirement that
follows from Decision P needing its threshold checkable, not a requirement that was missed.

**It is a write on every generation, and that is the cost of the answer.** One insert beside an update
that already happens. Two mitigations rather than none: the insert is separate from the counter update,
so a failed row can never cost a learner their accounting; and `units` is stored without the token
counts it was derived from, because `units_for_tokens` has already applied the rate and keeping both
invites the two disagreeing. The model is kept because under Decision P the same operation costs
different units on different tiers, so "which model ran" is part of the answer rather than a duplicate
of it.

No `userTier` column, deliberately. The tier is derivable from the model under Decision P, and a
denormalised tier would be a second opinion about entitlement — which is exactly what Decision B
exists to prevent.
"""

import sqlalchemy as sa

from alembic import op

revision = "070_usage_events"
down_revision = "069_proactive_sub_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "UsageEvent",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("userId", sa.String(), nullable=False),
        # The `operation` label callers pass to `llm_resilient`. Not a foreign key and not an enum:
        # a new operation must be able to start recording itself without a migration, and an enum
        # would make adding a labelled call site a schema change.
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        # Nullable because a charge can be recorded without one — a provider reply that carried usage
        # but no model name still costs money, and a null here is more honest than a guess.
        sa.Column("model", sa.String(), nullable=True),
        # Decision M rule 1's category tag, carried here as well as on the month counter so the
        # proactive share can be attributed per operation rather than only in aggregate.
        sa.Column("proactive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # No FK to `User`. This is an accounting record, and a deleted learner's spend still happened
        # — a cascade would quietly rewrite history to make the cost model look better than it was.
    )
    # The aggregation this table exists for: units by operation over a period.
    op.create_index("UsageEvent_operation_createdAt_idx", "UsageEvent", ["operation", "createdAt"])
    # Per-learner, for the distribution questions §6.7 leaves open — typical consumption, and how
    # much of a learner's month goes to work they did not ask for.
    op.create_index("UsageEvent_userId_createdAt_idx", "UsageEvent", ["userId", "createdAt"])


def downgrade() -> None:
    op.drop_index("UsageEvent_userId_createdAt_idx", table_name="UsageEvent")
    op.drop_index("UsageEvent_operation_createdAt_idx", table_name="UsageEvent")
    op.drop_table("UsageEvent")
