"""Admin referrals — reshaped onto the points model.

The Prisma-era `ReferralReward`/`ReferralRewardClaim` tables are empty; referrals now earn points
(`PointsLedgerEntry` with `kind='referral_qualified'`, per `MAIGIE_PLUS_COMMERCIAL_PLAN.md`). So this
reads the points ledger, not the retired token tables, and maps it into the client's referral shape:
`tokens` is the points granted, and a grant is a realised reward (there is no separate claim step in
the points model), so `isClaimed` is True with `claimedAt` = the grant time.
"""

from __future__ import annotations

import math

from sqlalchemy import func, select

from .. import models

_KIND = "referral_qualified"


async def list_referrals(
    *, page: int, page_size: int, referrer_id: str | None = None, is_claimed: bool | None = None
) -> models.ReferralListResponse:
    from src.domains.billing.db_models import PointsLedgerEntry
    from src.domains.identity.db_models import User
    from src.shared.database import get_session_factory

    conditions = [PointsLedgerEntry.kind == _KIND]
    if referrer_id:
        conditions.append(PointsLedgerEntry.user_id == referrer_id)
    # Every points-referral is realised (no claim step); a filter for unclaimed matches nothing.
    if is_claimed is False:
        conditions.append(PointsLedgerEntry.id.is_(None))  # deliberately empty

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(PointsLedgerEntry).where(*conditions)
            )
        ).scalar() or 0
        rows = list(
            (
                await session.execute(
                    select(PointsLedgerEntry)
                    .where(*conditions)
                    .order_by(PointsLedgerEntry.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        # Resolve referrer + referred users in one lookup each.
        ids: set[str] = set()
        for r in rows:
            ids.add(r.user_id)
            if r.source_ref:
                ids.add(r.source_ref)
        users = {}
        if ids:
            users = {
                u.id: u
                for u in (await session.execute(select(User).where(User.id.in_(ids))))
                .scalars()
                .all()
            }

    def _u(uid: str | None):
        return users.get(uid) if uid else None

    rewards = []
    for r in rows:
        referrer = _u(r.user_id)
        referred = _u(r.source_ref)
        rewards.append(
            models.ReferralItem(
                id=r.id,
                referrerId=r.user_id,
                referrerEmail=referrer.email if referrer else None,
                referrerName=referrer.name if referrer else None,
                referredUserId=r.source_ref,
                referredUserEmail=referred.email if referred else None,
                referredUserName=referred.name if referred else None,
                rewardType="referral",
                tokens=r.points,
                isClaimed=True,
                claimedAt=r.created_at,
                createdAt=r.created_at,
            )
        )

    return models.ReferralListResponse(
        rewards=rewards,
        total=total,
        page=page,
        pageSize=page_size,
        totalPages=math.ceil(total / page_size) if total else 0,
    )


async def referral_statistics() -> models.ReferralStatistics:
    from src.domains.billing.db_models import PointsLedgerEntry
    from src.domains.identity.db_models import User
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(PointsLedgerEntry)
                .where(PointsLedgerEntry.kind == _KIND)
            )
        ).scalar() or 0
        tokens = (
            await session.execute(
                select(func.coalesce(func.sum(PointsLedgerEntry.points), 0)).where(
                    PointsLedgerEntry.kind == _KIND
                )
            )
        ).scalar() or 0

        top_rows = (
            await session.execute(
                select(
                    PointsLedgerEntry.user_id,
                    func.count().label("n"),
                    func.coalesce(func.sum(PointsLedgerEntry.points), 0).label("pts"),
                )
                .where(PointsLedgerEntry.kind == _KIND)
                .group_by(PointsLedgerEntry.user_id)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()
        top_ids = [uid for uid, _n, _p in top_rows]
        users = {}
        if top_ids:
            users = {
                u.id: u
                for u in (await session.execute(select(User).where(User.id.in_(top_ids))))
                .scalars()
                .all()
            }

    top_referrers = [
        models.TopReferrer(
            email=users[uid].email if uid in users else None,
            name=users[uid].name if uid in users else None,
            totalReferrals=int(n),
            totalTokens=int(pts),
        )
        for uid, n, pts in top_rows
    ]

    total = int(total)
    tokens = int(tokens)
    return models.ReferralStatistics(
        totalRewards=total,
        # Points are granted on qualification — every reward is realised, none pending.
        claimedRewards=total,
        unclaimedRewards=0,
        totalTokensAwarded=tokens,
        totalTokensClaimed=tokens,
        topReferrers=top_referrers,
        signupRewards=total,
        subscriptionRewards=0,
    )
