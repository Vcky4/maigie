"""Measure what a database round trip costs from here, and where it goes.

Written while fixing "check answer takes long". Sequential round trips only matter
if a round trip is expensive, so this measures rather than assumes — and then
attributes the cost, so any decision about engine configuration is made from a
reading.

Read-only. Times the same harmless lookup sequentially and concurrently, then
breaks a single call down into its round trips.

    poetry run python scripts/measure_rtt.py                      # as the app connects
    poetry run python scripts/db_direct.py python scripts/measure_rtt.py   # direct host
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


def _median(values: list[float]) -> float:
    return statistics.median(values)


async def measure_repository_calls() -> float:
    """Time a repository call as the application makes it. Returns the median ms."""
    from src.domains.personal_learning.repository import personal_learning_repo as repo
    from src.shared.database.session import connect_db, disconnect_db

    await connect_db()
    try:
        # Warm the pool, so connection setup is not counted as query time.
        await repo.count_prep_topics("warmup")

        sequential: list[float] = []
        for _ in range(ROUNDS):
            started = time.perf_counter()
            await repo.count_prep_topics("nonexistent")
            sequential.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        await asyncio.gather(*(repo.count_prep_topics("nonexistent") for _ in range(ROUNDS)))
        concurrent_total = (time.perf_counter() - started) * 1000

        per_call = _median(sequential)
        print(f"one repository call              : {per_call:.0f} ms (median of {ROUNDS})")
        print(f"{ROUNDS} sequential calls              : {sum(sequential):.0f} ms")
        print(f"{ROUNDS} concurrent calls              : {concurrent_total:.0f} ms")
        return per_call
    finally:
        await disconnect_db()


async def measure_round_trips() -> None:
    """Attribute a single call's cost to its round trips."""
    import asyncpg

    from src.config import get_settings

    raw = (get_settings().DATABASE_URL or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    for param in ("?pgbouncer=true", "&pgbouncer=true"):
        raw = raw.replace(param, "")

    conn = await asyncpg.connect(raw, statement_cache_size=0)
    try:
        # One statement on an already-open connection: the network round trip, and
        # the floor for anything the application does.
        rtt: list[float] = []
        for _ in range(ROUNDS):
            started = time.perf_counter()
            await conn.fetchval("SELECT 1")
            rtt.append((time.perf_counter() - started) * 1000)

        # An explicit transaction, as `_use_session` opens for every call including
        # read-only ones: COMMIT is a round trip of its own.
        txn: list[float] = []
        for _ in range(ROUNDS):
            started = time.perf_counter()
            async with conn.transaction():
                await conn.fetchval("SELECT 1")
            txn.append((time.perf_counter() - started) * 1000)

        print(f"\nraw round trip (SELECT 1)        : {_median(rtt):.0f} ms")
        print(f"same, inside a transaction       : {_median(txn):.0f} ms")
        print("\nwhere a repository call's time goes:")
        print("  1. pool_pre_ping SELECT 1  (on every connection checkout)")
        print("  2. the query")
        print("  3. COMMIT                  (even for a read-only SELECT)")
        print(f"  ~3 x {_median(rtt):.0f} ms")
    finally:
        await conn.close()


async def main() -> None:
    per_call = await measure_repository_calls()
    await measure_round_trips()

    print("\nsubmit_answer, in repository calls:")
    print(f"  9 sequential (before)          ~ {9 * per_call:.0f} ms")
    print(f"  3 waves of 4, 1, 3 (after)     ~ {3 * per_call:.0f} ms")
    print("\nplus the client session refetch that no longer happens:")
    print(f"  GET /quizzes/{{id}}: 4 calls      ~ {4 * per_call:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
