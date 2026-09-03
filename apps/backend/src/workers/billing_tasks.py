"""
Billing domain background tasks.

Subscription lifecycle checks and account-deletion processing.

`billing.reset_credit_periods` is deleted (drift 13). It swept up to 500 users whose
`creditsPeriodEnd` had passed and zeroed `creditsUsed` — a scheduled job whose entire purpose was to
make a counter agree with a clock. The usage window does the same work by *reading*: a window that
has elapsed reports zero used, and the first billable operation after it persists the new boundaries
(`credit_consumption_service.window_state`). Nothing needs sweeping, and a learner's allowance no
longer depends on a Celery beat having fired.

That dependency was the real defect, not the cost of the job. The reset ran at most every schedule
interval on at most 500 rows, so a learner whose period ended could stay locked out until a later
pass reached them, and the 501st user waited a full cycle. Lazy rollover cannot have a backlog.
"""

import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="billing.check_expired_trials", queue="default", time_limit=60)
def check_expired_trials_task():
    """Downgrade users whose trial/subscription has expired without renewal."""
    import asyncio

    async def _check():
        from datetime import UTC, datetime

        from sqlalchemy import select, update

        from src.domains.identity.db_models import User
        from src.shared.database import get_session_factory

        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            # Find premium users whose subscription period ended
            stmt = (
                select(User)
                .where(
                    User.tier != "FREE",
                    User.subscription_current_period_end < now,
                    User.stripe_subscription_status.in_(["canceled", "unpaid", "past_due"]),
                )
                .limit(200)
            )
            result = await session.execute(stmt)
            expired = list(result.scalars().all())

            for user in expired:
                upd = update(User).where(User.id == user.id).values(tier="FREE")
                await session.execute(upd)

            await session.commit()

        if expired:
            logger.info(f"Downgraded {len(expired)} expired subscriptions to FREE")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_check())
    finally:
        loop.close()


@celery_app.task(name="billing.process_account_deletions", queue="default", time_limit=120)
def process_account_deletions_task():
    """Process accounts that have passed their 90-day deletion window."""
    import asyncio

    async def _process():
        from datetime import UTC, datetime

        from sqlalchemy import select, update

        from src.domains.identity.db_models import User
        from src.shared.database import get_session_factory

        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            # Find users whose deletion date has passed
            stmt = (
                select(User)
                .where(
                    User.account_deletion_scheduled_for <= now,
                    User.account_deletion_requested_at.isnot(None),
                )
                .limit(50)
            )
            result = await session.execute(stmt)
            ready = list(result.scalars().all())

            for user in ready:
                # Deactivate and anonymize (actual data purge is a separate job)
                upd = (
                    update(User)
                    .where(User.id == user.id)
                    .values(
                        is_active=False,
                        email=f"deleted_{user.id}@maigie.com",
                        name=None,
                        password_hash=None,
                    )
                )
                await session.execute(upd)

            await session.commit()

        if ready:
            logger.info(f"Processed {len(ready)} account deletions")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_process())
    finally:
        loop.close()


