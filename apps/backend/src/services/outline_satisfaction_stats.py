"""Aggregate outline satisfaction KPIs for admin dashboards."""

from __future__ import annotations

from datetime import datetime

from prisma import Client as PrismaClient

from ..models.analytics import CourseOutlineSatisfactionPlatformStats


async def fetch_outline_satisfaction_platform_stats(
    db: PrismaClient,
    *,
    thirty_days_ago: datetime,
) -> CourseOutlineSatisfactionPlatformStats:
    total = await db.courseoutlinesatisfaction.count()
    if total == 0:
        return CourseOutlineSatisfactionPlatformStats()

    satisfied = await db.courseoutlinesatisfaction.count(where={"kind": "SATISFIED"})
    not_satisfied = await db.courseoutlinesatisfaction.count(where={"kind": "NOT_SATISFIED"})
    modification = await db.courseoutlinesatisfaction.count(
        where={"kind": "MODIFICATION_REQUESTED"}
    )
    last_30 = await db.courseoutlinesatisfaction.count(
        where={"createdAt": {"gte": thirty_days_ago}}
    )
    rate = round(100.0 * satisfied / total, 1) if total > 0 else None

    return CourseOutlineSatisfactionPlatformStats(
        totalResponses=total,
        satisfied=satisfied,
        notSatisfied=not_satisfied,
        modificationRequested=modification,
        satisfactionRatePercent=rate,
        responsesLast30Days=last_30,
    )
