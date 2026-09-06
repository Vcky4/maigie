"""Does `GET /progress/goals` actually publish `pendingNudge`?

Read-only. The global nudge prompt reads one page of active goals and looks for `pendingNudge`. The database
has the rows and the goal sorts to position 1, so if the prompt shows nothing the field is either absent from
the payload or arriving null. This calls the *same* code path the route uses — `list_goals` then
`_goal_responses` — so it tests the real serialisation rather than a reimplementation of it.

    python scripts/db_direct.py python scripts/debug/check_goal_list_payload.py [email]
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

DEFAULT_EMAIL = "okon.victor.u@gmail.com"


async def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL

    from sqlalchemy import text

    from src.domains.progress import routes as progress_routes
    from src.domains.progress.services import goal_service
    from src.shared.database.session import ensure_db, get_session_factory

    await ensure_db()
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    factory = get_session_factory()
    async with factory() as session:
        user_id = (
            await session.execute(text('SELECT id FROM "User" WHERE email = :e'), {"e": email})
        ).scalar()

    if not user_id:
        print(f"No such learner: {email}")
        return

    # The same call the route makes for the prompt's request: one page of active goals, through the service
    # rather than the repository, so any filtering the service adds is exercised too.
    goals, total = await goal_service.list_goals(
        user_id=user_id, status="ACTIVE", page=1, page_size=20
    )
    print(f"--- {email}: {len(goals)} of {total} active goals ---\n")

    responses = await progress_routes._goal_responses(goals)
    for response in responses:
        published = response.model_dump(by_alias=True)
        nudge = published.get("pendingNudge")
        flag = "  <-- the prompt would fire on this" if nudge else ""
        print(f"  pendingNudge={str(nudge):<20} {response.title[:44]}{flag}")

    print()
    with_nudge = [r for r in responses if r.pendingNudge]
    print(f"  {len(with_nudge)} of {len(responses)} carry a pendingNudge.")
    if not with_nudge:
        print(
            "  The payload is the problem, not the client: the rows exist but the field is null here."
        )
    else:
        print("  The payload is correct, so the client is where it is being lost.")
        # The exact shape the browser receives, for the first one, so a field-name mismatch is visible.
        print(f"\n  first nudged goal as published:\n    {with_nudge[0].model_dump(by_alias=True)}")


if __name__ == "__main__":
    asyncio.run(main())
