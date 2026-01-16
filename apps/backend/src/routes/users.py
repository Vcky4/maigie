import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from prisma import Client as PrismaClient

from src.core.database import db
from src.dependencies import CurrentUser
from src.models.auth import UserResponse
from src.services.credit_service import get_credit_usage
from src.utils.dependencies import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])


# Schema for updating preferences
class PreferencesUpdate(BaseModel):
    theme: str | None = None
    language: str | None = None
    notifications: bool | None = None


@router.put("/preferences", response_model=UserResponse)
async def update_preferences(preferences: PreferencesUpdate, current_user: CurrentUser):
    """
    Update the current user's preferences.
    """
    # Using Prisma's 'update' with a nested write to UserPreferences
    updated_user = await db.user.update(
        where={"id": current_user.id},
        data={
            "preferences": {
                "upsert": {
                    "create": {
                        "theme": preferences.theme or "light",
                        "language": preferences.language or "en",
                        "notifications": (
                            preferences.notifications
                            if preferences.notifications is not None
                            else True
                        ),
                    },
                    "update": {
                        # Only update fields that were sent (exclude None)
                        **preferences.model_dump(exclude_unset=True)
                    },
                }
            }
        },
        include={"preferences": True},
    )

    return updated_user


# ============================================================================
# Usage & Token Tracking
# ============================================================================


class UsageHistoryItem(BaseModel):
    """Token usage for a specific date."""

    date: str  # ISO date string
    tokens: int
    messages: int
    operations: int  # AI operations (course generation, actions, etc.)


class UsageOverview(BaseModel):
    """Overview of token usage."""

    totalTokensUsed: int
    totalMessages: int
    totalOperations: int
    averageTokensPerMessage: float
    periodStart: Optional[str]
    periodEnd: Optional[str]


class UsageResponse(BaseModel):
    """Complete usage response."""

    # Current usage from credit service
    creditsUsed: int
    creditsRemaining: int
    hardCap: int
    softCap: int
    usagePercentage: float
    isSoftCapReached: bool
    isHardCapReached: bool

    # Daily usage (for FREE tier)
    creditsUsedToday: Optional[int]
    creditsRemainingToday: Optional[int]
    dailyLimit: Optional[int]
    dailyUsagePercentage: Optional[float]
    isDailyLimitReached: Optional[bool]
    nextDailyReset: Optional[str]

    # Overview stats
    overview: UsageOverview

    # History (last 30 days)
    history: list[UsageHistoryItem]

    # Tier info
    tier: str
    canUpgrade: bool  # True if user is on FREE tier


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    current_user: CurrentUser,
    db: Annotated[PrismaClient, Depends(get_db_client)] = None,
):
    """
    Get comprehensive token usage information including history, limits, and overview.
    """
    try:
        user_id = current_user.id

        # Get current user with credit info
        user = await db.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get credit usage info
        credit_usage = await get_credit_usage(user, db_client=db)

        # Get chat messages for token usage history
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        # Get all chat messages in the last 30 days
        messages = await db.chatmessage.find_many(
            where={
                "userId": user_id,
                "createdAt": {"gte": thirty_days_ago},
            },
            order={"createdAt": "asc"},
        )

        # Get AI actions (from AIActionLog) for operations count
        # AIActionLog is linked through ChatMessage, so we filter by message userId
        # First get message IDs for this user in the time period
        user_message_ids = [
            msg.id
            for msg in await db.chatmessage.find_many(
                where={"userId": user_id, "createdAt": {"gte": thirty_days_ago}},
                select={"id": True},
            )
        ]

        # Then get actions for those messages
        actions = []
        if user_message_ids:
            actions = await db.aiactionlog.find_many(
                where={
                    "messageId": {"in": user_message_ids},
                    "createdAt": {"gte": thirty_days_ago},
                },
            )

        # Build daily history
        daily_usage = defaultdict(lambda: {"tokens": 0, "messages": 0, "operations": 0})

        for message in messages:
            date_str = message.createdAt.date().isoformat()
            daily_usage[date_str]["tokens"] += message.tokenCount or 0
            daily_usage[date_str]["messages"] += 1

        for action in actions:
            date_str = action.createdAt.date().isoformat()
            daily_usage[date_str]["operations"] += 1

        # Convert to list and fill missing dates
        history = []
        for i in range(30):
            date = (now - timedelta(days=i)).date()
            date_str = date.isoformat()
            usage = daily_usage.get(date_str, {"tokens": 0, "messages": 0, "operations": 0})
            history.append(
                UsageHistoryItem(
                    date=date_str,
                    tokens=usage["tokens"],
                    messages=usage["messages"],
                    operations=usage["operations"],
                )
            )

        # Reverse to show oldest first
        history.reverse()

        # Calculate overview stats
        total_tokens = sum(m.tokenCount or 0 for m in messages)
        total_messages = len(messages)
        total_operations = len(actions)
        avg_tokens_per_message = total_tokens / total_messages if total_messages > 0 else 0.0

        overview = UsageOverview(
            totalTokensUsed=total_tokens,
            totalMessages=total_messages,
            totalOperations=total_operations,
            averageTokensPerMessage=round(avg_tokens_per_message, 2),
            periodStart=credit_usage.get("period_start"),
            periodEnd=credit_usage.get("period_end"),
        )

        # Determine if user can upgrade
        tier_str = str(user.tier) if user.tier else "FREE"
        can_upgrade = tier_str == "FREE"

        return UsageResponse(
            creditsUsed=credit_usage.get("credits_used", 0),
            creditsRemaining=credit_usage.get("credits_remaining", 0),
            hardCap=credit_usage.get("hard_cap", 0),
            softCap=credit_usage.get("soft_cap", 0),
            usagePercentage=credit_usage.get("usage_percentage", 0),
            isSoftCapReached=credit_usage.get("is_soft_cap_reached", False),
            isHardCapReached=credit_usage.get("is_hard_cap_reached", False),
            creditsUsedToday=credit_usage.get("credits_used_today"),
            creditsRemainingToday=credit_usage.get("credits_remaining_today"),
            dailyLimit=credit_usage.get("daily_limit"),
            dailyUsagePercentage=credit_usage.get("daily_usage_percentage"),
            isDailyLimitReached=credit_usage.get("is_daily_limit_reached"),
            nextDailyReset=credit_usage.get("next_daily_reset"),
            overview=overview,
            history=history,
            tier=tier_str,
            canUpgrade=can_upgrade,
        )

    except Exception as e:
        logger.error(f"Error in get_usage: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch usage data")
