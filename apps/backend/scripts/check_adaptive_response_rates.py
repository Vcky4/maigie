"""Do learners answer what the adaptive goal lifecycle asks them?

Read-only. **This is the number phase 7 is waiting on.**

The programme rests on two assumptions that have never been observed: that a learner will say how an exam
went, and that a learner will answer a nudge about a goal falling behind. §6.2's readiness calibration is
worthless below some response rate nobody can predict, and phase 8 depends on nudge answers existing at all.
Nothing new is stored to answer this — every figure comes from columns that were already there.

**Read `never asked` before any rate.** "Asked and ignored" and "never asked" are different failures with
opposite remedies, and a rate whose denominator is candidates rather than asks hides the second inside the
first. This database has preparations the old date-based sweep closed before anyone was ever asked; counting
those as unanswered would report a response rate for a question that never left the building.

    python scripts/db_direct.py python scripts/check_adaptive_response_rates.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.domains.progress.services import adaptive_response_metrics as arm  # noqa: E402


def _pct(value: float | None) -> str:
    """A rate as a percentage, or the reason there isn't one.

    "not asked yet" rather than "0%", because those are the two readings this whole script exists to keep
    apart and the summary line is where they would be confused.
    """
    return "not asked yet" if value is None else f"{value * 100:.0f}%"


def _days(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} days"


def _report(title: str, funnel: arm.Funnel) -> None:
    print(f"--- {title} ---")
    print(f"  candidates       {funnel.candidates}")
    print(f"  asked            {funnel.asked}")
    if funnel.never_asked:
        print(f"  NEVER ASKED      {funnel.never_asked}   <-- not a response-rate problem")
    print(f"  answered         {funnel.answered}")
    print(f"  declined         {funnel.declined}")
    print(f"  no reply         {funnel.silent}")
    print(f"  response rate    {_pct(funnel.response_rate)}   (answered / asked)")
    print(f"  engagement rate  {_pct(funnel.engagement_rate)}   (answered or declined / asked)")
    print(f"  median reply in  {_days(funnel.median_days_to_answer)}")
    print()


async def main() -> None:
    # The service reads through the shared session factory, which the app normally initialises at startup.
    from src.shared.database.session import ensure_db

    await ensure_db()
    # **After** `ensure_db`, not at import. The shared engine is built with `echo` on, and SQLAlchemy sets
    # that logger's level itself when the engine is created — so quietening it beforehand is overwritten and
    # a fifteen-line report arrives buried under its own SQL.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    review_rows = await arm.load_review_rows()
    nudge_rows = await arm.load_nudge_rows()

    _report("The post-exam review", arm.review_funnel(review_rows))

    nudges = arm.nudge_funnel(nudge_rows)
    _report("The goal nudge", nudges)

    if nudge_rows:
        # The cut phase 8 needs: a single overall rate cannot say which rung is worth keeping.
        print("--- The goal nudge, by rung ---")
        for action, funnel in arm.split_by(nudge_rows, "action").items():
            print(
                f"  {action:<18} asked {funnel.asked:>4}"
                f"  answered {funnel.answered:>4}"
                f"  rate {_pct(funnel.response_rate)}"
            )
        print()
        print("--- The goal nudge, by trigger ---")
        for trigger, funnel in arm.split_by(nudge_rows, "trigger").items():
            print(
                f"  {trigger:<18} asked {funnel.asked:>4}"
                f"  answered {funnel.answered:>4}"
                f"  rate {_pct(funnel.response_rate)}"
            )
        print()

        breakdown = arm.response_breakdown(nudge_rows)
        print("--- What they said ---")
        if not breakdown:
            print("  nothing yet")
        for response, count in breakdown.items():
            # `already_done` is the one to watch: it says the *measurement* was wrong rather than the
            # learner, and it is the only signal in the system that can say so.
            flag = "   <-- the measurement missed real work" if response == "already_done" else ""
            print(f"  {response:<14} {count}{flag}")
        print()

    review = arm.review_funnel(review_rows)
    print("--- Is phase 7 worth building yet? ---")
    print(f"  recorded exam outcomes: {review.answered}")
    if review.answered == 0:
        print("  No. Calibration would be a query over an empty table.")
        if review.never_asked and not review.asked:
            print(
                "  And the rate is not the blocker: nothing has been asked. Check that the review sweep\n"
                "  runs, and that the deployment running it is current."
            )
    else:
        print(
            "  Maybe. Compare against the readiness figures stored on each outcome — "
            "`readinessPercent`\n  beside `experienceRating` is the pair §6.2 scores."
        )


if __name__ == "__main__":
    asyncio.run(main())
