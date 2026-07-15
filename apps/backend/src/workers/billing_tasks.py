"""
Billing domain background tasks.

Subscription lifecycle checks, credit period resets, and
re-engagement notifications for lapsed users.
"""

import logging

from src.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="billing.reset_credit_periods", queue="default", time_limit=60)
def reset_credit_periods_task():
    """Reset credit usage for users whose billing period has ended."""
    import asyncio

    async def _reset():
        from datetime import UTC, datetime

        from src.shared.database import db

        now = datetime.now(UTC)
        # Find users whose credit period has ended
        users = await db.user.find_many(
            where={
                "creditsPeriodEnd": {"lte": now},
                "creditsUsed": {"gt": 0},
            },
            take=500,
        )
        for user in users:
            await db.user.update(
                where={"id": user.id},
                data={"creditsUsed": 0, "creditsPeriodStart": now},
            )
        if users:
            logger.info(f"Reset credit periods for {len(users)} users")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_reset())
    finally:
        loop.close()


@celery_app.task(name="billing.check_expired_trials", queue="default", time_limit=60)
def check_expired_trials_task():
    """Downgrade users whose trial/subscription has expired without renewal."""
    import asyncio

    async def _check():
        from datetime import UTC, datetime

        from src.shared.database import db

        now = datetime.now(UTC)
        # Find premium users whose subscription period ended
        expired = await db.user.find_many(
            where={
                "tier": {"not": "FREE"},
                "subscriptionCurrentPeriodEnd": {"lt": now},
                "stripeSubscriptionStatus": {"in": ["canceled", "unpaid", "past_due"]},
            },
            take=200,
        )
        for user in expired:
            await db.user.update(
                where={"id": user.id},
                data={"tier": "FREE"},
            )
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

        from src.shared.database import db

        now = datetime.now(UTC)
        # Find users whose deletion date has passed
        ready = await db.user.find_many(
            where={
                "accountDeletionScheduledFor": {"lte": now},
                "accountDeletionRequestedAt": {"not": None},
            },
            take=50,
        )
        for user in ready:
            # Deactivate and anonymize (actual data purge is a separate job)
            await db.user.update(
                where={"id": user.id},
                data={
                    "isActive": False,
                    "email": f"deleted_{user.id}@maigie.com",
                    "name": None,
                    "passwordHash": None,
                },
            )
        if ready:
            logger.info(f"Processed {len(ready)} account deletions")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_process())
    finally:
        loop.close()
