"""Does the read-only session path actually save round trips?

`_read_session` skips the transaction that `_use_session` opens and commits for
every call including read-only ones. This times the same query through both paths
against the real database, because the whole justification for converting read
methods is a saving that has to be observed rather than reasoned about.

Read-only.

    poetry run python scripts/measure_read_session.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROUNDS = 6


async def _time(label: str, run) -> float:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        await run()
        samples.append((time.perf_counter() - started) * 1000)
    median = statistics.median(samples)
    print(f"{label:<34}: {median:7.0f} ms (median of {ROUNDS})")
    return median


async def main() -> None:
    from sqlalchemy import func, select

    from src.domains.personal_learning.db_models import PrepTopic
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.shared.database.session import connect_db, disconnect_db

    await connect_db()
    try:
        stmt = select(func.count()).select_from(PrepTopic).where(PrepTopic.prep_id == "nope")

        async def transactional() -> None:
            async with repo._use_session(None) as s:
                await s.execute(stmt)

        async def read_only() -> None:
            async with repo._read_session(None) as s:
                await s.execute(stmt)

        # Warm the pool so connection setup is not counted as query time.
        await transactional()

        before = await _time("_use_session (transaction)", transactional)
        after = await _time("_read_session (autocommit)", read_only)

        print()
        if after < before:
            saved = before - after
            print(
                f"saving per read call              : {saved:7.0f} ms "
                f"({saved / before * 100:.0f}%)"
            )
            print(f"a 4-read endpoint saves           : {4 * saved:7.0f} ms")
        else:
            print(
                "No saving observed. Do not convert read methods on this evidence —\n"
                "the transaction was not the cost here."
            )

        # The correctness half: the same query must return the same answer, and a
        # caller-supplied session must be respected rather than reconfigured.
        async with repo._read_session(None) as s:
            standalone = (await s.execute(stmt)).scalar_one()
        async with repo.unit_of_work() as uow:
            async with repo._read_session(uow) as s:
                joined = (await s.execute(stmt)).scalar_one()
        print()
        print(f"same result both ways             : {standalone == joined} ({standalone})")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
