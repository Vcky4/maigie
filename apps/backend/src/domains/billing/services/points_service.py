"""Points: earned by referring learners who stay, spent on passes, never on the subscription.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.9, Decision O. One earned currency, one thing to spend it on.

**A ledger, and the balance is derived from it.** Each grant expires 60 days after it is earned, so
the truth is a set of dated signed entries and `balance` is `SUM(points) WHERE NOT expired`. A single
`User.pointsBalance` integer cannot express per-grant expiry, so it is a cache these functions write
and the ledger can always rebuild.

**Redemption produces a pass and cannot reach the subscription — by construction, not by a check.**
`redeem` accepts only the two pass ids in `POINTS_COST`; there is no branch that could grant
subscription time, no coupon path, and no `productKind='subscription'` reachable from here. Stating it
as a construction matters because a validation is a thing a later ticket removes (§6.9). The redeemed
pass is a `PlusPass` with `source='points'` and no `PlusPurchase` behind it — nothing was purchased —
and everything downstream (activation, the fresh window, Decision D's refusal, the sweep) is the code
path a bought pass already uses.

**Expiry is lazy on read as well as swept.** `balance` and the FIFO spend both exclude grants past
their `expiresAt`, so a stale sweep can never let expired points be spent — the same belt-and-braces
Decision E gives passes. The nightly sweep writes the negative `expiry` entries so the ledger is
self-explaining rather than reconstructed.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.domains.billing.db_models import PointsLedgerEntry
from src.domains.identity.repository import IdentityRepository
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, ValidationError

logger = logging.getLogger(__name__)

# ===========================================================================
# The numbers
# ===========================================================================

#: Granted once per referred learner who qualifies (§6.9). One qualified referral is exactly one
#: 5-hour pass, deliberately: a learner who does the thing once gets something whole for it, so nothing
#: ever expires unspent for anyone who did the minimum.
POINTS_PER_QUALIFIED_REFERRAL = 100

#: 60 days from each grant. Long enough to survive a referred learner's own 7-day qualification plus a
#: slow month; short enough to bound the liability and to convert a saver into a user. The reasoning
#: for 60 over 30 or never is in Decision O.
POINTS_EXPIRY_DAYS = 60

#: What each pass costs in points. The 5-hour pass matches one referral exactly (above); the 7-day
#: pass is 2.5×, so two referrals nearly reach it and the small remainder is the only thing that ever
#: expires for a steady earner. **Only passes.** The subscription is absent because points cannot buy
#: it, and its absence here is the construction that guarantees that.
POINTS_COST: dict[str, int] = {
    "plus_pass_5h": 100,
    "plus_pass_7d": 250,
}

#: Distinct billable days a referred learner must reach before their referrer is paid. The whole
#: anti-abuse mechanism now the cap is gone (§6.9): a farm has to drive each fake account on seven
#: separate days, at a free-tier COGS that exceeds the reward.
QUALIFICATION_DISTINCT_DAYS = 7

KIND_REFERRAL = "referral_qualified"
KIND_REDEMPTION = "redemption"
KIND_EXPIRY = "expiry"
KIND_ADJUSTMENT = "adjustment"


# ===========================================================================
# Shapes
# ===========================================================================


@dataclass(frozen=True)
class PointsBalance:
    """A learner's spendable points and when the next batch expires."""

    balance: int
    #: The soonest-expiring live grant's remaining points and date, or `None` when nothing is live.
    #: `None` rather than a zero so a client can tell "nothing to lose" from "loses some tomorrow".
    next_expiry_points: int | None
    next_expiry_at: datetime | None

    @property
    def redeemable(self) -> list[str]:
        """The pass ids this balance can afford right now, cheapest first."""
        return [
            pid
            for pid, cost in sorted(POINTS_COST.items(), key=lambda kv: kv[1])
            if self.balance >= cost
        ]


# ===========================================================================
# Grant
# ===========================================================================


