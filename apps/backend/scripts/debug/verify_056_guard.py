"""Prove migration 056's trigger refuses the wrong write and allows the right one.

A trigger is untestable by the unit suite — those tests use fakes and never reach Postgres. So this exercises
it directly against the database, in a **transaction that is always rolled back**, and asserts all four
behaviours the migration claims:

1. `AWAITING_REVIEW → COMPLETED` with no outcome is **refused**;
2. the same transition **with** an outcome for that sitting is allowed — the legitimate path;
3. an outcome for a *different* sitting does not satisfy it, so a postponed preparation's earlier answer
   cannot complete a later one;
4. the transitions this must not touch still work: `IN_PROGRESS → COMPLETED` (abandoning early),
   `AWAITING_REVIEW → IN_PROGRESS` (postponed), and `COMPLETED → AWAITING_REVIEW` (the repair script).

Writes nothing: every case runs inside a savepoint that is rolled back, and the outer transaction is rolled
back too.

    python scripts/db_direct.py python scripts/debug/verify_056_guard.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

#: The sitting, in the two shapes the two tables actually store.
#:
#: `ExamPrep."examDate"` is `timestamp without time zone` and `PrepOutcome."examDate"` is `timestamp with time
#: zone`. **Binding a naive datetime to the aware column silently interprets it in the client's local
#: timezone** — from a BST machine, a naive 09:00 was stored as 08:00 UTC, an hour off the preparation, and
#: this script reported the guard as broken when the guard was fine. The service never does that:
#: `record_outcome` passes `_as_utc(prep.exam_date)`, so both sides agree in production. Matched here the same
#: way, deliberately, so the probe tests the trigger rather than its own binding.
SITTING_NAIVE = datetime(2026, 8, 1, 9, 0)
SITTING_AWARE = SITTING_NAIVE.replace(tzinfo=UTC)
OTHER_SITTING_AWARE = SITTING_AWARE + timedelta(days=30)


def _url() -> str:
    raw = os.environ["DATABASE_URL"]
    return raw.replace("postgresql://", "postgresql+asyncpg://").replace(
        "postgres://", "postgresql+asyncpg://"
    )


async def _seed(conn, status: str) -> tuple[str, str]:
    """A throwaway user and preparation, inside the caller's savepoint."""
    user_id = f"t_{uuid.uuid4().hex[:20]}"
    prep_id = f"t_{uuid.uuid4().hex[:20]}"
    now = datetime.now(UTC).replace(tzinfo=None)
    # `role` and `tier` are NOT NULL with no server default, so they must be stated. Omitting them made
    # every case here fail on the insert — and the two "expect refused" cases then **passed for the wrong
    # reason**, agreeing with the assertion by accident. The "must allow" cases are what exposed it, which is
    # the argument for always asserting both directions of a guard.
    await conn.execute(
        text(
            'INSERT INTO "User" (id, email, role, tier, "createdAt", "updatedAt") '
            "VALUES (:id, :email, 'USER', 'FREE', :now, :now)"
        ),
        {"id": user_id, "email": f"{user_id}@example.invalid", "now": now},
    )
    await conn.execute(
        text(
            'INSERT INTO "ExamPrep" (id, "userId", subject, "examDate", status, "createdAt", "updatedAt") '
            "VALUES (:id, :uid, :subject, :exam, :status, :now, :now)"
        ),
        {
            "id": prep_id,
            "uid": user_id,
            "subject": "Trigger probe",
            "exam": SITTING_NAIVE,
            "status": status,
            "now": now,
        },
    )
    return user_id, prep_id


async def _add_outcome(conn, prep_id: str, user_id: str, sitting: datetime) -> None:
    await conn.execute(
        text(
            'INSERT INTO "PrepOutcome" (id, "prepId", "userId", "examDate", attended, "answeredAt", '
            '"createdAt", "updatedAt") '
            "VALUES (:id, :pid, :uid, :exam, 'sat', :now, :now, :now)"
        ),
        {
            "id": f"t_{uuid.uuid4().hex[:20]}",
            "pid": prep_id,
            "uid": user_id,
            "exam": sitting,
            "now": datetime.now(UTC).replace(tzinfo=None),
        },
    )


async def _set_status(conn, prep_id: str, status: str) -> None:
    await conn.execute(
        text('UPDATE "ExamPrep" SET status = :s WHERE id = :id'),
        {"s": status, "id": prep_id},
    )


