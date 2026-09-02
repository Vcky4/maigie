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
