"""Referral codes and the record of who referred whom.

**What this module no longer does: reward anybody.** It used to grant 1 000 tokens for a signup and
500 for a subscription, hold them as unclaimed `ReferralReward` rows, and let a learner claim one to
raise their *daily credit limit* for that calendar day. Five functions implemented that —
`REFERRAL_REWARDS`, `track_referral_subscription`, `get_claimable_rewards`, `claim_referral_reward`,
`get_daily_limit_increase` — and all five are deleted.

Three reasons, in order of weight:

1. **Nothing tops up a window (§6.3).** The reward's mechanism was `creditsDailyLimit`, which
   Phase 3 dropped. A rolling 5-hour allowance has nowhere to put a bonus that isn't either
   invisible (spent within the window it lands in) or unbounded.
2. **A subscription is the wrong trigger.** Paying us is something the *referred* learner does;
   rewarding the referrer for it pays out on the signal easiest to game and says nothing about
   whether the referral was any good.
3. **A reward you must claim, that then silently raises a number you cannot see, buys no advocacy.**
   The learner could not observe it, predict it or plan around it.

Decision O replaces all of it with points: granted when a referred learner has genuinely studied on
seven distinct days, expiring per grant at 60 days, redeemable for passes and for nothing else. That
is a different contract, not a port.

**What survives, ported to SQLAlchemy:** the code, and the record of who referred whom. Both are
inputs to the points ledger, and both were dead before this — the module held a
`PrismaClientRemoved` sentinel where its database used to be, so `get_or_create_referral_code`
raised on every call. `milestone_service` has been rendering share cards with a slice of a primary
key in place of a referral code as a result.

`get_referral_stats` keeps its shape minus the two token totals, which counted a currency that is
being retired. It reports referral counts; the points balance joins it in Phase 4b, from the ledger.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import secrets
import string
from typing import Any

from sqlalchemy import func, select, update

from src.domains.billing.db_models import ReferralReward
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)


def generate_referral_code(length: int = 8) -> str:
    """Generate a candidate referral code.

    Uppercase letters and digits only, so it survives being read aloud, typed on a phone keyboard
    and printed on a share card. Collisions are handled by the caller, not by making the code
    longer.
    """
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def get_or_create_referral_code(user_id: str) -> str:
    """Return the learner's referral code, minting one on first use.

    Args:
        user_id: The learner's id. Takes an id rather than a `User` row, unlike the Prisma version:
            the row it was handed could be stale, and it wrote through to the database anyway.

    Returns:
        The referral code.

    Raises:
        ValueError: If the user does not exist, or if ten candidate codes all collided — at 36^8
            that means something is wrong with the generator, not with our luck.
    """
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(User.referral_code).where(User.id == user_id))
        ).scalar_one_or_none()
        if existing:
            return existing

    for _ in range(10):
        code = generate_referral_code()
        async with factory() as session:
            taken = (
                await session.execute(select(User.id).where(User.referral_code == code))
            ).scalar_one_or_none()
            if taken:
                continue
            result = await session.execute(
                update(User)
                .where(User.id == user_id, User.referral_code.is_(None))
                .values(referral_code=code)
            )
            await session.commit()

        rowcount = getattr(result, "rowcount", 0)
        if rowcount:
            logger.info("Generated referral code %s for user %s", code, user_id)
            return code

        # The row either does not exist or acquired a code between the read above and this write.
        # Re-read rather than retry: a second racer minting a *different* code would leave the two
        # callers disagreeing about the learner's own code.
        async with factory() as session:
            settled = (
                await session.execute(select(User.referral_code).where(User.id == user_id))
            ).scalar_one_or_none()
        if settled:
            return settled
        raise ValueError(f"User {user_id} not found")

    raise ValueError("Failed to generate a unique referral code after 10 attempts")


async def track_referral_signup(referred_user_id: str, referral_code: str) -> str | None:
    """Record that `referred_user_id` signed up on someone's code.

    Records the relationship and **grants nothing**. The grant now waits on the referred learner
    studying for seven distinct days (Decision O), which this row is the precondition for
    rather than the trigger of.

    Args:
        referred_user_id: The learner who just signed up.
        referral_code: The code they arrived with.

    Returns:
        The referrer's id, or `None` if the code is unknown or self-referral was attempted.
    """
    # Normalised here rather than at each caller. Codes are minted uppercase, and one arriving
    # from a URL or a share sheet is routinely lowercase — an exact match on the raw value
    # silently loses a real referral, which is the least visible way for this to fail.
    referral_code = (referral_code or "").strip().upper()
    if not referral_code:
        return None

    factory = get_session_factory()
    async with factory() as session:
        referrer_id = (
            await session.execute(select(User.id).where(User.referral_code == referral_code))
        ).scalar_one_or_none()

    if not referrer_id:
        logger.warning("Referral code %s not found", referral_code)
        return None

    if referrer_id == referred_user_id:
        logger.warning("User %s attempted self-referral", referred_user_id)
        return None

    async with factory() as session:
        already = (
            await session.execute(
                select(ReferralReward.id).where(
                    ReferralReward.referred_user_id == referred_user_id,
                    ReferralReward.reward_type == "signup",
                )
            )
        ).scalar_one_or_none()
        if already:
            return referrer_id

        # `tokens=0` and `is_claimed=False` are the columns' defaults and are left alone. The row is
        # a record of a relationship now, not of an amount owed; the amount lives in the points
        # ledger, and these two columns go when Decision O's tables land.
        session.add(
            ReferralReward(
                referrer_id=referrer_id,
                referred_user_id=referred_user_id,
                reward_type="signup",
            )
        )
        await session.execute(
            update(User)
            .where(User.id == referred_user_id, User.referred_by_code.is_(None))
            .values(referred_by_code=referral_code)
        )
        try:
            await session.commit()
        except Exception:
            # The unique index on (referrerId, referredUserId, rewardType) is the real guard; the
            # read above only avoids the common case. Two concurrent signups on one code cannot
            # both land, and losing the race is success.
            await session.rollback()

    logger.info("Recorded referral: %s -> %s", referrer_id, referred_user_id)
    return referrer_id


def referral_display_name(full_name: str | None) -> str:
    """First name only, or `Friend` when the referred learner has no name to show.

    The list is for the referrer, not a directory: an email or a full legal name would be more than
    this surface is allowed to leak.
    """
    if not full_name:
        return "Friend"
    first = full_name.strip().split(None, 1)[0] if full_name.strip() else ""
    return first or "Friend"


def referral_progress(*, distinct_days: int, qualified: bool, required: int) -> tuple[str, int]:
    """Status plus a day count a client can put next to `required` without clamping."""
    capped = min(max(int(distinct_days), 0), required)
    if qualified:
        return "qualified", required
    return "pending", capped


async def get_referral_stats(user_id: str) -> dict[str, Any]:
    """Referral code, counts, and each referred learner's stage on the seven-day gate.

    `totalTokensEarned` and `totalTokensClaimed` are gone: they summed a currency being retired.
    Progress is derived at read time from `ReferralReward` + `UsageEvent` + the points ledger, so a
    client never has to reconstruct a status the three tables could disagree about.
    """
    from src.domains.billing.db_models import PointsLedgerEntry, UsageEvent
    from src.domains.billing.services.points_service import (
        KIND_REFERRAL,
        POINTS_PER_QUALIFIED_REFERRAL,
        QUALIFICATION_DISTINCT_DAYS,
    )
    from src.domains.identity.db_models import User

    referral_code = await get_or_create_referral_code(user_id)

    factory = get_session_factory()
    async with factory() as session:
        reward_rows = (
            await session.execute(
                select(ReferralReward, User.name)
                .join(User, User.id == ReferralReward.referred_user_id)
                .where(
                    ReferralReward.referrer_id == user_id,
                    ReferralReward.reward_type == "signup",
                )
                .order_by(ReferralReward.created_at.desc())
            )
        ).all()

        referred_ids = [reward.referred_user_id for reward, _name in reward_rows]
        day_counts: dict[str, int] = {}
        grants: dict[str, PointsLedgerEntry] = {}
        if referred_ids:
            day_rows = (
                await session.execute(
                    select(
                        UsageEvent.user_id,
                        func.count(func.distinct(func.date(UsageEvent.created_at))),
                    )
                    .where(UsageEvent.user_id.in_(referred_ids))
                    .group_by(UsageEvent.user_id)
                )
            ).all()
            day_counts = {uid: int(n or 0) for uid, n in day_rows}

            grant_rows = (
                (
                    await session.execute(
                        select(PointsLedgerEntry).where(
                            PointsLedgerEntry.user_id == user_id,
                            PointsLedgerEntry.kind == KIND_REFERRAL,
                            PointsLedgerEntry.source_ref.in_(referred_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            grants = {row.source_ref: row for row in grant_rows if row.source_ref}

    referrals: list[dict[str, Any]] = []
    for reward, name in reward_rows:
        grant = grants.get(reward.referred_user_id)
        status, study_days = referral_progress(
            distinct_days=day_counts.get(reward.referred_user_id, 0),
            qualified=grant is not None,
            required=QUALIFICATION_DISTINCT_DAYS,
        )
        referrals.append(
            {
                "id": reward.id,
                "displayName": referral_display_name(name),
                "status": status,
                "studyDays": study_days,
                "requiredStudyDays": QUALIFICATION_DISTINCT_DAYS,
                "joinedAt": reward.created_at,
                "qualifiedAt": grant.created_at if grant else None,
                "pointsAwarded": grant.points if grant else None,
            }
        )

    qualified_count = sum(1 for item in referrals if item["status"] == "qualified")
    return {
        "referralCode": referral_code,
        "totalReferrals": len(referrals),
        "pendingReferrals": len(referrals) - qualified_count,
        "qualifiedReferrals": qualified_count,
        "requiredStudyDays": QUALIFICATION_DISTINCT_DAYS,
        "pointsPerQualifiedReferral": POINTS_PER_QUALIFIED_REFERRAL,
        "referrals": referrals,
    }