@celery_app.task(name="billing.sweep_expired_passes", queue="default", time_limit=120)
def sweep_expired_passes_task():
    """Write the status of passes that have ended, and tell the learner.

    **Expiry is already true before this runs.** `entitlement_service._active_pass` applies both of
    Decision E's endings on read, so a pass whose clock passed four minutes ago is already resolving as
    free and a learner cannot be granted Plus by it while waiting for a job. This is not the mechanism.

    So what is it for? Two things a lazy read cannot do. It **writes** `status='consumed'` and
    `endedReason`, so the inventory listing and any later support question have a durable answer rather
    than one derived on the fly. And it **tells the learner** — a pass ending silently is the one part
    of the product a learner cannot discover by looking, because nothing about their screen changes
    until they try something. Decision E: "a learner whose pass ended must be told, and nothing tells
    them if nothing runs."

    **It does not touch the voice balance, and an earlier version of the plan said it should.** The
    instruction was that the same sweep should zero `voiceSecondsRemaining` when the entitlement its
    `voiceAllowanceSourceId` names ends. Phase 3 built the lazy alternative instead: when the
    entitlement stops naming the stored source, the next read discards the granted balance. Anyone
    adding voice here would be fighting `voice_service.resolve` rather than helping it — and would
    reintroduce exactly the interval the lazy design removes, during which an ended pass's minutes are
    still spendable.

    Every five minutes, and bounded: a batch limit rather than "all rows", so a backlog cannot turn one
    run into a long transaction. Passes that miss a run are picked up by the next one and were already
    free to their learner throughout.
    """
    import asyncio

    async def _sweep() -> dict:
        from datetime import UTC, datetime

        from sqlalchemy import select

        from src.domains.billing.db_models import PlusPass
        from src.domains.billing.services import pass_service
        from src.shared.database import get_session_factory

        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            # Both endings, in one pass over the index. `(status, expiresAt)` covers the clock half;
            # the allowance half is a column comparison on the same rows.
            due = list(
                (
                    await session.execute(
                        select(
                            PlusPass.id,
                            PlusPass.expires_at,
                            PlusPass.units_used,
                            PlusPass.units_allowance,
                        )
                        .where(
                            PlusPass.status == pass_service.STATUS_ACTIVE,
                            (PlusPass.expires_at <= now)
                            | (PlusPass.units_used >= PlusPass.units_allowance),
                        )
                        .order_by(PlusPass.expires_at)
                        .limit(500)
                    )
                ).all()
            )

        expired = exhausted = 0
        for pass_id, expires_at, units_used, units_allowance in due:
            # The clock is checked first, so a pass that ran out of both gets `expired`. That is the
            # honest reading: its time was up regardless of what was left in the allowance, and a
            # learner told "you used it all" about a pass that simply ended would rightly disagree.
            clock_done = expires_at is not None and expires_at <= now
            reason = pass_service.REASON_EXPIRED if clock_done else pass_service.REASON_EXHAUSTED
            try:
                await pass_service.expire(pass_id=pass_id, reason=reason)
            except Exception:
                # One bad pass does not end the run. The next sweep retries it, and it is already free
                # to its learner in the meantime.
                logger.exception("pass sweep: failed to end %s", pass_id)
                continue
            if clock_done:
                expired += 1
            else:
                exhausted += 1

        if due:
            logger.info(
                "pass sweep: ended %d pass(es) — %d expired, %d exhausted",
                expired + exhausted,
                expired,
                exhausted,
            )
        return {
            "ended": expired + exhausted,
            "expired": expired,
            "exhausted": exhausted,
        }

    return asyncio.run(_sweep())


def get_beat_schedule() -> dict:
    """Beat entries for the billing domain.

    Only the pass sweep. `billing.check_expired_trials` is registered above and has never had a
    schedule — a pre-existing gap, and scheduling a sweep that has not been running is a behaviour
    change rather than wiring, so it is recorded here rather than quietly switched on. The same note
    `progress_tasks.get_beat_schedule` carries, for the same reason.

    That gap matters slightly less than it looks: `entitlement_service._subscription_lapsed` expires a
    stale paid tier on read, so a learner whose subscription ended is not treated as Plus while waiting
    for a job. What the unscheduled task would add is writing `tier` back to `FREE`.
    """
    return {
        # Every five minutes (Decision E). Not because expiry needs it — that is resolved on read —
        # but because the notification does, and five minutes is the resolution at which "your pass has
        # ended" is still useful to hear. A longer interval makes the message arrive after the learner
        # has already discovered it themselves.
        "billing.sweep_expired_passes": {
            "task": "billing.sweep_expired_passes",
            "schedule": 300.0,
            "options": {"queue": "default"},
        },
    }
