"""Read-only check of the Bug Hunt seasons on whatever database is configured.

Answers the launch-checklist question "is Season 1 actually open, and is it configured the way the plan
signed off" without anybody reading a JSON blob by eye. Reports differences against
`bug_hunt.rewards` defaults rather than against numbers typed in here, so this cannot drift from the
values the domain itself considers correct.

**Reads only.** No writes, no transactions that could hold a lock on a shared database.

    .venv/bin/python scripts/check_bug_hunt_season.py

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from src.domains.bug_hunt import rewards  # noqa: E402
from src.domains.bug_hunt.db_models import (  # noqa: E402
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
)
from src.shared.database import connect_db, disconnect_db, get_session_factory  # noqa: E402

TICK = "\u2713"
CROSS = "\u2717"


def naira(kobo: int | None) -> str:
    if kobo is None:
        return "not set"
    return f"NGN {kobo / 100:,.0f}"


async def main() -> int:
    await connect_db()
    factory = get_session_factory()
    problems: list[str] = []

    async with factory() as session:
        seasons = (
            (await session.execute(select(BugHuntProgram).order_by(BugHuntProgram.season_number)))
            .scalars()
            .all()
        )

        if not seasons:
            print(f"{CROSS} No Bug Hunt seasons exist on this database.")
            await disconnect_db()
            return 1

        print(f"Found {len(seasons)} season(s).\n")
        now = datetime.now(UTC)

        for program in seasons:
            counts = dict(
                (
                    await session.execute(
                        select(BugHuntParticipant.status, func.count())
                        .where(BugHuntParticipant.program_id == program.id)
                        .group_by(BugHuntParticipant.status)
                    )
                ).all()
            )
            submissions = (
                await session.execute(
                    select(func.count())
                    .select_from(BugHuntSubmission)
                    .where(BugHuntSubmission.program_id == program.id)
                )
            ).scalar() or 0
            credited = (
                await session.execute(
                    select(func.coalesce(func.sum(BugHuntLedgerEntry.amount_kobo), 0)).where(
                        BugHuntLedgerEntry.program_id == program.id,
                        BugHuntLedgerEntry.amount_kobo > 0,
                    )
                )
            ).scalar() or 0

            live = program.status == "open" and program.starts_at <= now <= program.ends_at
            print(f"=== Season {program.season_number}: {program.name} ({program.slug})")
            print(f"  status          {program.status}{'  (live now)' if live else ''}")
            print(
                f"  window          {program.starts_at:%Y-%m-%d %H:%M %Z} -> {program.ends_at:%Y-%m-%d %H:%M %Z}"
            )
            print(f"  days            {(program.ends_at - program.starts_at).days}")
            print(f"  countries       {', '.join(program.country_allowlist)}")
            print(
                f"  budget          {naira(program.budget_kobo)}  (awarded {naira(program.awarded_kobo)})"
            )
            print(f"  per-tester cap  {naira(program.per_participant_cap_kobo)}")
            print(f"  min withdrawal  {naira(program.min_withdrawal_kobo)}")
            print(f"  pass uplift     {program.pass_uplift_percent}%")
            print(f"  daily limit     {program.submission_daily_limit}")
            print(f"  rules version   {program.rules_version}")
            print(f"  participants    {counts or 'none'}")
            print(f"  submissions     {submissions}")
            print(f"  credited        {naira(int(credited))}")

            matrix = program.reward_matrix or {}
            print("  reward matrix")
            expected = rewards.DEFAULT_REWARD_MATRIX
            for category in sorted(expected):
                for severity in sorted(expected[category]):
                    want = expected[category][severity]
                    got = (matrix.get(category) or {}).get(severity)
                    mark = TICK if got == want else CROSS
                    note = "" if got == want else f"   <- default is {naira(want)}"
                    print(f"    {mark} {category:<8} {severity:<10} {naira(got)}{note}")
                    if got != want:
                        problems.append(
                            f"Season {program.season_number} prices {category}/{severity} at "
                            f"{naira(got)}, not the default {naira(want)}"
                        )

            # Only worth flagging on the season that is actually live.
            if live:
                if program.per_participant_cap_kobo != rewards.DEFAULT_PER_PARTICIPANT_CAP_KOBO:
                    problems.append(
                        f"Season {program.season_number} cap is "
                        f"{naira(program.per_participant_cap_kobo)}, not the default "
                        f"{naira(rewards.DEFAULT_PER_PARTICIPANT_CAP_KOBO)}"
                    )
                if program.min_withdrawal_kobo != rewards.DEFAULT_MIN_WITHDRAWAL_KOBO:
                    problems.append(
                        f"Season {program.season_number} minimum withdrawal is "
                        f"{naira(program.min_withdrawal_kobo)}, not the default "
                        f"{naira(rewards.DEFAULT_MIN_WITHDRAWAL_KOBO)}"
                    )
                if program.budget_kobo != rewards.DEFAULT_BUDGET_KOBO:
                    problems.append(
                        f"Season {program.season_number} budget is {naira(program.budget_kobo)}, "
                        f"not the default {naira(rewards.DEFAULT_BUDGET_KOBO)}"
                    )
                if program.ends_at < now:
                    problems.append(
                        f"Season {program.season_number} is marked open but its window closed on "
                        f"{program.ends_at:%Y-%m-%d}. Intake is still accepted; close it or move the date."
                    )
            print()

        # More than one open season is refused by a partial unique index, but checking here means a
        # human reading this output does not have to trust that from memory.
        open_seasons = [p for p in seasons if p.status == "open"]
        if len(open_seasons) > 1:
            problems.append(f"{len(open_seasons)} seasons are open at once.")
        if not open_seasons:
            problems.append(
                "No season is open, so the participant app is in its between-seasons state."
            )

        # Whether the season was made through the admin editor rather than by SQL, which is the
        # multi-season claim the plan wanted demonstrated rather than asserted.
        from src.domains.admin.db_models import AuditLog

        audit = (
            await session.execute(
                select(AuditLog.action_type, AuditLog.timestamp)
                .where(AuditLog.action_type.like("bug_hunt%"))
                .order_by(AuditLog.timestamp)
            )
        ).all()
        print("=== Admin audit trail for bug_hunt actions")
        if not audit:
            print(
                f"  {CROSS} none. The season exists but no admin action created or opened it, so it\n"
                "    was written by SQL or a script. The plan wants the editor path proved."
            )
            problems.append("No bug_hunt admin audit entries: the season editor path is unproven.")
        else:
            for action, at in audit:
                print(f"  {TICK} {at:%Y-%m-%d %H:%M}  {action}")

    await disconnect_db()

    print()
    if problems:
        print(f"{CROSS} {len(problems)} thing(s) to look at:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    print(f"{TICK} Season configuration matches the signed-off defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
