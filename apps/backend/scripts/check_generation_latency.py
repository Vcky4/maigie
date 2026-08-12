"""How long does starting a practice session actually take?

Decision H fixed quiz start as synchronous **until p95 start latency exceeds 10s**,
and Phase 4e deferred a real staged progress bar to the same reading. The figure was
cited three times in the plan and never read, because it existed only as a log field.
Migration `018` persists it on `QuizSession.generationMs`; this reports it.

Coverage is printed alongside the percentiles and matters as much as they do: rows
that predate `018` are `NULL`, not `0`, and a percentile over a handful of rows is
not a p95 of anything. It says so rather than presenting three samples as a verdict.

Read-only.

    poetry run python scripts/check_generation_latency.py
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Decision H's threshold, in milliseconds.
P95_TRIGGER_MS = 10_000
# Below this, a percentile is a description of a handful of rows and not a signal.
MIN_SAMPLES_FOR_A_VERDICT = 20


def percentile(values: list[int], fraction: float) -> int:
    """Nearest-rank percentile: `ceil(p x N)`, clamped, no interpolation.

    Every result is a duration that was actually observed. An interpolating
    percentile would answer with a number nobody measured, and this number decides
    whether an architecture changes.

    `ceil` rather than `round(p * N + 0.5)`, which overshoots on an exact rank —
    at p95 over 20 samples that returned the maximum instead of the 19th value,
    reporting the worst start on record as the 95th percentile.
    """
    if not values:
        return 0
    ordered = sorted(values)
    rank = math.ceil(fraction * len(ordered))
    index = min(len(ordered) - 1, max(0, rank - 1))
    return ordered[index]


async def main() -> None:
    from sqlalchemy import select

    from src.domains.personal_learning.db_models import QuizSession
    from src.shared.database.session import connect_db, disconnect_db, get_session_factory

    await connect_db()
    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        QuizSession.mode,
                        QuizSession.status,
                        QuizSession.generation_ms,
                        QuizSession.total_questions,
                    )
                )
            ).all()

        total = len(rows)
        timed = [row for row in rows if row.generation_ms is not None]
        durations = [int(row.generation_ms) for row in timed]

        print(f"quiz sessions                   : {total}")
        print(f"  with a persisted duration     : {len(timed)}")
        print(f"  predating migration 018       : {total - len(timed)}  (NULL, not zero)")
        print()

        if not durations:
            print(
                "No timings recorded yet. Migration `018` is applied but no session has\n"
                "been started since, so there is nothing to read. Start a few sessions\n"
                "and run this again — that is the whole point of persisting the value."
            )
            return

        print(f"{'percentile':<12}{'ms':>9}")
        print("-" * 21)
        for label, fraction in (("p50", 0.50), ("p75", 0.75), ("p90", 0.90), ("p95", 0.95)):
            print(f"{label:<12}{percentile(durations, fraction):>9}")
        print(f"{'max':<12}{max(durations):>9}")
        print(f"{'mean':<12}{sum(durations) // len(durations):>9}")
        print()

        by_mode: dict[str, list[int]] = defaultdict(list)
        for row in timed:
            by_mode[row.mode or "?"].append(int(row.generation_ms))

        print(f"{'mode':<18}{'n':>4}{'p50':>8}{'p95':>8}")
        print("-" * 38)
        for mode, values in sorted(by_mode.items(), key=lambda kv: -percentile(kv[1], 0.95)):
            print(
                f"{mode:<18}{len(values):>4}"
                f"{percentile(values, 0.50):>8}{percentile(values, 0.95):>8}"
            )
        print()

        failed = [int(row.generation_ms) for row in timed if row.status == "FAILED"]
        if failed:
            # A start that spent 40s and produced nothing is the reading that
            # matters most, which is why the failure path records it too.
            print(
                f"failed starts                   : {len(failed)}  "
                f"p50 {percentile(failed, 0.50)}ms, max {max(failed)}ms"
            )
            print()

        p95 = percentile(durations, 0.95)
        if len(durations) < MIN_SAMPLES_FOR_A_VERDICT:
            print(
                f"NOT ENOUGH DATA: {len(durations)} timed session(s). A p95 over fewer than\n"
                f"{MIN_SAMPLES_FOR_A_VERDICT} samples describes those samples, not the surface. "
                f"The figure above\nis {p95}ms; treat it as an observation, not as Decision H's trigger."
            )
        elif p95 > P95_TRIGGER_MS:
            print(
                f"DECISION H TRIGGERED: p95 is {p95}ms, above the {P95_TRIGGER_MS}ms threshold.\n"
                "Quiz start should stop being synchronous. The `GENERATING` status already\n"
                "exists for queue-and-poll, and a real staged progress bar becomes possible\n"
                "for the first time — the client cannot observe stages of a POST that does\n"
                "not return until it is done."
            )
        else:
            print(
                f"DECISION H HOLDS: p95 is {p95}ms, within the {P95_TRIGGER_MS}ms threshold.\n"
                "Synchronous start stays, and the wait screen stays honest about not knowing\n"
                "which stage it is in."
            )
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
