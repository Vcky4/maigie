"""Report the narrative state of every stored reflection.

Phase 6 recorded that the composer had never run against a live model, so every row read
`narrative: null` and the paid half of `/reflections/:id` was unexercised. This says exactly how many
rows are in that state, and how many carry each narrative section, before and after regeneration.

Read-only.
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402

from src.domains.personal_learning.db_models import Reflection  # noqa: E402
from src.shared.database.session import connect_db, get_session_factory  # noqa: E402

SECTIONS = (
    "opening",
    "highlights",
    "signals",
    "subjects",
    "rhythm",
    "patterns",
    "theme",
    "changeLabel",
    "closing",
)


def _section_present(narrative: dict, key: str) -> bool:
    value = narrative.get(key)
    if value is None:
        return False
    if isinstance(value, (list, str, dict)):
        if isinstance(value, dict) and key == "patterns":
            # `patterns` is an object with two nullable members; present means at least one is set.
            return any(value.get(side) for side in ("keep", "watch"))
        return bool(value)
    return True


async def main() -> None:
    await connect_db()
    session_maker = get_session_factory()

    async with session_maker() as session:
        rows = (await session.execute(select(Reflection))).scalars().all()

    print(f"reflections: {len(rows)}")
    if not rows:
        return

    depths = Counter(row.depth for row in rows)
    types = Counter(row.type for row in rows)
    print(f"depth: {dict(depths)}")
    print(f"type: {dict(types)}")

    narrated = [row for row in rows if row.narrative]
    print(f"narrative present: {len(narrated)} / {len(rows)}")

    section_counts: Counter[str] = Counter()
    for row in narrated:
        narrative = row.narrative if isinstance(row.narrative, dict) else {}
        for key in SECTIONS:
            if _section_present(narrative, key):
                section_counts[key] += 1

    print("sections (of narrated rows):")
    for key in SECTIONS:
        print(f"  {key:<12} {section_counts[key]}")

    with_actions = sum(1 for row in rows if row.recommendations)
    print(f"recommendations non-empty: {with_actions} / {len(rows)}")

    metrics_all_null = 0
    for row in rows:
        metrics = row.metrics if isinstance(row.metrics, dict) else {}
        if all(value is None for value in metrics.values()) or not metrics:
            metrics_all_null += 1
    print(f"metrics entirely unmeasured: {metrics_all_null} / {len(rows)}")

    print("\nper row:")
    for row in rows:
        narrative = row.narrative if isinstance(row.narrative, dict) else None
        present = (
            ",".join(key for key in SECTIONS if narrative and _section_present(narrative, key))
            or "-"
        )
        print(
            f"  {row.id[:8]} {row.type:<7} {row.depth:<8} "
            f"{row.period_start.date()}..{row.period_end.date()} "
            f"actions={len(row.recommendations or [])} sections={present}"
        )

    print("\n" + json.dumps({"total": len(rows), "narrated": len(narrated)}))


if __name__ == "__main__":
    asyncio.run(main())
