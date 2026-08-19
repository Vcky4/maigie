"""Re-measure and re-narrate reflections that were written before metrics were measured.

Until migration 038, `reflection_service` asked a language model for its own metrics while
showing it only the behaviour profile. The model produced counts for data it had never seen —
`topics_studied`, `sessions_completed`, `total_minutes` and the rest — and they were persisted
as though measured.

Migration 038 dropped the three JSON columns that held those counts, which is why every
pre-existing reflection now reads `metrics = {}`. That is honest: for those rows we genuinely
do not know what the learner did. But it does not finish the job, because **the invented
figures are also in the summary prose** — "maintaining a 4-day study streak", "averaging 4.6
minutes", "completed 4 study sessions". A dropped column cannot reach a sentence, and the
narrative is the most prominent thing the reflection page renders.

This script regenerates each affected row against **its own period**, so the metrics are
measured from the rows that actually exist for that week and the prose is written from those
figures.

Why a script rather than part of migration 038: it makes one LLM call per reflection, so it is
slow, costs quota, and can partially fail. A migration offers no dry run and no way to do half
of it and resume.

Why not simply `POST /reflections/generate` per learner: that targets the week ending now. It
would leave every legacy row exactly as it is and add a new one beside it.

**Idempotent, and safe to re-run.** `regenerate_reflection` upserts on
`(userId, type, periodStart)`, all three taken from the existing row, so a row is updated in
place and never duplicated. `openedAt` is preserved by the repository.

A learner's historical metrics may legitimately come back all-null: if nothing was ever
recorded for that week, that is the true answer, and it is the answer the old code refused to
give.

Usage::

    python scripts/regenerate_legacy_reflections.py                  # dry run, report only
    python scripts/regenerate_legacy_reflections.py --apply          # regenerate
    python scripts/regenerate_legacy_reflections.py --apply --user-id abc123
    python scripts/regenerate_legacy_reflections.py --apply --all    # include clean rows
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domains.personal_learning.db_models import Reflection  # noqa: E402
from src.domains.personal_learning.services import reflection_service  # noqa: E402
from src.shared.database.session import (  # noqa: E402
    connect_db,
    disconnect_db,
    get_session_factory,
)

#: A summary containing a digit is a summary asserting a figure. Crude on purpose: the point is
#: to catch every row that states a number, and a false positive costs one regeneration while a
#: false negative leaves a fabricated measurement on screen.
_HAS_FIGURE = re.compile(r"\d")


#: The placeholder `reflection_service` writes when the narrative step fails. Metrics are
#: unaffected on that path — they are measured before the model is called — so a row carrying
#: this text needs only its prose retried.
_FELL_BACK = "could not be generated this time"


async def _load(user_id: str | None) -> list[tuple[str, str, str, bool]]:
    """Every reflection, as `(id, userId, summary, states_a_figure)`."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Reflection.id, Reflection.user_id, Reflection.summary, Reflection.metrics)
        if user_id:
            stmt = stmt.where(Reflection.user_id == user_id)
        rows = (await session.execute(stmt.order_by(Reflection.period_end))).all()

    return [
        (row_id, owner, summary or "", bool(_HAS_FIGURE.search(summary or "")))
        for row_id, owner, summary, _metrics in rows
    ]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write; otherwise report only")
    parser.add_argument("--user-id", default=None, help="limit to one learner")
    parser.add_argument(
        "--all",
        action="store_true",
        help="regenerate every reflection, not only those whose summary states a figure",
    )
    parser.add_argument(
        "--only-fallback",
        action="store_true",
        help=(
            "retry only rows whose narrative step failed. Safe to repeat: it never re-rolls a "
            "summary that was written successfully, so retrying cannot make the set worse."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help=(
            "seconds to wait between calls. Ten back-to-back requests drew four empty replies "
            "from the provider on the first run; pacing them costs seconds and buys narratives."
        ),
    )
    args = parser.parse_args()

    await connect_db()
    try:
        rows = await _load(args.user_id)
        if args.only_fallback:
            targets = [row for row in rows if _FELL_BACK in row[2]]
        elif args.all:
            targets = rows
        else:
            targets = [row for row in rows if row[3]]

        print(f"reflections found:          {len(rows)}")
        print(f"summaries stating a figure: {sum(1 for row in rows if row[3])}")
        print(f"narratives that fell back:  {sum(1 for row in rows if _FELL_BACK in row[2])}")
        print(f"to regenerate:              {len(targets)}")
        print()

        if not args.apply:
            for row_id, owner, summary, _ in targets:
                print(f"  would regenerate {row_id} (user {owner[:8]}…): {summary[:70]}…")
            print()
            print("Dry run. Re-run with --apply to write.")
            return 0

        regenerated = 0
        failed: list[tuple[str, str]] = []
        for index, (row_id, owner, _summary, _) in enumerate(targets):
            if index and args.delay:
                await asyncio.sleep(args.delay)
            try:
                result = await reflection_service.regenerate_reflection(
                    user_id=owner, reflection_id=row_id
                )
                measured = sum(1 for value in (result.metrics or {}).values() if value is not None)
                narrated = "could not be generated" not in (result.summary or "")
                print(
                    f"  {row_id}: {measured} metrics measured, narrative: "
                    f"{'written' if narrated else 'fell back'}"
                )
                regenerated += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                failed.append((row_id, f"{type(exc).__name__}: {exc}"))
                print(f"  {row_id}: FAILED — {type(exc).__name__}: {exc}")

        print()
        print(f"regenerated: {regenerated}")
        print(f"failed:      {len(failed)}")
        for row_id, reason in failed:
            print(f"  {row_id}: {reason}")
        return 1 if failed else 0
    finally:
        await disconnect_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
