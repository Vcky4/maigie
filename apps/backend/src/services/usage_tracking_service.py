"""
Usage tracking service for managing Free tier feature limits.

This module handles:
- File upload limit tracking (5/month for FREE tier)
- AI summary generation limit tracking
- Monthly period reset for usage counters

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Optional, Tuple

from prisma import Prisma
from prisma.models import User

from ..core.database import db
from ..utils.exceptions import SubscriptionLimitError

logger = logging.getLogger(__name__)

# Feature limits per tier (None = unlimited)
UNLIMITED = {"file_uploads": None, "summary_generations": None}

FEATURE_LIMITS = {
    "FREE": {
        "file_uploads": 2,  # 2 files per month
        "summary_generations": 3,  # 3 summaries per month
    },
    "PREMIUM_MONTHLY": UNLIMITED,
    "PREMIUM_YEARLY": UNLIMITED,
    "STUDY_CIRCLE_MONTHLY": UNLIMITED,
    "STUDY_CIRCLE_YEARLY": UNLIMITED,
    "SQUAD_MONTHLY": UNLIMITED,
    "SQUAD_YEARLY": UNLIMITED,
}


async def get_feature_limit(tier: str, feature: str) -> int | None:
    """
    Get the limit for a specific feature and tier.

    Args:
        tier: User tier (FREE, PREMIUM_MONTHLY, PREMIUM_YEARLY)
        feature: Feature name (file_uploads, summary_generations)

    Returns:
        Limit value or None if unlimited
    """
    limits = FEATURE_LIMITS.get(tier, FEATURE_LIMITS["FREE"])
    return limits.get(feature)


async def ensure_usage_period(user: User, feature: str, db_client: Prisma | None = None) -> User:
    """
    Ensure user has an active usage period for a feature. Reset if period expired.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object with valid usage period
    """
    if db_client is None:
        db_client = db

    now = datetime.now(UTC)
    tier_str = str(user.tier) if user.tier else "FREE"

    # Premium users don't need tracking
    if tier_str != "FREE":
        return user

    # Determine which period field to check based on feature
    if feature == "file_uploads":
        period_start = user.fileUploadsPeriodStart
        count_field = "fileUploadsCount"
        period_start_field = "fileUploadsPeriodStart"
    elif feature == "summary_generations":
        period_start = user.summaryGenerationsPeriodStart
        count_field = "summaryGenerationsCount"
        period_start_field = "summaryGenerationsPeriodStart"
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Check if period needs to be initialized or reset
    needs_reset = False

    if period_start is None:
        needs_reset = True
    else:
        # Normalize: DB may return naive or aware datetimes; ensure we can compare
        if period_start.tzinfo is None:
            period_start = period_start.replace(tzinfo=UTC)
        period_end = period_start + timedelta(days=30)
        if now >= period_end:
            needs_reset = True

    if needs_reset:
        update_data = {
            count_field: 0,
            period_start_field: now,
        }
        user = await db_client.user.update(where={"id": user.id}, data=update_data)
        logger.info(f"Reset {feature} usage period for user {user.id}")

    return user


async def check_feature_limit(
    user: User, feature: str, db_client: Prisma | None = None
) -> tuple[bool, str | None]:
    """
    Check if user can use a feature (hasn't reached limit).

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Tuple of (can_use, error_message)
        - can_use: True if feature can be used, False if limit reached
        - error_message: Optional error message if limit reached
    """
    if db_client is None:
        db_client = db

    tier_str = str(user.tier) if user.tier else "FREE"
    limit = await get_feature_limit(tier_str, feature)

    # Premium users have unlimited access
    if limit is None:
        return True, None

    # Ensure usage period is active
    user = await ensure_usage_period(user, feature, db_client)

    # Refresh user from database to get latest counts
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    # Get current count based on feature
    if feature == "file_uploads":
        current_count = user.fileUploadsCount or 0
        feature_name = "file uploads"
    elif feature == "summary_generations":
        current_count = user.summaryGenerationsCount or 0
        feature_name = "AI summaries"
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Check if limit reached
    if current_count >= limit:
        period_start = (
            user.fileUploadsPeriodStart
            if feature == "file_uploads"
            else user.summaryGenerationsPeriodStart
        )
        if period_start:
            period_end = period_start + timedelta(days=30)
            reset_date = period_end.strftime("%B %d, %Y")
        else:
            reset_date = "next month"

        error_message = (
            f"You've reached your monthly limit of {limit} {feature_name}. "
            f"Your limit will reset on {reset_date}. "
            f"Start a free trial for unlimited {feature_name}."
        )
        return False, error_message

    return True, None


async def increment_feature_usage(
    user: User, feature: str, db_client: Prisma | None = None
) -> User:
    """
    Increment usage counter for a feature.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Updated User object
    """
    if db_client is None:
        db_client = db

    # Check limit before incrementing
    can_use, error_message = await check_feature_limit(user, feature, db_client)
    if not can_use:
        raise SubscriptionLimitError(
            message=error_message or f"Limit reached for {feature}",
            detail=f"Start a free trial for unlimited {feature}.",
        )

    # Determine which field to increment
    if feature == "file_uploads":
        update_data = {"fileUploadsCount": {"increment": 1}}
    elif feature == "summary_generations":
        update_data = {"summaryGenerationsCount": {"increment": 1}}
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Increment counter
    user = await db_client.user.update(where={"id": user.id}, data=update_data)
    logger.info(f"Incremented {feature} usage for user {user.id}")

    return user


async def get_feature_usage(user: User, feature: str, db_client: Prisma | None = None) -> dict:
    """
    Get current usage information for a feature.

    Args:
        user: User model instance
        feature: Feature name (file_uploads, summary_generations)
        db_client: Optional Prisma client (defaults to global db)

    Returns:
        Dictionary with usage information
    """
    if db_client is None:
        db_client = db

    tier_str = str(user.tier) if user.tier else "FREE"
    limit = await get_feature_limit(tier_str, feature)

    # Ensure period is active
    user = await ensure_usage_period(user, feature, db_client)

    # Refresh user from database
    user = await db_client.user.find_unique(where={"id": user.id})
    if not user:
        raise ValueError(f"User {user.id} not found")

    # Get current count
    if feature == "file_uploads":
        current_count = user.fileUploadsCount or 0
        period_start = user.fileUploadsPeriodStart
    elif feature == "summary_generations":
        current_count = user.summaryGenerationsCount or 0
        period_start = user.summaryGenerationsPeriodStart
    else:
        raise ValueError(f"Unknown feature: {feature}")

    # Calculate period end
    if period_start:
        period_end = period_start + timedelta(days=30)
        period_end_str = period_end.isoformat()
    else:
        period_end_str = None

    return {
        "used": current_count,
        "limit": limit,
        "remaining": (limit - current_count) if limit else None,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end_str,
        "is_unlimited": limit is None,
    }


# ---------------------------------------------------------------------------
# AI Usage emission and scope-filtered queries (Circle Reimagining)
# ---------------------------------------------------------------------------
#
# Every AI call MUST be persisted to ``AiUsageRecord`` with a Usage_Scope so
# Personal_Workspace and Circle_Workspace usage are mutually isolated
# (Requirements 7.5, 7.6, 12.2, 13.2, 13.3). The two scopes are stored as:
#
# * ``"personal"``               — user's Personal_Workspace usage
# * ``"circle:{circle_id}"``     — usage scoped to one Circle_Workspace
#
# When ``usage_scope`` starts with ``"circle:"`` the ``circle_id`` column is
# also populated so per-Circle analytics queries can use the dedicated index.

PERSONAL_USAGE_SCOPE = "personal"


def build_circle_usage_scope(circle_id: str) -> str:
    """Build the canonical ``circle:{circle_id}`` Usage_Scope string."""
    if not circle_id:
        raise ValueError("circle_id is required for circle usage scope")
    return f"circle:{circle_id}"


def _validate_usage_scope(usage_scope: str, circle_id: str | None) -> None:
    """Validate that ``usage_scope`` and ``circle_id`` are consistent.

    Raises ``ValueError`` for any of:

    * unknown scope kind
    * ``circle:`` scope without a circle id
    * ``circle:`` scope whose embedded id disagrees with ``circle_id``
    * ``personal`` scope with a non-null ``circle_id``
    """
    if usage_scope == PERSONAL_USAGE_SCOPE:
        if circle_id is not None:
            raise ValueError("circle_id must be None when usage_scope is 'personal'")
        return

    if not usage_scope.startswith("circle:") or len(usage_scope) <= len("circle:"):
        raise ValueError(
            f"Invalid usage_scope: {usage_scope!r}; expected 'personal' or 'circle:{{id}}'"
        )

    embedded_id = usage_scope[len("circle:") :]
    if not circle_id:
        raise ValueError("circle_id is required when usage_scope is 'circle:{id}'")
    if embedded_id != circle_id:
        raise ValueError(
            f"usage_scope circle id {embedded_id!r} does not match circle_id {circle_id!r}"
        )


async def emit_ai_usage(
    *,
    user_id: str,
    usage_scope: str,
    circle_id: str | None,
    provider: str | None,
    model: str | None,
    feature: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_count: int = 1,
    db_client: Prisma | None = None,
) -> None:
    """Persist a single ``AiUsageRecord`` row scoped to ``usage_scope``.

    This is the canonical emit point used by every AI-call code path
    (chat WS, Gemini Live, LLM router, etc.). Personal calls pass
    ``usage_scope='personal'`` and ``circle_id=None``; Circle calls pass
    ``usage_scope='circle:{circle_id}'`` and the matching ``circle_id``.

    The function never raises — telemetry failures are logged but must not
    break the AI request itself.
    """
    if db_client is None:
        db_client = db

    try:
        _validate_usage_scope(usage_scope, circle_id)
    except ValueError as exc:
        logger.warning("emit_ai_usage rejected invalid scope: %s", exc)
        return

    if input_tokens < 0:
        input_tokens = 0
    if output_tokens < 0:
        output_tokens = 0
    if request_count < 1:
        request_count = 1

    try:
        await db_client.aiusagerecord.create(
            data={
                "userId": user_id,
                "usageScope": usage_scope,
                "circleId": circle_id,
                "provider": provider,
                "model": model,
                "feature": feature,
                "inputTokens": int(input_tokens),
                "outputTokens": int(output_tokens),
                "requestCount": int(request_count),
            }
        )
    except Exception:
        # Telemetry failures must not break the request.
        logger.exception(
            "emit_ai_usage failed to persist record for user_id=%s scope=%s",
            user_id,
            usage_scope,
        )


async def get_personal_usage_summary(
    user_id: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    db_client: Prisma | None = None,
) -> dict:
    """Aggregate AI usage for a user's Personal_Workspace only.

    Filters by ``usageScope = 'personal'`` so Circle_Workspace usage is
    excluded (Requirement 13.2, 13.3). Returns request count, input
    tokens, and output tokens.
    """
    if db_client is None:
        db_client = db

    where: dict = {"userId": user_id, "usageScope": PERSONAL_USAGE_SCOPE}
    if since is not None or until is not None:
        created_at: dict = {}
        if since is not None:
            created_at["gte"] = since
        if until is not None:
            created_at["lte"] = until
        where["createdAt"] = created_at

    records = await db_client.aiusagerecord.find_many(where=where)

    total_requests = sum(getattr(r, "requestCount", 1) or 1 for r in records)
    total_input = sum(getattr(r, "inputTokens", 0) or 0 for r in records)
    total_output = sum(getattr(r, "outputTokens", 0) or 0 for r in records)

    return {
        "scope": PERSONAL_USAGE_SCOPE,
        "user_id": user_id,
        "request_count": total_requests,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


async def get_circle_usage_summary(
    circle_id: str,
    *,
    user_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    db_client: Prisma | None = None,
) -> dict:
    """Aggregate AI usage for a Circle_Workspace, optionally per member.

    Filters by ``usageScope = 'circle:{circle_id}'`` so Personal_Workspace
    usage and any other Circle's usage are excluded
    (Requirements 7.5, 7.6, 12.2). When ``user_id`` is provided the result
    is scoped to a single member of the Circle.
    """
    if db_client is None:
        db_client = db

    scope = build_circle_usage_scope(circle_id)
    where: dict = {"usageScope": scope, "circleId": circle_id}
    if user_id is not None:
        where["userId"] = user_id
    if since is not None or until is not None:
        created_at: dict = {}
        if since is not None:
            created_at["gte"] = since
        if until is not None:
            created_at["lte"] = until
        where["createdAt"] = created_at

    records = await db_client.aiusagerecord.find_many(where=where)

    total_requests = sum(getattr(r, "requestCount", 1) or 1 for r in records)
    total_input = sum(getattr(r, "inputTokens", 0) or 0 for r in records)
    total_output = sum(getattr(r, "outputTokens", 0) or 0 for r in records)

    return {
        "scope": scope,
        "circle_id": circle_id,
        "user_id": user_id,
        "request_count": total_requests,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


# ---------------------------------------------------------------------------
# Per-member usage analytics (Task 11.1)
# ---------------------------------------------------------------------------


async def get_per_member_usage(
    circle_id: str,
    *,
    window_days: int = 30,
    detail_level: str = "basic",
    db_client: Prisma | None = None,
) -> list[dict]:
    """Aggregate per-member AI usage for a Circle.

    Args:
        circle_id: The Circle to query.
        window_days: Number of days to look back (default 30).
        detail_level: ``"basic"`` (free Circles) returns only request_count
            and active_days. ``"detailed"`` (plan Circles) adds token_count,
            model breakdown, and feature breakdown.
        db_client: Optional Prisma client.

    Returns:
        List of per-member usage dicts, one per member who has any usage.
    """
    if db_client is None:
        db_client = db

    from datetime import timedelta

    since = datetime.now(UTC) - timedelta(days=window_days)
    scope = build_circle_usage_scope(circle_id)

    records = await db_client.aiusagerecord.find_many(
        where={
            "usageScope": scope,
            "circleId": circle_id,
            "createdAt": {"gte": since},
        },
    )

    # Group by userId
    from collections import defaultdict

    by_user: dict[str, list] = defaultdict(list)
    for r in records:
        by_user[r.userId].append(r)

    results = []
    for user_id, user_records in by_user.items():
        request_count = sum(getattr(r, "requestCount", 1) or 1 for r in user_records)
        active_days = len({r.createdAt.date() for r in user_records})

        entry: dict = {
            "userId": user_id,
            "requestCount": request_count,
            "activeDays": active_days,
        }

        if detail_level == "detailed":
            input_tokens = sum(getattr(r, "inputTokens", 0) or 0 for r in user_records)
            output_tokens = sum(getattr(r, "outputTokens", 0) or 0 for r in user_records)

            # Model breakdown
            model_counts: dict[str, int] = defaultdict(int)
            feature_counts: dict[str, int] = defaultdict(int)
            for r in user_records:
                model_key = f"{getattr(r, 'provider', 'unknown')}:{getattr(r, 'model', 'unknown')}"
                model_counts[model_key] += getattr(r, "requestCount", 1) or 1
                feature = getattr(r, "feature", "unknown") or "unknown"
                feature_counts[feature] += getattr(r, "requestCount", 1) or 1

            entry["inputTokens"] = input_tokens
            entry["outputTokens"] = output_tokens
            entry["tokenCount"] = input_tokens + output_tokens
            entry["modelBreakdown"] = dict(model_counts)
            entry["featureBreakdown"] = dict(feature_counts)

        results.append(entry)

    # Sort by request count descending
    results.sort(key=lambda x: x["requestCount"], reverse=True)
    return results
