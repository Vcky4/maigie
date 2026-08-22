"""Run the **deep** narrative path against a live model, for a real learner's real figures.

Phase 6 carried this forward as the one unverified thing on the surface: every stored reflection is
`depth: standard`, so `compose(deep=True, ...)` — the half that produces `signals`, `patterns`,
per-subject `insight`, `theme`, `changeLabel` and `closing` — had never run outside a unit test with a
stubbed reply. The client renders those five sections, so "it type-checks" was the only evidence they
worked.

This calls the real `_compose_narrative` with `deep=True`, which means the real prompt, the real
`generate_content_json`, and the real measured skeleton. It **does not write** and it **does not
change anyone's tier**: it takes the period and the metrics from an existing reflection and discards
the result after reporting it. Nothing here grants Plus to a learner who has not paid for it.

Usage::

    python scripts/debug/exercise_deep_narrative.py                     # first reflection with metrics
    python scripts/debug/exercise_deep_narrative.py --reflection-id abc
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402

from src.domains.personal_learning import models  # noqa: E402
from src.domains.personal_learning.db_models import Reflection  # noqa: E402
from src.domains.personal_learning.services import (  # noqa: E402
    reflection_metrics,
    reflection_service,
)
from src.shared.database.session import connect_db, get_session_factory  # noqa: E402


def _describe(value: object, width: int = 96) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= width else f"{text[: width - 1]}…"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reflection-id", default=None)
    args = parser.parse_args()

    await connect_db()
    session_maker = get_session_factory()

    async with session_maker() as session:
        stmt = select(Reflection)
        if args.reflection_id:
            stmt = stmt.where(Reflection.id == args.reflection_id)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        print("no reflections found")
        return

    # Prefer a row that actually measured something: a deep narrative over an empty week would
    # demonstrate only that the model can be handed nothing.
    def measured_count(row: Reflection) -> int:
        metrics = row.metrics if isinstance(row.metrics, dict) else {}
        return sum(1 for value in metrics.values() if value is not None)

    target = max(rows, key=measured_count)
    print(f"reflection {target.id}")
    print(f"user       {target.user_id}")
    print(f"period     {target.period_start.date()} .. {target.period_end.date()}")
    print(f"stored depth {target.depth} (unchanged by this script)")
    print(f"measured metrics: {measured_count(target)}")

    metrics = await reflection_metrics.compute_metrics(
        user_id=target.user_id,
        period_start=target.period_start,
        period_end=target.period_end,
    )

    for label, deep in (("FREE (deep=False)", False), ("PLUS (deep=True)", True)):
        narrative, actions = await reflection_service._compose_narrative(
            user_id=target.user_id,
            type_=models.ReflectionType(target.type),
            period_start=target.period_start,
            period_end=target.period_end,
            deep=deep,
            metrics=metrics,
            summary=target.summary,
        )

        print(f"\n===== {label} =====")
        if narrative is None:
            print("  narrative: None (measured skeleton unavailable)")
            continue

        print(f"  opening      {len(narrative.opening or [])} paragraph(s)")
        for paragraph in narrative.opening or []:
            print(f"    - {_describe(paragraph)}")
        print(f"  highlights   {len(narrative.highlights or [])}")
        print(f"  signals      {len(narrative.signals or [])}")
        for signal in narrative.signals or []:
            print(
                f"    - {signal.id}: value={signal.value}{signal.unit or ''} "
                f"description={_describe(signal.description, 60)} "
                f"evidence={_describe(signal.evidence, 40)}"
            )
        print(f"  subjects     {len(narrative.subjects or [])}")
        for subject in narrative.subjects or []:
            print(
                f"    - {subject.title}: mastery={subject.mastery} change={subject.change} "
                f"insight={_describe(subject.insight, 60)}"
            )
        keep = narrative.patterns.keep if narrative.patterns else None
        watch = narrative.patterns.watch if narrative.patterns else None
        print(f"  patterns.keep  {_describe(keep.title if keep else None, 60)}")
        if keep:
            print(f"    body: {_describe(keep.body)}")
        print(f"  patterns.watch {_describe(watch.title if watch else None, 60)}")
        if watch:
            print(f"    body: {_describe(watch.body)}")
        print(f"  theme        {narrative.theme!r}")
        print(f"  changeLabel  {narrative.change_label!r}")
        print(f"  closing      {_describe(narrative.closing)}")
        print(f"  rhythm       {len(narrative.rhythm or [])} day(s)")
        print(f"  actions      {len(actions)}")
        for action in actions:
            target_kind = action.target.kind if action.target else None
            entity = action.target.entity_id if action.target else None
            print(f"    - {action.title} [{action.label}] -> {target_kind} {entity}")

    print("\nNothing was written. No tier was changed.")


if __name__ == "__main__":
    asyncio.run(main())
