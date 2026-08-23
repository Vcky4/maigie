"""Bounded composition service for the authenticated Reflect dashboard.

Read-only, and owns no persistence. Every figure comes from the service that already defines it —
`growth_service` for the trends and subjects, `reflect_aggregates` for the goal portfolio,
`goal_metrics` for the goal labels, `reflection_metrics` for the streak — so this surface cannot
disagree with `/reflections`, `/progress/goals` or the Learn dashboard about the same number.

Partial failure follows the policy Learn and Prepare set: a section that cannot be loaded is named in
`meta.degradedSections` and rendered as unavailable, **not** as empty. An empty state and a failed
load look identical to a learner and mean opposite things — "you have not done this yet" against "we
could not read what you did".

Only a total failure is an error.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status

from src.shared.exceptions import MaigieError

from .. import models
from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)

#: Emitted in this order regardless of which failed, so the list is deterministic rather than
#: set-ordered — the same reasoning `prepare_dashboard_service` records.
_SECTION_ORDER: list[models.ReflectDashboardSection] = [
    "summary",
    "reflection",
    "trends",
    "subjects",
    "goals",
    "studyPlans",
    "activity",
    "achievements",
]

#: Which sections each source feeds. Declarative, following the Learn dashboard, so a new source
#: cannot quietly degrade a section it has nothing to do with.
_SOURCE_SECTIONS: dict[str, set[models.ReflectDashboardSection]] = {
    "trends": {"trends", "summary"},
    "subjects": {"subjects", "summary"},
    "goals": {"goals", "summary"},
    "reflection": {"reflection", "summary"},
    "plans": {"studyPlans"},
    "activity": {"activity"},
    "achievements": {"achievements"},
}


def _log_source_failure(user_id: str, source: str, error: BaseException) -> None:
    logger.warning(
        "Reflect dashboard source unavailable",
        extra={"user_id": user_id, "source": source},
        exc_info=(type(error), error, error.__traceback__),
    )


async def _load_goals(user_id: str, *, limit: int, now: datetime) -> tuple[Any, list[Any]]:
    """The portfolio counts and the goals to show, with their derived figures.

    Returns `(portfolio, cards)`. Two reads plus the batched derivations rather than a query per
    goal — `derive_current_values` issues one per metric kind present, not one per goal.
    """
    from src.domains.progress.services import goal_metrics

    from . import reflect_aggregates

    portfolio = await reflect_aggregates.get_goal_portfolio(user_id=user_id, now=now)

    from src.domains.progress.repository import progress_repo

    goals, _ = await progress_repo.list_goals(
        user_id, where={"status": "ACTIVE"}, skip=0, take=limit
    )
    measurements = await goal_metrics.derive_current_values(goals, now=now)

    cards = [
        models.ReflectGoal(
            id=goal.id,
            title=goal.title,
            status=goal.status,
            progress=goal.progress or 0.0,
            target_date=goal.target_date,
            status_label=goal_metrics.status_label(
                progress=goal.progress or 0.0,
                status=goal.status,
                created_at=goal.created_at,
                target_date=goal.target_date,
                now=now,
            ),
            metric_kind=goal.metric_kind,
            target_value=goal.target_value,
            unit=goal.unit,
            current_value=(
                measurements[goal.id].current_value if goal.id in measurements else None
            ),
            current_value_measured=(
                measurements[goal.id].measured if goal.id in measurements else False
            ),
        )
        for goal in goals
    ]
    return portfolio, cards


async def _load_latest_reflection(user_id: str) -> Any | None:
    """The newest reflection, or `None` when the learner has never had one generated."""
    items, _ = await repo.list_reflections(user_id, sort="newest", skip=0, take=1)
    return items[0] if items else None


async def _load_achievements(user_id: str, *, limit: int) -> list[models.ReflectAchievement]:
    """Recent milestones, from both tables that hold them.

    **This used to read `Achievement` alone, and that was Decision Q's instruction — but the table
    nothing writes.** `create_achievement` is called from nowhere in `src`; the four rows that exist are
    Prisma-era and belong to a single learner. `LearningMilestone` is the one written live, by
    `milestone_service._record_milestone`, and it holds rows for five. So this section was showing four
    frozen records to one learner and an empty list to everybody else — indistinguishable, on screen,
    from having achieved nothing.

    Decision Q's *concern* was two milestone lists that could disagree, and that still holds: there is
    one list here. What needed correcting was its *choice* of table, and reading both is what keeps the
    legacy records visible while making the live ones appear at all. `source` on each item says which is
    which.

    Sliced after the merge rather than per table, so the newest few of one kind cannot crowd out newer
    entries of the other.
    """
    from . import reflect_aggregates

    items = await reflect_aggregates.list_growth_milestones(user_id=user_id, limit=limit)
    return [
        models.ReflectAchievement(
            id=item.id,
            achievement_type=item.kind,
            title=item.title,
            description=item.description,
            icon=item.icon,
            unlocked_at=item.unlocked_at,
        )
        for item in items
    ]


async def get_dashboard(
    *,
    user_id: str,
    range_: str = "30d",
    subject_limit: int = 4,
    goal_limit: int = 3,
    plan_limit: int = 2,
    activity_limit: int = 4,
    achievement_limit: int = 3,
) -> models.ReflectDashboardResponse:
    now = datetime.now(UTC)

    from src.domains.personal_learning.repository import personal_learning_repo

    from . import growth_service, reflection_metrics

    # **Two waves of four, not one flat gather of eight**, and this is a connection-budget decision
    # rather than a stylistic one. Each source opens its own session, and several reach different
    # engines — `progress_repo` has its own, and `growth_service` and `reflect_aggregates` go through
    # `get_session_factory`. Eight at once exhausted the session-mode pooler outright
    # (`EMAXCONNSESSION`, 15 clients) when this was first run against the real database, degrading
    # three sections of a page that had nothing wrong with it. Halving the peak keeps the composition
    # concurrent where it matters while staying inside the budget the deployment actually has.
    #
    # The split is the page's spine first, then its side rails, so if the budget were ever exceeded
    # again the sections that would suffer are the peripheral ones.
    trends_result, subjects_result, goals_result, reflection_result = await asyncio.gather(
        growth_service.get_trends(user_id=user_id, range_=range_, now=now),
        growth_service.get_subjects(user_id=user_id, range_=range_, limit=subject_limit, now=now),
        _load_goals(user_id, limit=goal_limit, now=now),
        _load_latest_reflection(user_id),
        return_exceptions=True,
    )

    plans_result, activity_result, achievements_result, streak_result = await asyncio.gather(
        personal_learning_repo.list_plans_paginated(user_id, skip=0, take=plan_limit),
        personal_learning_repo.list_feed_entries(user_id, skip=0, take=activity_limit),
        _load_achievements(user_id, limit=achievement_limit),
        reflection_metrics.count_reflection_streak(user_id=user_id),
        return_exceptions=True,
    )

    results = {
        "trends": trends_result,
        "subjects": subjects_result,
        "goals": goals_result,
        "reflection": reflection_result,
        "plans": plans_result,
        "activity": activity_result,
        "achievements": achievements_result,
    }

    # Total failure only when *every* source failed. A narrower rule would turn one bad query into a
    # blank page, which is exactly what per-section degradation exists to avoid.
    if all(isinstance(result, BaseException) for result in results.values()):
        for source, result in results.items():
            _log_source_failure(user_id, source, result)
        raise MaigieError(
            "Reflect is temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="REFLECT_DASHBOARD_UNAVAILABLE",
        )

    degraded: set[models.ReflectDashboardSection] = set()
    for source, result in results.items():
        if isinstance(result, BaseException):
            degraded.update(_SOURCE_SECTIONS[source])
            _log_source_failure(user_id, source, result)

    trends = (
        trends_result
        if not isinstance(trends_result, BaseException)
        else models.GrowthTrendsResponse(range=range_, days=models.GROWTH_RANGE_DAYS[range_])
    )
    subjects = subjects_result.items if not isinstance(subjects_result, BaseException) else []
    portfolio, goal_cards = (
        goals_result if not isinstance(goals_result, BaseException) else (None, [])
    )
    latest_reflection = (
        reflection_result if not isinstance(reflection_result, BaseException) else None
    )
    plans = plans_result[0] if not isinstance(plans_result, BaseException) else []
    activity = activity_result[0] if not isinstance(activity_result, BaseException) else []
    achievements = achievements_result if not isinstance(achievements_result, BaseException) else []

    # A streak failure leaves the figure unknown rather than claiming zero, and does not degrade the
    # summary on its own: `None` already reads as "not measured" on this surface.
    reflection_streak = streak_result if not isinstance(streak_result, BaseException) else None
    if isinstance(streak_result, BaseException):
        _log_source_failure(user_id, "reflectionStreak", streak_result)

    # The summary reads from the sources already loaded rather than issuing its own queries, which is
    # what keeps the figure on the ring identical to the figure in the chart beneath it. The last
    # captured day is the freshest measurement there is — today has no snapshot yet by design.
    latest_point = trends.points[-1] if trends.points else None
    focused_minutes = (
        sum(point.focused_minutes or 0.0 for point in trends.points) if trends.points else None
    )

    return models.ReflectDashboardResponse(
        meta=models.ReflectDashboardMeta(
            generated_at=now,
            degraded_sections=[s for s in _SECTION_ORDER if s in degraded],
            range=range_,
        ),
        summary=models.ReflectSummaryStats(
            consistency_score=latest_point.consistency_score if latest_point else None,
            overall_mastery_percent=latest_point.mastery_percent if latest_point else None,
            focused_minutes=focused_minutes,
            active_days=trends.active_days if trends.points else None,
            reflection_streak=reflection_streak,
            goals_active=portfolio.active if portfolio else 0,
            goals_completed=portfolio.completed if portfolio else 0,
            goals_at_risk=portfolio.at_risk if portfolio else 0,
            goals_average_progress=portfolio.average_progress if portfolio else None,
        ),
        latest_reflection=latest_reflection,
        trends=trends,
        subjects=subjects,
        goals=goal_cards,
        study_plans=plans,
        activity=activity,
        achievements=achievements,
    )
