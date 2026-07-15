"""
Billing domain — Data access layer.

Encapsulates all Prisma queries for subscription state, credit transactions,
referral rewards, ad claims, and billing-related user fields.
"""

import logging
from datetime import datetime
from typing import Any

from src.domains.identity.db_models import User

from src.shared.database import db

logger = logging.getLogger(__name__)


class BillingRepository:
    """Data access for billing-related entities."""

    # -----------------------------------------------------------------------
    # Subscription state (on User table)
    # -----------------------------------------------------------------------

    async def get_user_billing(self, user_id: str) -> User | None:
        """Fetch user with subscription-relevant fields."""
        return await db.user.find_unique(where={"id": user_id})

    async def update_subscription(self, user_id: str, data: dict[str, Any]) -> User:
        """Update subscription fields on user (tier, stripe/paystack IDs, etc.)."""
        return await db.user.update(where={"id": user_id}, data=data)

    async def find_user_by_stripe_customer(self, stripe_customer_id: str) -> User | None:
        return await db.user.find_unique(where={"stripeCustomerId": stripe_customer_id})

    async def find_user_by_stripe_subscription(self, subscription_id: str) -> User | None:
        return await db.user.find_unique(where={"stripeSubscriptionId": subscription_id})

    async def find_user_by_paystack_customer(self, customer_code: str) -> User | None:
        return await db.user.find_unique(where={"paystackCustomerCode": customer_code})

    async def find_user_by_google_play_token(self, purchase_token: str) -> User | None:
        return await db.user.find_unique(where={"googlePlayPurchaseToken": purchase_token})

    # -----------------------------------------------------------------------
    # Credits
    # -----------------------------------------------------------------------

    async def get_credits(self, user_id: str) -> dict[str, Any]:
        """Get current credit state for a user."""
        user = await db.user.find_unique(where={"id": user_id})
        if not user:
            return {}
        return {
            "credits_used": user.creditsUsed or 0,
            "purchased_balance": user.purchasedCreditsBalance or 0,
            "credits_used_today": user.creditsUsedToday or 0,
            "daily_limit": user.creditsDailyLimit,
            "hard_cap": user.creditsHardCap,
            "soft_cap": user.creditsSoftCap,
            "period_start": user.creditsPeriodStart,
            "period_end": user.creditsPeriodEnd,
        }

    async def update_credits(self, user_id: str, data: dict[str, Any]) -> User:
        """Update credit fields on user."""
        return await db.user.update(where={"id": user_id}, data=data)

    async def adjust_purchased_credits(self, user_id: str, amount: int) -> User:
        """Increment or decrement purchased credits balance."""
        user = await db.user.find_unique(where={"id": user_id})
        if not user:
            raise ValueError(f"User {user_id} not found")
        new_balance = max(0, (user.purchasedCreditsBalance or 0) + amount)
        return await db.user.update(
            where={"id": user_id},
            data={"purchasedCreditsBalance": new_balance},
        )

    # -----------------------------------------------------------------------
    # Credit Purchase Transactions
    # -----------------------------------------------------------------------

    async def create_purchase_transaction(self, data: dict[str, Any]):
        """Record a credit purchase transaction."""
        return await db.creditpurchasetransaction.create(data=data)

    async def get_purchase_history(
        self, user_id: str, *, skip: int = 0, take: int = 20
    ) -> tuple[list, int]:
        """Get paginated purchase history for a user."""
        total = await db.creditpurchasetransaction.count(where={"userId": user_id})
        items = await db.creditpurchasetransaction.find_many(
            where={"userId": user_id},
            order={"createdAt": "desc"},
            skip=skip,
            take=take,
        )
        return items, total

    # -----------------------------------------------------------------------
    # Referral Rewards
    # -----------------------------------------------------------------------

    async def get_referral_rewards(self, user_id: str) -> list:
        """Get all referral rewards for a user (as referrer)."""
        return await db.referralreward.find_many(
            where={"referrerId": user_id},
            order={"createdAt": "desc"},
        )

    async def get_unclaimed_rewards(self, user_id: str) -> list:
        """Get unclaimed referral rewards."""
        return await db.referralreward.find_many(
            where={
                "referrerId": user_id,
                "claims": {"none": {}},
            },
            include={"referredUser": True},
            order={"createdAt": "desc"},
        )

    async def create_reward_claim(self, data: dict[str, Any]):
        """Record a reward claim."""
        return await db.referralrewardclaim.create(data=data)

    # -----------------------------------------------------------------------
    # Ad Reward Claims
    # -----------------------------------------------------------------------

    async def count_ads_today(self, user_id: str, today_start: datetime) -> int:
        """Count ads watched today."""
        return await db.adrewardclaim.count(
            where={
                "userId": user_id,
                "createdAt": {"gte": today_start},
            }
        )

    async def get_total_ad_earnings(self, user_id: str) -> int:
        """Get total credits earned from ads."""
        claims = await db.adrewardclaim.find_many(where={"userId": user_id})
        return sum(claim.credits for claim in claims)

    async def create_ad_claim(self, data: dict[str, Any]):
        """Record an ad reward claim."""
        return await db.adrewardclaim.create(data=data)

    # -----------------------------------------------------------------------
    # Circle/Space Billing
    # -----------------------------------------------------------------------

    async def get_circle_subscription(self, circle_id: str):
        """Get the active subscription for a circle/learning space."""
        return await db.circlesubscription.find_first(
            where={"circleId": circle_id, "status": "active"}
        )

    async def get_seat_addons(self, circle_id: str) -> list:
        """Get all seat add-ons for a circle/learning space."""
        return await db.circleseataddon.find_many(
            where={"circleId": circle_id},
            order={"createdAt": "desc"},
        )

    # -----------------------------------------------------------------------
    # Audit
    # -----------------------------------------------------------------------

    async def create_audit_log(self, data: dict[str, Any]):
        """Create an audit log entry for billing actions."""
        return await db.auditlog.create(data=data)


# Singleton
billing_repo = BillingRepository()
