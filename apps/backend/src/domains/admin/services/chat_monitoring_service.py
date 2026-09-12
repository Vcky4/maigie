"""Admin chat monitoring — aggregate + metadata only.

This is the operational/cost view the plan's Decision 6 permits: session **metadata** (title, activity)
and **aggregates** (message counts, tokens, cost/revenue), never message content. Reading an individual
learner's conversation is a separate, policy-gated action (Open Question 1) and is deliberately NOT
built here — "a learner should never feel surveilled" (`ch16`). Super-admin only.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from src.shared.database import get_session_factory, ilike_any

from .. import models


async def list_chat_sessions(
    *, page: int, page_size: int, user_id: str | None = None, search: str | None = None
) -> models.ChatSessionListResponse:
    from src.domains.identity.db_models import User
    from src.domains.intelligence.db_models import ChatMessage, ChatSession

    conditions = []
    if user_id:
        conditions.append(ChatSession.user_id == user_id)
    if search:
        conditions.append(ilike_any(search, ChatSession.title))

    factory = get_session_factory()
    async with factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(ChatSession).where(*conditions))
        ).scalar() or 0
        rows = (
            await session.execute(
                select(ChatSession, User.email, User.name)
                .outerjoin(User, User.id == ChatSession.user_id)
                .where(*conditions)
                .order_by(ChatSession.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()

        ids = [cs.id for cs, _e, _n in rows]
        agg: dict[str, tuple[int, int, float, float]] = {}
        if ids:
            agg_rows = (
                await session.execute(
                    select(
                        ChatMessage.session_id,
                        func.count(),
                        func.coalesce(func.sum(ChatMessage.token_count), 0),
                        func.coalesce(func.sum(ChatMessage.cost_usd), 0.0),
                        func.coalesce(func.sum(ChatMessage.revenue_usd), 0.0),
                    )
                    .where(ChatMessage.session_id.in_(ids))
                    .group_by(ChatMessage.session_id)
                )
            ).all()
            agg = {
                sid: (int(c), int(tok or 0), float(cost or 0.0), float(rev or 0.0))
                for sid, c, tok, cost, rev in agg_rows
            }

    sessions = []
    for cs, email, name in rows:
        count, tokens, cost, revenue = agg.get(cs.id, (0, 0, 0.0, 0.0))
        sessions.append(
            models.ChatSessionItem(
                id=cs.id,
                userId=cs.user_id,
                userEmail=email,
                userName=name,
                title=cs.title,
                isActive=cs.is_active,
                messageCount=count,
                totalTokens=tokens,
                totalCostUsd=round(cost, 4),
                totalRevenueUsd=round(revenue, 4),
                profitUsd=round(revenue - cost, 4),
                createdAt=cs.created_at,
                updatedAt=cs.updated_at,
            )
        )

    return models.ChatSessionListResponse(
        sessions=sessions,
        total=total,
        page=page,
        pageSize=page_size,
        totalPages=math.ceil(total / page_size) if total else 0,
    )


async def chat_statistics() -> models.ChatStatisticsResponse:
    from src.domains.intelligence.db_models import ChatMessage, ChatSession

    now = datetime.now(UTC)
    since = now - timedelta(days=30)

    factory = get_session_factory()
    async with factory() as session:
        total_sessions = (
            await session.execute(select(func.count()).select_from(ChatSession))
        ).scalar() or 0
        total_messages = (
            await session.execute(select(func.count()).select_from(ChatMessage))
        ).scalar() or 0
        total_tokens = int(
            (
                await session.execute(select(func.coalesce(func.sum(ChatMessage.token_count), 0)))
            ).scalar()
            or 0
        )
        unique_users = (
            await session.execute(
                select(func.count(func.distinct(ChatSession.user_id))).select_from(ChatSession)
            )
        ).scalar() or 0
        total_cost = float(
            (
                await session.execute(select(func.coalesce(func.sum(ChatMessage.cost_usd), 0.0)))
            ).scalar()
            or 0.0
        )
        total_revenue = float(
            (
                await session.execute(select(func.coalesce(func.sum(ChatMessage.revenue_usd), 0.0)))
            ).scalar()
            or 0.0
        )

        day = func.date_trunc("day", ChatMessage.created_at)
        daily_rows = (
            await session.execute(
                select(
                    day,
                    func.count(),
                    func.coalesce(func.sum(ChatMessage.token_count), 0),
                    func.coalesce(func.sum(ChatMessage.cost_usd), 0.0),
                    func.coalesce(func.sum(ChatMessage.revenue_usd), 0.0),
                )
                .where(ChatMessage.created_at >= since)
                .group_by(day)
            )
        ).all()

    daily_stats = {
        d.date().isoformat(): {
            "messages": int(c),
            "tokens": int(tok or 0),
            "costUsd": round(float(cost or 0.0), 4),
            "revenueUsd": round(float(rev or 0.0), 4),
        }
        for d, c, tok, cost, rev in daily_rows
        if d is not None
    }

    profit = round(total_revenue - total_cost, 4)
    return models.ChatStatisticsResponse(
        totalSessions=int(total_sessions),
        totalMessages=int(total_messages),
        totalTokens=total_tokens,
        averageTokensPerMessage=round(total_tokens / total_messages, 1) if total_messages else 0.0,
        uniqueUsers=int(unique_users),
        totalCostUsd=round(total_cost, 4),
        totalRevenueUsd=round(total_revenue, 4),
        totalProfitUsd=profit,
        profitMargin=round(profit / total_revenue * 100, 1) if total_revenue else 0.0,
        dailyStats=daily_stats,
    )