async def _case(conn, name: str, *, expect_refused: bool, body) -> bool:
    """Run one case in a savepoint, roll it back, and report whether it behaved."""
    savepoint = await conn.begin_nested()
    refused = False
    detail = ""
    try:
        await body(conn)
    except Exception as exc:  # noqa: BLE001 - the kind of failure is checked below
        refused = True
        detail = str(getattr(exc, "orig", exc))
    finally:
        await savepoint.rollback()

    # **A refusal only counts if it came from the guard.** Any exception used to satisfy `expect_refused`,
    # so a broken seed — a NOT NULL column omitted — made the refuse cases pass while the write under test
    # never ran. The custom `ERRCODE` exists precisely so this can be told apart from an incidental failure.
    from_guard = "MG001" in detail or "awaiting review and has no recorded outcome" in detail
    if refused and not from_guard:
        print(f"  [ERROR] {name}\n         the probe itself failed, so nothing was tested:\n         {detail}")
        return False

    ok = refused == expect_refused
    verdict = "PASS" if ok else "FAIL"
    wanted = "refused" if expect_refused else "allowed"
    got = "refused by the guard" if refused else "allowed"
    print(f"  [{verdict}] {name}\n         expected {wanted}, got {got}")
    return ok


async def main() -> None:
    engine = create_async_engine(_url(), echo=False)
    results: list[bool] = []

    async with engine.connect() as conn:
        outer = await conn.begin()

        installed = (
            await conn.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'exam_prep_completion_guard'")
            )
        ).scalar()
        print(f"trigger installed: {bool(installed)}\n")
        if not installed:
            print("Migration 056 has not been applied. Nothing to verify.")
            await outer.rollback()
            await engine.dispose()
            return

        async def unanswered(c):
            _, prep_id = await _seed(c, "AWAITING_REVIEW")
            await _set_status(c, prep_id, "COMPLETED")

        async def answered(c):
            user_id, prep_id = await _seed(c, "AWAITING_REVIEW")
            await _add_outcome(c, prep_id, user_id, SITTING_AWARE)
            await _set_status(c, prep_id, "COMPLETED")

        async def wrong_sitting(c):
            user_id, prep_id = await _seed(c, "AWAITING_REVIEW")
            await _add_outcome(c, prep_id, user_id, OTHER_SITTING_AWARE)
            await _set_status(c, prep_id, "COMPLETED")

        async def abandon_early(c):
            _, prep_id = await _seed(c, "IN_PROGRESS")
            await _set_status(c, prep_id, "COMPLETED")

        async def postponed(c):
            _, prep_id = await _seed(c, "AWAITING_REVIEW")
            await _set_status(c, prep_id, "IN_PROGRESS")

        async def repaired(c):
            _, prep_id = await _seed(c, "COMPLETED")
            await _set_status(c, prep_id, "AWAITING_REVIEW")

        print("--- what the guard must refuse ---")
        results.append(
            await _case(
                conn,
                "AWAITING_REVIEW -> COMPLETED with no outcome",
                expect_refused=True,
                body=unanswered,
            )
        )
        results.append(
            await _case(
                conn,
                "AWAITING_REVIEW -> COMPLETED with an outcome for a different sitting",
                expect_refused=True,
                body=wrong_sitting,
            )
        )

        print("\n--- what it must allow ---")
        results.append(
            await _case(
                conn,
                "AWAITING_REVIEW -> COMPLETED with this sitting's outcome (the real path)",
                expect_refused=False,
                body=answered,
            )
        )
        results.append(
            await _case(
                conn,
                "IN_PROGRESS -> COMPLETED (abandoning before the exam)",
                expect_refused=False,
                body=abandon_early,
            )
        )
        results.append(
            await _case(
                conn,
                "AWAITING_REVIEW -> IN_PROGRESS (postponed exam)",
                expect_refused=False,
                body=postponed,
            )
        )
        results.append(
            await _case(
                conn,
                "COMPLETED -> AWAITING_REVIEW (the repair script)",
                expect_refused=False,
                body=repaired,
            )
        )

        await outer.rollback()

    await engine.dispose()
    print(f"\n{sum(results)}/{len(results)} cases behaved as the migration claims.")
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
