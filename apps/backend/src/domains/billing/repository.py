"""
Billing domain — Data access layer (SQLAlchemy).

Encapsulates queries for subscription state, credit transactions,
referral rewards, ad claims, and billing-related user fields.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory
from src.domains.identity.db_models import User
from src.domains.billing.db_models import (
    CreditPack,
    CreditPurchaseTransaction,
    ReferralReward,
    ReferralRewardClaim,
    AdRewardClaim,
)
from src.domains.learning_spaces.db_models import SpaceSubscription, SpaceSeatAddon

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

    async def find_user_by_google_play_token(self, purchase_token: str) -> User | None:
        async with await self._session() as session:
            stmt = select(User).where(User.google_play_purchase_token == purchase_token)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Credits
    # -----------------------------------------------------------------------

    async def get_credits(self, user_id: str) -> dict[str, Any]:
        user = await self.get_user_billing(user_id)
        if not user:
            return {}
        return {
            "credits_used": user.credits_used or 0,
            "purchased_balance": user.purchased_credits_balance or 0,
            "credits_used_today": user.credits_used_today or 0,
            "daily_limit": user.credits_daily_limit,
            "hard_cap": user.credits_hard_cap,
            "soft_cap": user.credits_soft_cap,
            "period_start": user.credits_period_start,
            "period_end": user.credits_period_end,
        }

    async def update_credits(self, user_id: str, data: dict[str, Any]) -> User:
        from src.domains.identity.repository import IdentityRepository

        repo = IdentityRepository()
        return await repo.update(user_id, data)

    async def adjust_purchased_credits(self, user_id: str, amount: int) -> User:
        async with await self._session() as session:
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                raise ValueError(f"User {user_id} not found")
            new_balance = max(0, (user.purchased_credits_balance or 0) + amount)
            upd = (
                update(User).where(User.id == user_id).values(purchased_credits_balance=new_balance)
            )
            await session.execute(upd)
            await session.commit()
        return await self.get_user_billing(user_id)

    # -----------------------------------------------------------------------
    # Credit Purchase Transactions
    # -----------------------------------------------------------------------

    async def create_purchase_transaction(self, data: dict[str, Any]) -> CreditPurchaseTransaction:
        async with await self._session() as session:
            txn = CreditPurchaseTransaction(
                user_id=data["userId"],
                credit_pack_id=data.get("creditPackId"),
                credits_granted=data["creditsGranted"],
                amount_paid=data["amountPaid"],
                currency=data["currency"],
                payment_provider=data["paymentProvider"],
                provider_reference=data["providerReference"],
                session_id=data.get("sessionId"),
                session_expires_at=data.get("sessionExpiresAt"),
                status=data.get("status", "pending"),
                completed_at=data.get("completedAt"),
            )
            session.add(txn)
            await session.commit()
            await session.refresh(txn)
            return txn

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

    async def find_transaction_by_reference(
        self, provider_reference: str
    ) -> CreditPurchaseTransaction | None:
        async with await self._session() as session:
            stmt = select(CreditPurchaseTransaction).where(
                CreditPurchaseTransaction.provider_reference == provider_reference
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_transaction(self, txn_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = (
                update(CreditPurchaseTransaction)
                .where(CreditPurchaseTransaction.id == txn_id)
                .values(**data)
            )
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Credit Packs
    # -----------------------------------------------------------------------

    async def list_active_packs(self) -> list[CreditPack]:
        async with await self._session() as session:
            stmt = (
                select(CreditPack)
                .where(CreditPack.is_active == True)  # noqa: E712
                .order_by(CreditPack.sort_order.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_pack(self, pack_id: str) -> CreditPack | None:
        async with await self._session() as session:
            stmt = select(CreditPack).where(
                CreditPack.id == pack_id, CreditPack.is_active.is_(True)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

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
