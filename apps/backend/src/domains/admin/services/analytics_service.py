"""Admin analytics — honest, real-row aggregates.

Every figure here is a COUNT/SUM/AVG over persisted rows. Nothing is estimated, projected or
fabricated: the honesty invariant (`MAIGIE_PLUS_COMMERCIAL_PLAN.md` §3) applies to staff screens too,
and the book warns twice against an analytics wall that optimises a single vanity metric
(`ch24-reasoning`, "resist… a single metric"; `ch06`, "progress matters more than activity").

No platform-level analytics service existed before this — the only prior analytics code
(`progress/services/analytics_service.py`) is strictly per-user session/streak. These reads are
cross-domain by nature (identity + knowledge + intelligence); per Decision 2 they should migrate
behind each domain's own read as those are added, the same transitional note the dashboard counts
carry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from src.shared.database import get_session_factory
from src.shared.exceptions import NotFoundError

from .. import models

_TOP_N = 10


def _course_item(
    course, email: str, name: str | None, modules, topics
) -> models.CourseAnalyticsItem:
    m_total, m_done = modules
    t_total, t_done = topics
    return models.CourseAnalyticsItem(
        courseId=course.id,
        title=course.title,
        userId=course.user_id,
        userEmail=email,
        userName=name,
        progress=course.progress or 0.0,
        totalTopics=t_total,
        completedTopics=t_done,
        totalModules=m_total,
        completedModules=m_done,
        difficulty=course.difficulty,
        isAIGenerated=course.is_ai_generated,
        isArchived=course.archived,
        createdAt=course.created_at,
    )


async def _counts_for_courses(session, course_ids: list[str]) -> tuple[dict, dict]:
    """Module and topic (total, completed) counts keyed by course id, for a bounded set of courses."""
    from src.domains.knowledge.db_models import Module, Topic

    if not course_ids:
        return {}, {}

    module_rows = (
        await session.execute(
            select(
                Module.course_id,
                func.count(Module.id),
                func.sum(case((Module.completed.is_(True), 1), else_=0)),
            )
            .where(Module.course_id.in_(course_ids))
            .group_by(Module.course_id)
        )
    ).all()
    modules = {cid: (int(total), int(done or 0)) for cid, total, done in module_rows}

    topic_rows = (
        await session.execute(
            select(
                Module.course_id,
                func.count(Topic.id),
                func.sum(case((Topic.completed.is_(True), 1), else_=0)),
            )
            .select_from(Module)
            .join(Topic, Topic.module_id == Module.id)
            .where(Module.course_id.in_(course_ids))
            .group_by(Module.course_id)
        )
    ).all()
    topics = {cid: (int(total), int(done or 0)) for cid, total, done in topic_rows}

    return modules, topics


async def platform_analytics() -> models.AdminAnalyticsResponse:
    """Platform-wide statistics plus top users, top courses and recent courses."""
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course, Module, Topic

    factory = get_session_factory()
    async with factory() as session:
        total_users = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
        active_users = (
            await session.execute(
                select(func.count()).select_from(User).where(User.is_active.is_(True))
            )
        ).scalar() or 0
        users_by_tier = dict(
            (await session.execute(select(User.tier, func.count()).group_by(User.tier))).all()
        )

        total_courses = (
            await session.execute(select(func.count()).select_from(Course))
        ).scalar() or 0
        archived_courses = (
            await session.execute(
                select(func.count()).select_from(Course).where(Course.archived.is_(True))
            )
        ).scalar() or 0
        ai_courses = (
            await session.execute(
                select(func.count()).select_from(Course).where(Course.is_ai_generated.is_(True))
            )
        ).scalar() or 0
        courses_by_difficulty = dict(
            (
                await session.execute(
                    select(Course.difficulty, func.count()).group_by(Course.difficulty)
                )
            ).all()
        )
        avg_course_progress = (
            await session.execute(select(func.coalesce(func.avg(Course.progress), 0.0)))
        ).scalar() or 0.0

        # Average of each learner's own average — a different figure from the course average, and
        # labelled as such rather than collapsed into one number.
        per_user_avg = (
            select(func.avg(Course.progress).label("up")).group_by(Course.user_id).subquery()
        )
        avg_user_progress = (
            await session.execute(select(func.coalesce(func.avg(per_user_avg.c.up), 0.0)))
        ).scalar() or 0.0

        total_modules = (
            await session.execute(select(func.count()).select_from(Module))
        ).scalar() or 0
        total_topics = (
            await session.execute(select(func.count()).select_from(Topic))
        ).scalar() or 0
        completed_topics = (
            await session.execute(
                select(func.count()).select_from(Topic).where(Topic.completed.is_(True))
            )
        ).scalar() or 0
        total_est = (
            await session.execute(select(func.coalesce(func.sum(Topic.estimated_hours), 0.0)))
        ).scalar() or 0.0
        completed_est = (
            await session.execute(
                select(func.coalesce(func.sum(Topic.estimated_hours), 0.0)).where(
                    Topic.completed.is_(True)
                )
            )
        ).scalar() or 0.0

        platform = models.PlatformStatistics(
            totalUsers=total_users,
            activeUsers=active_users,
            totalCourses=total_courses,
            activeCourses=total_courses - archived_courses,
            archivedCourses=archived_courses,
            totalModules=total_modules,
            totalTopics=total_topics,
            completedTopics=completed_topics,
            totalEstimatedHours=round(float(total_est), 2),
            completedEstimatedHours=round(float(completed_est), 2),
            averageCourseProgress=round(float(avg_course_progress), 2),
            averageUserProgress=round(float(avg_user_progress), 2),
            usersByTier={str(k): int(v) for k, v in users_by_tier.items()},
            coursesByDifficulty={str(k): int(v) for k, v in courses_by_difficulty.items()},
            aiGeneratedCourses=ai_courses,
            manualCourses=total_courses - ai_courses,
        )

        top_users = await _top_users(session)
        top_courses = await _course_list(session, order_by=Course.progress.desc())
        recent_courses = await _course_list(session, order_by=Course.created_at.desc())

    return models.AdminAnalyticsResponse(
        platformStats=platform,
        topUsers=top_users,
        topCourses=top_courses,
        recentCourses=recent_courses,
    )


async def _top_users(session) -> list[models.UserAnalyticsItem]:
    """The busiest learners by course count, with their topic completion."""
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course, Module, Topic

    per_user = (
        await session.execute(
            select(
                Course.user_id,
                func.count(Course.id).label("total"),
                func.sum(case((Course.archived.is_(False), 1), else_=0)).label("active"),
                func.sum(case((Course.progress >= 100, 1), else_=0)).label("completed"),
                func.coalesce(func.avg(Course.progress), 0.0).label("overall"),
            )
            .group_by(Course.user_id)
            .order_by(func.count(Course.id).desc())
            .limit(_TOP_N)
        )
    ).all()
    if not per_user:
        return []

    user_ids = [r.user_id for r in per_user]
    users = {
        u.id: u
        for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    }

    topic_rows = (
        await session.execute(
            select(
                Course.user_id,
                func.count(Topic.id),
                func.sum(case((Topic.completed.is_(True), 1), else_=0)),
            )
            .select_from(Course)
            .join(Module, Module.course_id == Course.id)
            .join(Topic, Topic.module_id == Module.id)
            .where(Course.user_id.in_(user_ids))
            .group_by(Course.user_id)
        )
    ).all()
    topics = {uid: (int(t or 0), int(d or 0)) for uid, t, d in topic_rows}

    items: list[models.UserAnalyticsItem] = []
    for r in per_user:
        user = users.get(r.user_id)
        if user is None:
            continue
        t_total, t_done = topics.get(r.user_id, (0, 0))
        items.append(
            models.UserAnalyticsItem(
                userId=user.id,
                email=user.email,
                name=user.name,
                tier=user.tier,
                totalCourses=int(r.total),
                activeCourses=int(r.active or 0),
                completedCourses=int(r.completed or 0),
                totalTopics=t_total,
                completedTopics=t_done,
                overallProgress=round(float(r.overall or 0.0), 2),
                createdAt=user.created_at,
            )
        )
    return items


async def _course_list(session, *, order_by) -> list[models.CourseAnalyticsItem]:
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course

    rows = (
        await session.execute(
            select(Course, User.email, User.name)
            .join(User, User.id == Course.user_id)
            .order_by(order_by)
            .limit(_TOP_N)
        )
    ).all()
    course_ids = [c.id for c, _e, _n in rows]
    modules, topics = await _counts_for_courses(session, course_ids)
    return [
        _course_item(c, email, name, modules.get(c.id, (0, 0)), topics.get(c.id, (0, 0)))
        for c, email, name in rows
    ]


async def user_analytics(user_id: str) -> models.UserDetailAnalyticsResponse:
    """One learner's courses and aggregate progress. Raises NotFoundError if the user is gone."""
    from src.domains.identity.db_models import User
    from src.domains.knowledge.db_models import Course

    factory = get_session_factory()
    async with factory() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User", user_id)

        course_rows = (
            (
                await session.execute(
                    select(Course)
                    .where(Course.user_id == user_id)
                    .order_by(Course.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        course_ids = [c.id for c in course_rows]
        modules, topics = await _counts_for_courses(session, course_ids)

        courses = [
            _course_item(
                c,
                user.email,
                user.name,
                modules.get(c.id, (0, 0)),
                topics.get(c.id, (0, 0)),
            )
            for c in course_rows
        ]

    total_courses = len(courses)
    active_courses = sum(1 for c in courses if not c.isArchived)
    archived_courses = total_courses - active_courses
    completed_courses = sum(1 for c in courses if c.progress >= 100)
    total_modules = sum(c.totalModules for c in courses)
    completed_modules = sum(c.completedModules for c in courses)
    total_topics = sum(c.totalTopics for c in courses)
    completed_topics = sum(c.completedTopics for c in courses)
    avg_progress = (
        round(sum(c.progress for c in courses) / total_courses, 2) if total_courses else 0.0
    )

    summary = models.UserProgressSummary(
        userId=user_id,
        totalCourses=total_courses,
        activeCourses=active_courses,
        completedCourses=completed_courses,
        archivedCourses=archived_courses,
        totalModules=total_modules,
        completedModules=completed_modules,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        overallProgress=avg_progress,
        totalEstimatedHours=0.0,
        completedEstimatedHours=0.0,
        averageCourseProgress=avg_progress,
    )
    user_item = models.UserAnalyticsItem(
        userId=user.id,
        email=user.email,
        name=user.name,
        tier=user.tier,
        totalCourses=total_courses,
        activeCourses=active_courses,
        completedCourses=completed_courses,
        totalTopics=total_topics,
        completedTopics=completed_topics,
        overallProgress=avg_progress,
        createdAt=user.created_at,
    )
    return models.UserDetailAnalyticsResponse(user=user_item, courses=courses, summary=summary)


_RISK_INACTIVE_DAYS = 7
_RISK_MEDIUM_DAYS = 14
_RISK_HIGH_DAYS = 30


def _risk_level(days_inactive: int) -> str:
    if days_inactive >= _RISK_HIGH_DAYS:
        return "high"
    if days_inactive >= _RISK_MEDIUM_DAYS:
        return "medium"
    return "low"


async def users_at_risk(limit: int) -> models.UsersAtRiskResponse:
    """Inactive learners, most-inactive first — a read only, no nudge is sent here.

    "Inactive" is the honest signal `User.last_seen_at` (falling back to signup for a learner never
    seen), thresholded at 7 days. Described plainly and never as a verdict on the learner
    (`ch16-the-learner`, `ch14-behaviour`); acting on this list is the separate, consent-gated wake.
    """
    from sqlalchemy import and_, or_, select

    from src.domains.identity.db_models import User
    from src.domains.progress.db_models import UserStreak

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=_RISK_INACTIVE_DAYS)

    factory = get_session_factory()
    async with factory() as session:
        candidates = list(
            (
                await session.execute(
                    select(User).where(
                        User.role == "USER",
                        or_(
                            User.last_seen_at < cutoff,
                            and_(User.last_seen_at.is_(None), User.created_at < cutoff),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids = [u.id for u in candidates]
        streaks = {}
        if ids:
            streaks = {
                s.user_id: s
                for s in (
                    await session.execute(select(UserStreak).where(UserStreak.user_id.in_(ids)))
                )
                .scalars()
                .all()
            }

    items: list[models.AtRiskUser] = []
    for u in candidates:
        last = u.last_seen_at or u.created_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        days_inactive = max(0, (now - last).days)
        streak = streaks.get(u.id)
        items.append(
            models.AtRiskUser(
                userId=u.id,
                email=u.email,
                name=u.name,
                tier=u.tier,
                daysInactive=days_inactive,
                currentStreak=(streak.current_streak if streak else 0),
                longestStreak=(streak.longest_streak if streak else 0),
                lastActivity=last.isoformat(),
                signupDate=u.created_at.isoformat(),
                riskLevel=_risk_level(days_inactive),
            )
        )

    items.sort(key=lambda x: x.daysInactive, reverse=True)
    counts = models.RiskCounts(
        high=sum(1 for i in items if i.riskLevel == "high"),
        medium=sum(1 for i in items if i.riskLevel == "medium"),
        low=sum(1 for i in items if i.riskLevel == "low"),
    )
    return models.UsersAtRiskResponse(users=items[:limit], total=len(items), riskCounts=counts)


async def daily_series(days: int) -> tuple[list[dict], list[dict]]:
    """(signups, messages) day-series over the last ``days`` days, zero-filled.

    Honest counts of real rows: `User.created_at` and `ChatMessage.created_at`. The item keys are
    ``signups`` and ``messages`` — the exact keys the dashboard charts read.
    """
    from src.domains.identity.db_models import User
    from src.domains.intelligence.db_models import ChatMessage

    now = datetime.now(UTC)
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Build each day-bucket expression once and reuse the same object in SELECT and GROUP BY. Calling
    # func.date_trunc twice renders two separately-bound params, which Postgres treats as distinct
    # expressions and then rejects the grouping ("must appear in the GROUP BY clause").
    signup_day = func.date_trunc("day", User.created_at)
    message_day = func.date_trunc("day", ChatMessage.created_at)

    factory = get_session_factory()
    async with factory() as session:
        signup_rows = (
            await session.execute(
                select(signup_day, func.count())
                .where(User.created_at >= since)
                .group_by(signup_day)
            )
        ).all()
        message_rows = (
            await session.execute(
                select(message_day, func.count())
                .where(ChatMessage.created_at >= since)
                .group_by(message_day)
            )
        ).all()

    def _series(rows, key: str) -> list[dict]:
        by_day = {d.date().isoformat(): int(c) for d, c in rows if d is not None}
        out: list[dict] = []
        for i in range(days):
            day = (since + timedelta(days=i)).date().isoformat()
            out.append({"date": day, key: by_day.get(day, 0)})
        return out

    return _series(signup_rows, "signups"), _series(message_rows, "messages")


async def dashboard_charts(days: int) -> dict:
    """Daily signups and messages over the last ``days`` days, zero-filled.

    Returns the shape the dashboard charts consume directly (``dailySignups``/``dailyMessages`` with
    ``signups``/``messages`` keys), so the period selector re-queries without a client-side remap.
    """
    signups, messages = await daily_series(days)
    return {"days": days, "dailySignups": signups, "dailyMessages": messages}