async def grant(
    *, user_id: str, points: int, kind: str, source_ref: str, note: str | None = None
) -> PointsLedgerEntry | None:
    """Write a positive ledger entry and advance the cache. Idempotent on the unique index.

    Returns the entry, or `None` when the unique index refused it — which for a `referral_qualified`
    grant means "already granted for this referred learner", the exact case the daily job produces by
    re-evaluating everyone. A `None` is success, not failure: the point of the constraint is that a
    second attempt is a no-op rather than a double payment.

    `expires_at` is 60 days out. Only positive entries carry one; a redemption or an expiry is written
    by its own function, not this one.
    """
    if points <= 0:
        raise ValidationError(message="A grant must be positive.", detail=f"points={points}")

    now = datetime.now(UTC)
    entry = PointsLedgerEntry(
        user_id=user_id,
        points=points,
        kind=kind,
        expires_at=now + timedelta(days=POINTS_EXPIRY_DAYS),
        source_ref=source_ref,
        note=note,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(entry)
        try:
            await session.commit()
        except IntegrityError:
            # The partial unique index fired: this referral was already granted. Losing the race is
            # success — see the docstring. Nothing to advance, because nothing was written.
            await session.rollback()
            logger.info(
                "points: grant of %d to user=%s (kind=%s ref=%s) already exists",
                points,
                user_id,
                kind,
                source_ref,
            )
            return None
        await session.refresh(entry)

    await _recache_balance(user_id)
    logger.info(
        "points: granted %d to user=%s (kind=%s ref=%s)",
        points,
        user_id,
        kind,
        source_ref,
    )
    return entry


# ===========================================================================
# Read
# ===========================================================================


async def balance(user_id: str) -> PointsBalance:
    """Spendable points and the next expiry. Excludes expired grants without needing the sweep.

    `SUM(points)` over live entries: every positive grant not yet past its `expiresAt`, plus every
    negative entry (redemptions and expiries, which have no `expiresAt` and are always counted). So a
    grant that expired an hour ago stops counting here whether or not the nightly sweep has written its
    `expiry` row — the sweep makes the ledger self-explaining, it does not make the expiry true.
    """
    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(PointsLedgerEntry.points), 0)).where(
                    PointsLedgerEntry.user_id == user_id,
                    # A live grant, or any spend/expiry entry. The `OR expiresAt IS NULL` is what lets
                    # the negatives through — they never carry an expiry.
                    (PointsLedgerEntry.expires_at.is_(None)) | (PointsLedgerEntry.expires_at > now),
                )
            )
        ).scalar() or 0

        # The soonest-expiring live grant, for the wallet's "expires on" line and the notification.
        next_grant = (
            await session.execute(
                select(PointsLedgerEntry)
                .where(
                    PointsLedgerEntry.user_id == user_id,
                    PointsLedgerEntry.kind == KIND_REFERRAL,
                    PointsLedgerEntry.expires_at > now,
                )
                .order_by(PointsLedgerEntry.expires_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    return PointsBalance(
        balance=int(total),
        next_expiry_points=(next_grant.points if next_grant else None),
        next_expiry_at=(next_grant.expires_at if next_grant else None),
    )


async def history(user_id: str, *, limit: int = 50) -> list[PointsLedgerEntry]:
    """The learner's ledger, newest first. Every entry, so the wallet explains its own balance."""
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PointsLedgerEntry)
                    .where(PointsLedgerEntry.user_id == user_id)
                    .order_by(PointsLedgerEntry.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )


# ===========================================================================
# Redeem
# ===========================================================================


async def redeem(*, user_id: str, product_id: str):
    """Spend points on a pass. FIFO across grants, oldest live grant first.

    **Only a pass.** `product_id` not in `POINTS_COST` is refused before anything is read — the
    subscription is not in that dict and there is no code path that could grant it, which is the
    construction §6.9 relies on rather than a validation a later change could drop.

    FIFO so a steady earner never loses anything: the grant closest to expiring is spent first, so
    points leave in the order they would otherwise expire. One negative `redemption` entry is written
    per grant consumed, each referencing the redeemed pass, so the ledger reads as "these grants paid
    for that pass" rather than as one unexplained debit.

    Produces an inventory `PlusPass` via `pass_service.grant(source='points')` — identical to a bought
    pass but for its provenance and the absence of a `PlusPurchase` (Decision O). The learner activates
    it when they want it, which is the whole reason points buy passes and not window units.
    """
    cost = POINTS_COST.get(product_id)
    if cost is None:
        raise ValidationError(
            message="Points can only be redeemed for a pass.",
            detail=f"product_id={product_id} is not redeemable for points",
        )

    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        # Live grants, oldest expiry first, and their spend so far. A grant's remaining value is its
        # points plus the (negative) redemptions already charged against it — which is why redemptions
        # carry the grant's own logic through `_grant_remaining` rather than a column.
        live_grants = list(
            (
                await session.execute(
                    select(PointsLedgerEntry)
                    .where(
                        PointsLedgerEntry.user_id == user_id,
                        PointsLedgerEntry.kind == KIND_REFERRAL,
                        PointsLedgerEntry.expires_at > now,
                    )
                    .order_by(PointsLedgerEntry.expires_at)
                )
            )
            .scalars()
            .all()
        )

        # The spendable total is the live balance. Computed here inside the same session rather than
        # via `balance()` so the check and the spend see one consistent read.
        spendable = await _live_balance(session, user_id, now)
        if spendable < cost:
            raise ConflictError(
                message="You don't have enough points for that pass yet.",
                detail=f"have={spendable}, need={cost}, product={product_id}",
                code="INSUFFICIENT_POINTS",
            )

    # The pass is granted first, so the redemption entry can reference a real `PlusPass.id`. If the
    # grant somehow fails, no points have been spent — the ledger writes come after it.
    from src.domains.billing.services import pass_service

    new_pass = await pass_service.grant(
        user_id=user_id, product_id=product_id, source="points", purchase_id=None
    )

    # One negative entry per grant the cost draws from, oldest first, until the cost is met.
    remaining = cost
    factory = get_session_factory()
    async with factory() as session:
        for grant_entry in live_grants:
            if remaining <= 0:
                break
            grant_remaining = await _grant_remaining(session, grant_entry)
            if grant_remaining <= 0:
                continue
            take = min(grant_remaining, remaining)
            session.add(
                PointsLedgerEntry(
                    user_id=user_id,
                    points=-take,
                    kind=KIND_REDEMPTION,
                    expires_at=None,
                    source_ref=new_pass.id,
                    note=f"redeemed {product_id}",
                )
            )
            remaining -= take
        await session.commit()

    await _recache_balance(user_id)
    logger.info(
        "points: user=%s redeemed %s for %d points -> pass %s",
        user_id,
        product_id,
        cost,
        new_pass.id,
    )
    return new_pass


# ===========================================================================
# Expiry
# ===========================================================================


async def expire_due() -> int:
    """Write the negative `expiry` entries for grants past their date. Returns how many.

    Idempotent: a grant is expired once, marked by an `expiry` entry whose `sourceRef` is the grant's
    own id, and re-running skips any grant that already has one. The balance is already correct without
    this — `balance()` and `redeem()` both exclude expired grants on read — so this exists to make the
    ledger *explain* the drop rather than to cause it.
    """
    now = datetime.now(UTC)
    factory = get_session_factory()
    touched_users: set[str] = set()
    async with factory() as session:
        # Grants that have expired and do not yet have an expiry entry pointing at them. The value to
        # write off is the grant's remaining points, not its face value — a grant partly spent before
        # it expired forfeits only what was left.
        expired_grants = list(
            (
                await session.execute(
                    select(PointsLedgerEntry)
                    .where(
                        PointsLedgerEntry.kind == KIND_REFERRAL,
                        PointsLedgerEntry.expires_at <= now,
                    )
                    .order_by(PointsLedgerEntry.expires_at)
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )

        for grant_entry in expired_grants:
            already = (
                await session.execute(
                    select(PointsLedgerEntry.id).where(
                        PointsLedgerEntry.kind == KIND_EXPIRY,
                        PointsLedgerEntry.source_ref == grant_entry.id,
                    )
                )
            ).scalar_one_or_none()
            if already:
                continue
            remaining = await _grant_remaining(session, grant_entry)
            if remaining <= 0:
                # Fully spent before it expired: nothing to write off, but still mark it so the job
                # does not re-examine it every night.
                session.add(
                    PointsLedgerEntry(
                        user_id=grant_entry.user_id,
                        points=0,
                        kind=KIND_EXPIRY,
                        expires_at=None,
                        source_ref=grant_entry.id,
                        note="grant fully spent before expiry",
                    )
                )
            else:
                session.add(
                    PointsLedgerEntry(
                        user_id=grant_entry.user_id,
                        points=-remaining,
                        kind=KIND_EXPIRY,
                        expires_at=None,
                        source_ref=grant_entry.id,
                        note="grant expired",
                    )
                )
            touched_users.add(grant_entry.user_id)
        await session.commit()

    for user_id in touched_users:
        await _recache_balance(user_id)
    if touched_users:
        logger.info("points: expired grants for %d learner(s)", len(touched_users))
    return len(touched_users)


# ===========================================================================
# Expiry warning
# ===========================================================================

#: How far ahead of a grant's expiry the warning fires. Seven days is long enough to sit down and
#: study or redeem, short enough that the warning is about *this* grant rather than a distant date.
POINTS_EXPIRY_WARNING_DAYS = 7


async def notify_expiring_grants() -> int:
    """Warn learners whose soonest-expiring grant is a week out and still worth spending. Returns how many.

    Fired nightly alongside the sweep. A grant earns a warning only when, seven days from expiry, it
    *alone* still holds at least the cost of the cheapest pass (§6.9, Decision O). The "alone" is the
    point: a learner with a 40-point remainder that can never buy anything is not warned about losing
    it, because there is nothing they could do with the warning — it would name a loss they cannot
    prevent. Only a spendable grant produces a call to action.

    Fires once per grant, deduped by an idempotency key built from the grant's id, so a grant that
    stays in the window for several nightly runs is warned about exactly once. A grant partly spent
    below the pass floor before its window opens is silently skipped — the remainder expires without a
    warning, which is correct: the warning exists to prompt a redemption, and there is no redemption to
    prompt.
    """
    from src.domains.notifications import service as notification_service

    now = datetime.now(UTC)
    horizon = now + timedelta(days=POINTS_EXPIRY_WARNING_DAYS)
    cheapest = min(POINTS_COST.values())

    factory = get_session_factory()
    async with factory() as session:
        # Live grants crossing into their final week. Ordered oldest-expiry-first so a learner with
        # several grants is warned about the one they will lose first.
        expiring = list(
            (
                await session.execute(
                    select(PointsLedgerEntry)
                    .where(
                        PointsLedgerEntry.kind == KIND_REFERRAL,
                        PointsLedgerEntry.expires_at > now,
                        PointsLedgerEntry.expires_at <= horizon,
                    )
                    .order_by(PointsLedgerEntry.expires_at)
                )
            )
            .scalars()
            .all()
        )
        # A grant is worth warning about only if its own remaining value can still buy a pass.
        warnings: list[tuple[PointsLedgerEntry, int]] = []
        for grant_entry in expiring:
            remaining = await _grant_remaining(session, grant_entry)
            if remaining >= cheapest:
                warnings.append((grant_entry, remaining))

    sent = 0
    for grant_entry, remaining in warnings:
        expires_at = grant_entry.expires_at
        days_left = max(0, (expires_at - now).days) if expires_at else 0
        try:
            await notification_service.create_notification(
                user_id=grant_entry.user_id,
                type="billing.points_expiring",
                title="Your points are about to expire",
                body=(
                    f"You have {remaining} points expiring in {days_left} days. "
                    f"Redeem them for a pass before they're gone."
                ),
                action={"version": 1, "kind": "OPEN_BILLING"},
                idempotency_key=f"points-expiring:{grant_entry.id}",
                priority=5,
                source_domain="billing",
                source_entity_type="points_grant",
                source_entity_id=grant_entry.id,
            )
            sent += 1
        except Exception:
            # One learner's notification must not stop the nightly run.
            logger.exception(
                "points: failed to warn user=%s about expiring grant=%s",
                grant_entry.user_id,
                grant_entry.id,
            )

    if sent:
        logger.info("points: warned %d learner(s) about expiring grants", sent)
    return sent


# ===========================================================================
# Qualification
# ===========================================================================


async def qualify_referral(referred_user_id: str) -> PointsLedgerEntry | None:
    """Grant the referrer 100 points if the referred learner has now studied on 7 distinct days.

    **Activity means a billable operation, not an app open.** Distinct calendar days are counted from
    `UsageEvent`, which exists only for charged operations — an account that logs in seven times and
    studies nothing has no rows and does not qualify. This is why the check reads `UsageEvent` rather
    than `lastLoginAt`.

    Distinct **UTC** days, which is a deliberate simplification: a learner's own timezone would be more
    precise, but the seven days are an anti-farm floor rather than a reward the learner watches accrue,
    and a farm driving one account across seven UTC days is doing the thing the check exists to make
    expensive. Recorded so the choice is visible rather than assumed.

    Idempotent via the unique index in `grant`: called daily for everyone not yet qualified, and a
    second grant for the same referred learner is refused by the database and returns `None`.
    """
    from src.domains.billing.db_models import UsageEvent

    # Who referred this learner. `track_referral_signup` recorded it as a `signup` ReferralReward and
    # stamped `User.referred_by_code`; the referrer is whoever owns that code.
    repo = IdentityRepository()
    learner = await repo.find_by_id(referred_user_id)
    if learner is None or not learner.referred_by_code:
        return None

    factory = get_session_factory()
    async with factory() as session:
        from src.domains.identity.db_models import User

        referrer_id = (
            await session.execute(
                select(User.id).where(User.referral_code == learner.referred_by_code)
            )
        ).scalar_one_or_none()
        if not referrer_id or referrer_id == referred_user_id:
            return None

        distinct_days = (
            await session.execute(
                select(func.count(func.distinct(func.date(UsageEvent.created_at)))).where(
                    UsageEvent.user_id == referred_user_id
                )
            )
        ).scalar() or 0

    if distinct_days < QUALIFICATION_DISTINCT_DAYS:
        return None

    # Granted to the referrer, keyed on the referred learner so the unique index makes it once-only.
    return await grant(
        user_id=referrer_id,
        points=POINTS_PER_QUALIFIED_REFERRAL,
        kind=KIND_REFERRAL,
        source_ref=referred_user_id,
    )


# ===========================================================================
# Internals
# ===========================================================================


async def _grant_remaining(session, grant_entry: PointsLedgerEntry) -> int:
    """A grant's unspent points: its face value minus the redemptions charged against it.

    Redemptions reference the pass they bought, not the grant, so "charged against this grant" cannot
    be read by `sourceRef`. Instead a grant's spend is the shortfall between its face value and what
    FIFO would have left it — but FIFO is exactly what `redeem` already applied, so the honest and
    simple reading is: a grant is fully available until a redemption reaches it, and redemptions are
    written oldest-grant-first. This recomputes remaining value by replaying that order within the
    learner's grants and redemptions, which is O(entries) and correct rather than clever.
    """
    user_id = grant_entry.user_id
    grants = list(
        (
            await session.execute(
                select(PointsLedgerEntry)
                .where(
                    PointsLedgerEntry.user_id == user_id,
                    PointsLedgerEntry.kind == KIND_REFERRAL,
                )
                .order_by(PointsLedgerEntry.expires_at)
            )
        )
        .scalars()
        .all()
    )
    total_redeemed = (
        await session.execute(
            select(func.coalesce(func.sum(-PointsLedgerEntry.points), 0)).where(
                PointsLedgerEntry.user_id == user_id,
                PointsLedgerEntry.kind == KIND_REDEMPTION,
            )
        )
    ).scalar() or 0

    # Walk grants oldest-expiry-first, draining the redeemed total. Each grant is reduced by whatever
    # of the redeemed total falls on it, and the one we were asked about reports what is left.
    drain = int(total_redeemed)
    for entry in grants:
        if entry.id == grant_entry.id:
            return max(0, entry.points - min(drain, entry.points))
        drain = max(0, drain - entry.points)
    return 0


async def _live_balance(session, user_id: str, now: datetime) -> int:
    """Spendable total inside an open session — live grants minus everything spent or expired."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PointsLedgerEntry.points), 0)).where(
                PointsLedgerEntry.user_id == user_id,
                (PointsLedgerEntry.expires_at.is_(None)) | (PointsLedgerEntry.expires_at > now),
            )
        )
    ).scalar() or 0
    return int(total)


async def _recache_balance(user_id: str) -> None:
    """Rewrite `User.pointsBalance` from the ledger. Never raises — the cache is rebuildable.

    Same posture as `record_units`: a failure to update the cache under-reports a balance the ledger
    still holds correctly, which is a reason to log rather than to fail whatever the caller was doing.
    """
    try:
        bal = await balance(user_id)
        await IdentityRepository().update(user_id, {"pointsBalance": bal.balance})
    except Exception:
        logger.exception("points: failed to recache balance for user=%s", user_id)
