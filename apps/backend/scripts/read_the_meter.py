"""Read the meter, and replace §6.7's guesses with measurements.

`MAIGIE_PLUS_COMMERCIAL_PLAN.md` §6.7 opens by saying its assumptions "are all guesses and are the
first thing to replace with measurement", and §6.11 ends by saying the mix "is a hypothesis for Phase
3 to instrument, not a forecast". The instrumentation has been live since Phase 3. **This is the
command that closes that loop**, and it exists as a script rather than as a note for the same reason
`count_legacy_commercial_state.py` does: a number nobody can re-derive becomes a number somebody
remembers wrongly.

Four things the plan currently guesses, and what each would settle:

**Free COGS per active learner.** §6.7 models $0.08/month after the fixes and $940 before them, and
free inference is the largest single line item in both markets. It is also the figure the whole
Nigerian case rests on: §6.8 had two contradictory values for it, and at the higher one the launch
market's contribution went from +$518 to +$58.

**Consumption against allowance.** Every "typical COGS" column is asserted at roughly half the
ceiling. If learners draw closer to their caps than that, the margin tables are optimistic in a way
no rate-card correction would show, and the 1.5× overshoot row in §6.11 is the sensitivity that
matters.

**Where the units actually go.** §6.5 estimated 27 operations from `max_tokens` and a rate card. Some
will be wrong, and the ones that are wrong in the expensive direction are where the next cost work
belongs — rather than where the plan currently guesses it belongs.

**Proactive share.** Decision M caps background AI at 20% of the month. Whether learners come near
that is unknown, and if the real figure is 2% the sub-budget is a bound nobody reaches.

**Strictly read-only.** Aggregates over `User`'s usage columns and `LlmCostRecord`. No writes, no DDL,
no long transaction. Safe against production, which is the only place the answer exists.

**What it cannot tell you: the payer rate.** There are no payers, so 8% and 6% stay guesses until
Phase 5 ships a checkout. Every revenue figure in §6.7 and §6.11 depends on that number and none of
it is measurable yet — which is worth saying plainly, because this script measuring *costs* precisely
could otherwise read as the model being validated.

Usage:
    python scripts/read_the_meter.py
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

#: One unit is $0.0001 of measured COGS (§6.2), so 10 000 units is $1.00.
USD_PER_UNIT = 0.0001


async def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.")
        return 1
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # The same connect args the app uses (`shared/database/session.py`). Production sits behind
    # pgbouncer in transaction mode, which does not support prepared statements — without these the
    # second query fails with `DuplicatePreparedStatementError`, which reads as a database fault
    # rather than as a pooler configuration.
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    )
    try:
        async with engine.connect() as conn:
            print("=" * 78)
            print("THE METER, AS READ")
            print("=" * 78)

            # --- Who is actually being served -------------------------------------------------
            rows = (
                (
                    await conn.execute(
                        text(
                            """
                        SELECT
                            COUNT(*) AS learners,
                            COUNT(*) FILTER (
                                WHERE "usageWindowStartedAt" >= NOW() - INTERVAL '30 days'
                            ) AS active_30d,
                            COUNT(*) FILTER (
                                WHERE "usageWindowStartedAt" >= NOW() - INTERVAL '7 days'
                            ) AS active_7d,
                            COUNT(*) FILTER (WHERE "usageWindowStartedAt" IS NULL) AS never_billable
                        FROM "User"
                        """
                        )
                    )
                )
                .mappings()
                .first()
            )

            learners = rows["learners"] or 0
            active_30d = rows["active_30d"] or 0
            print(f"\nLearners:                     {learners}")
            print(f"  billable activity in 30d:   {active_30d}")
            print(f"  billable activity in 7d:    {rows['active_7d'] or 0}")
            print(f"  never ran a billable op:    {rows['never_billable'] or 0}")
            print(
                "\n  §6.7 assumes 50% of non-paying MAU are AI-active in a month. The ratio above "
                "is the\n  measured version, and it is the single largest input to free COGS."
            )

            # --- What the month costs ---------------------------------------------------------
            usage = (
                (
                    await conn.execute(
                        text(
                            """
                            SELECT
                                COALESCE(SUM("usageMonthUnitsUsed"), 0)          AS units,
                                COALESCE(SUM("usageMonthProactiveUnitsUsed"), 0) AS proactive,
                                COALESCE(AVG(NULLIF("usageMonthUnitsUsed", 0)), 0) AS avg_active,
                                COALESCE(MAX("usageMonthUnitsUsed"), 0)          AS max_units
                            FROM "User"
                            """
                        )
                    )
                )
                .mappings()
                .first()
            )
            units = int(usage["units"] or 0)
            proactive = int(usage["proactive"] or 0)
            avg_active = float(usage["avg_active"] or 0)

            print(f"\nUnits this month, all learners: {units:,} (${units * USD_PER_UNIT:,.2f})")
            print(
                f"  per learner with any usage:   {avg_active:,.0f} units "
                f"(${avg_active * USD_PER_UNIT:,.4f}/month)"
            )
            print(
                f"  heaviest single learner:      {int(usage['max_units']):,} units "
                f"(${int(usage['max_units']) * USD_PER_UNIT:,.2f})"
            )
            print(
                "\n  Compare the per-learner figure with §6.7's $0.08 for a fixed free learner and "
                "$1.10\n  for a fixed Plus one. Those are the numbers the margin tables are built on."
            )

            share = (proactive / units * 100) if units else 0.0
            print(f"\nProactive units this month:     {proactive:,} ({share:.1f}% of all usage)")
            print(
                "  Decision M caps this at 20% of each learner's monthly backstop. A share far "
                "below\n  that means the sub-budget is a bound nobody reaches."
            )

            # --- Which model served whom ------------------------------------------------------
            #
            # `LlmCostRecord` has no per-operation column: it holds provider, model, tier and cost.
            # So this cannot answer "where did the units go" — see the closing note.
            try:
                by_model = (
                    (
                        await conn.execute(
                            text(
                                """
                                SELECT
                                    "userTier"                    AS tier,
                                    model,
                                    COUNT(*)                      AS calls,
                                    COALESCE(SUM("costUsd"), 0)   AS usd
                                FROM "LlmCostRecord"
                                WHERE "createdAt" >= NOW() - INTERVAL '30 days'
                                GROUP BY 1, 2
                                ORDER BY 4 DESC
                                LIMIT 20
                                """
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            except Exception as exc:  # pragma: no cover - table may be empty or absent
                by_model = []
                print(f"\nModel breakdown unavailable: {type(exc).__name__}: {exc}")

            if by_model:
                print("\nWhich model served whom, last 30 days:")
                print(f"  {'tier':<10} {'model':<30} {'calls':>8} {'USD':>10}")
                for row in by_model:
                    print(
                        f"  {str(row['tier'])[:10]:<10} {str(row['model'])[:30]:<30} "
                        f"{int(row['calls'] or 0):>8,} {float(row['usd'] or 0):>10.2f}"
                    )
                print(
                    "\n  This is the check on Decision P: a FREE row against `gemini-3.5-flash` "
                    "means the\n  model-quality split is not holding, which is the defect revision "
                    "6 found once already."
                )
                print(
                    "  **Read it against the fix date before raising it.** The allowlist was "
                    "narrowed on\n  2026-09-02, so a 30-day window still contains rows from before "
                    "it — on first run this\n  reported 11 FREE calls on the Plus model, all of "
                    "them 29–31 August and all pre-fix. A\n  script that cries wolf about its own "
                    "history is one people learn to ignore."
                )

            print("\n" + "=" * 78)
            print("WHAT THIS CANNOT TELL YOU")
            print("=" * 78)
            print(
                "**The payer rate.** There are no payers, so §6.7's 6% and §6.8's mix stay guesses "
                "until\nPhase 5 ships a checkout — and every revenue figure in the plan rests on "
                "them. Costs are\nnow measured; revenue is not, and the model is only half checked."
            )
            print(
                "\n**Where the units went, per operation.** `record_units` logs the `operation` "
                "label and\nadvances two aggregate counters on `User`; it writes no per-operation "
                "row. `LlmCostRecord`\nhas provider, model, tier and cost but no operation, and it "
                "is written from the chat path\nonly. So §6.5's 27 estimates cannot be checked "
                "against measurement from the database — only\nfrom logs, which are not aggregated "
                "and do not persist.\n\nThat is a real gap rather than an oversight of this "
                "script: closing it means one row per\nmetered operation, which is a table nobody "
                "has asked for yet and which would be the first\nplace to look when the next cost "
                "question is 'which surface'."
            )
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
