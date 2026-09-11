"""Admin dashboard — the composite overview the landing page reads.

One honest aggregate. Every number traces to a persisted row (the honesty invariant): retention is
derived from `User.last_seen_at`, AI cost/revenue from the real `ChatMessage` columns, and revenue is
genuinely near-zero today because there are no payment relationships yet — a true $0, not a fabricated
one. Metrics with no backing are not invented; where the model has nothing to say, the value is a
plain count that is actually true (e.g. inactivity rate), not a manufactured KPI.

This assembles the shape the current admin dashboard expects. The deeper, decision-led redesign the
plan calls for (leading with what needs action over a wall of counters) is a frontend change tracked
separately; this makes the existing surface *honest and populated* rather than empty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from src.shared.database import get_session_factory

from . import analytics_service

_PREMIUM_TIERS = ("PREMIUM_MONTHLY", "PREMIUM_YEARLY")
_PLUS_MONTHLY_USD = (
    9.99  # published Plus price; MRR is an estimate off real tier counts, labelled so.
)


async def overview() -> dict:
    """Assemble the dashboard overview from real rows."""
    from src.domains.content.db_models import BlogPost
    from src.domains.feedback.db_models import Feedback
    from src.domains.identity.db_models import User
    from src.domains.intelligence.db_models import ChatMessage
    from src.domains.knowledge.db_models import Course, CourseOutlineSatisfaction

    now = datetime.now(UTC)
    d1 = now - timedelta(days=1)
    d3 = now - timedelta(days=3)
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)

    factory = get_session_factory()
    async with factory() as session:

        async def _count(stmt) -> int:
            return int((await session.execute(stmt)).scalar() or 0)

        learner = User.role == "USER"

        # --- Users -------------------------------------------------------
        total_users = await _count(select(func.count()).select_from(User).where(learner))
        premium = await _count(
            select(func.count()).select_from(User).where(User.tier.in_(_PREMIUM_TIERS))
        )
        premium_monthly = await _count(
            select(func.count()).select_from(User).where(User.tier == "PREMIUM_MONTHLY")
        )
        premium_yearly = await _count(
            select(func.count()).select_from(User).where(User.tier == "PREMIUM_YEARLY")
        )
        new_7 = await _count(
            select(func.count()).select_from(User).where(learner, User.created_at >= d7)
        )

        # --- Retention (from last_seen_at) -------------------------------
        async def _seen_since(since) -> int:
            return await _count(
                select(func.count()).select_from(User).where(learner, User.last_seen_at >= since)
            )

        dau = await _seen_since(d1)
        wau = await _seen_since(d7)
        mau = await _seen_since(d30)
        prev_wau = await _count(
            select(func.count())
            .select_from(User)
            .where(learner, User.last_seen_at >= d14, User.last_seen_at < d7)
        )
        # "At risk" = inactive 3–14 days (matches the card's own subtitle).
        at_risk_count = await _count(
            select(func.count())
            .select_from(User)
            .where(learner, User.last_seen_at >= d14, User.last_seen_at < d3)
        )
        inactive = max(0, total_users - mau)

        # --- Courses -----------------------------------------------------
        total_courses = await _count(select(func.count()).select_from(Course))
        ai_courses = await _count(
            select(func.count()).select_from(Course).where(Course.is_ai_generated.is_(True))
        )
        sat_total = await _count(select(func.count()).select_from(CourseOutlineSatisfaction))
        sat_satisfied = await _count(
            select(func.count())
            .select_from(CourseOutlineSatisfaction)
            .where(CourseOutlineSatisfaction.kind == "SATISFIED")
        )

        # --- Chat / AI economics (real ChatMessage columns) --------------
        total_messages = await _count(select(func.count()).select_from(ChatMessage))
        cost_30 = float(
            (
                await session.execute(
                    select(func.coalesce(func.sum(ChatMessage.cost_usd), 0.0)).where(
                        ChatMessage.created_at >= d30
                    )
                )
            ).scalar()
            or 0.0
        )
        rev_30 = float(
            (
                await session.execute(
                    select(func.coalesce(func.sum(ChatMessage.revenue_usd), 0.0)).where(
                        ChatMessage.created_at >= d30
                    )
                )
            ).scalar()
            or 0.0
        )

        # --- Feedback / content -----------------------------------------
        pending_feedback = await _count(
            select(func.count()).select_from(Feedback).where(Feedback.status == "PENDING")
        )
        blog_published = await _count(
            select(func.count()).select_from(BlogPost).where(BlogPost.published.is_(True))
        )

    # --- Derived (all from the figures above) ---------------------------
    profit_30 = round(rev_30 - cost_30, 2)
    profit_margin = round(profit_30 / rev_30 * 100, 1) if rev_30 else 0
    wau_change = round((wau - prev_wau) / prev_wau * 100, 1) if prev_wau else 0
    dau_mau_ratio = round(dau / mau * 100) if mau else 0
    churn_rate = round(inactive / total_users * 100) if total_users else 0
    satisfaction_rate = round(sat_satisfied / sat_total * 100) if sat_total else None

    signups, messages = await analytics_service.daily_series(14)
    at_risk = await analytics_service.users_at_risk(20)

    return {
        "users": {"total": total_users, "premium": premium, "newLast7Days": new_7},
        "retention": {
            "dau": dau,
            "wau": wau,
            "wauChange": wau_change,
            "mau": mau,
            "dauMauRatio": dau_mau_ratio,
            "atRiskCount": at_risk_count,
        },
        "courses": {
            "total": total_courses,
            "aiGenerated": ai_courses,
            "outlineSatisfaction": {"satisfactionRatePercent": satisfaction_rate},
        },
        "chat": {
            "totalMessages": total_messages,
            "totalRevenueUsdLast30Days": round(rev_30, 2),
            "totalCostUsdLast30Days": round(cost_30, 2),
            "totalProfitUsdLast30Days": profit_30,
            "profitMargin": profit_margin,
        },
        # Estimated from real tier counts × the published Plus price. Named "estimated" because the
        # month is derived, not a billed figure — there is no active-subscription registry to read.
        "subscriptions": {
            "estimatedMRR": round(premium_monthly * _PLUS_MONTHLY_USD, 2),
            "premiumMonthly": premium_monthly,
            "premiumYearly": premium_yearly,
        },
        "feedback": {"pending": pending_feedback},
        "content": {"blogPublished": blog_published},
        "atRiskUsers": [u.model_dump() for u in at_risk.users],
        "charts": {
            "dailySignups": signups,
            "dailyMessages": messages,
            "activityBreakdown": [
                {"name": "Active", "value": mau},
                {"name": "Inactive", "value": inactive},
            ],
            "churnRate": churn_rate,
        },
    }
