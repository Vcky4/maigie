"""
Billing domain — Data access layer (SQLAlchemy).

Encapsulates queries for subscription state, credit transactions,
referral rewards, ad claims, and billing-related user fields.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.billing.db_models import (
    AdRewardClaim,
    CreditPack,
    CreditPurchaseTransaction,
    ReferralReward,
    ReferralRewardClaim,
)
from src.domains.identity.db_models import User
from src.domains.learning_spaces.db_models import SpaceSeatAddon, SpaceSubscription
from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)


class BillingRepository:
    """Data access for billing-related entities."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Subscription state (on User table)
    # -----------------------------------------------------------------------

    async def get_user_billing(self, user_id: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_subscription(self, user_id: str, data: dict[str, Any]) -> User:
        from src.domains.identity.repository import IdentityRepository

        repo = IdentityRepository()
        return await repo.update(user_id, data)

    async def find_user_by_stripe_customer(self, stripe_customer_id: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.stripe_customer_id == stripe_customer_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_user_by_stripe_subscription(self, subscription_id: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.stripe_subscription_id == subscription_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_user_by_paystack_customer(self, customer_code: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.paystack_customer_code == customer_code)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_user_by_paystack_subscription(self, subscription_code: str) -> User | None:
        """The subscription-code counterpart to `find_user_by_paystack_customer`.

        Added for the Phase 2b port: `subscription.disable` identifies the learner by subscription
        code and by nothing else, so without this the only way to honour a cancellation was a raw
        query in the service. The Stripe side has had both lookups since it was written.
        """
        async with await self._session() as session:
            stmt = select(User).where(User.paystack_subscription_code == subscription_code)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_user_by_email(self, email: str) -> User | None:
        """Paystack webhooks identify a learner by the email on the customer record.

        Not a duplicate of `IdentityRepository.find_by_email`: that one optionally joins
        preferences, and a webhook wants the billing columns and nothing else.
        """
        async with await self._session() as session:
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_user_by_google_play_token(self, purchase_token: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.google_play_purchase_token == purchase_token)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Credits — removed
    # -----------------------------------------------------------------------
    #
    # `get_credits`, `update_credits` and `adjust_purchased_credits` read and wrote the nine
    # credit columns that Phase 3 dropped from `User`. None of the three had a caller: the
    # meter always went through `IdentityRepository` directly, and this trio was a second
    # door to the same rows that happened to be unused. Usage now lives in
    # `credit_consumption_service.window_state`, which reads the four `usage*` columns and is
    # the only thing that writes them.

    # -----------------------------------------------------------------------
    # Credit Purchase Transactions
    # -----------------------------------------------------------------------
    #
    # Read-only now. `create_purchase_transaction`, `find_transaction_by_reference` and
    # `update_transaction` existed to record and settle a credit-pack purchase; nothing can
    # buy one, so nothing writes here. A write path that still worked would be an invitation
    # to sell the withdrawn product from a webhook. `PlusPurchase` takes over the recording
    # (Decision G), and this table is read for history until Decision H drops it.

    async def get_purchase_history(
        self, user_id: str, *, skip: int = 0, take: int = 20
    ) -> tuple[list[CreditPurchaseTransaction], int]:
        async with await self._session() as session:
            count_stmt = (
                select(func.count())
                .select_from(CreditPurchaseTransaction)
                .where(CreditPurchaseTransaction.user_id == user_id)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = (
                select(CreditPurchaseTransaction)
                .where(CreditPurchaseTransaction.user_id == user_id)
                .order_by(CreditPurchaseTransaction.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    # -----------------------------------------------------------------------
    # Credit Packs — removed
    # -----------------------------------------------------------------------
    #
    # `list_active_packs` served the pack catalogue and `find_pack` priced a purchase; both
    # went with the product (§6.1). The `CreditPack` table itself is still mapped, because
    # `get_purchase_history` joins it to name a pack somebody bought, and Decision H drops
    # both tables together in the pass rails.

    # -----------------------------------------------------------------------
    # Referral Rewards
    # -----------------------------------------------------------------------

    async def get_referral_rewards(self, user_id: str) -> list[ReferralReward]:
        async with await self._session() as session:
            stmt = (
                select(ReferralReward)
                .where(ReferralReward.referrer_id == user_id)
                .order_by(ReferralReward.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_unclaimed_rewards(self, user_id: str) -> list[ReferralReward]:
        async with await self._session() as session:
            stmt = (
                select(ReferralReward)
                .where(
                    ReferralReward.referrer_id == user_id,
                    ReferralReward.is_claimed == False,  # noqa: E712
                )
                .order_by(ReferralReward.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_reward_claim(self, data: dict[str, Any]) -> ReferralRewardClaim:
        async with await self._session() as session:
            claim = ReferralRewardClaim(
                user_id=data["userId"],
                reward_id=data["rewardId"],
                tokens_claimed=data["tokensClaimed"],
                claim_date=data["claimDate"],
                daily_limit_increase=data.get("dailyLimitIncrease", 0),
            )
            session.add(claim)
            await session.commit()
            await session.refresh(claim)
            return claim

    # -----------------------------------------------------------------------
    # Ad Reward Claims
    # -----------------------------------------------------------------------

    async def count_ads_today(self, user_id: str, today_start: datetime) -> int:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(AdRewardClaim)
                .where(
                    AdRewardClaim.user_id == user_id,
                    AdRewardClaim.created_at >= today_start,
                )
            )
            return (await session.execute(stmt)).scalar() or 0

    async def get_total_ad_earnings(self, user_id: str) -> int:
        async with await self._session() as session:
            stmt = select(func.coalesce(func.sum(AdRewardClaim.credits), 0)).where(
                AdRewardClaim.user_id == user_id
            )
            return (await session.execute(stmt)).scalar() or 0

    async def create_ad_claim(self, data: dict[str, Any]) -> AdRewardClaim:
        async with await self._session() as session:
            claim = AdRewardClaim(
                user_id=data["userId"],
                ad_type=data["adType"],
                credits=data["credits"],
                ad_unit_id=data.get("adUnitId"),
            )
            session.add(claim)
            await session.commit()
            await session.refresh(claim)
            return claim

    # -----------------------------------------------------------------------
    # Space Billing
    # -----------------------------------------------------------------------

    async def get_space_subscription(self, space_id: str) -> SpaceSubscription | None:
        async with await self._session() as session:
            stmt = select(SpaceSubscription).where(
                SpaceSubscription.space_id == space_id,
                SpaceSubscription.status == "active",
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_seat_addons(self, space_id: str) -> list[SpaceSeatAddon]:
        async with await self._session() as session:
            stmt = (
                select(SpaceSeatAddon)
                .where(SpaceSeatAddon.space_id == space_id)
                .order_by(SpaceSeatAddon.purchased_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())


# Singleton
billing_repo = BillingRepository()
