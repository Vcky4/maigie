"""
Progress domain — Data access layer (SQLAlchemy).

Queries for goals, schedule blocks, study sessions, streaks,
achievements, and review items (spaced repetition).
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory
from src.shared.field_mapping import map_fields

from .db_models import (
    Achievement,
    Goal,
    GoalLifecycleAction,
    GoalMilestone,
    GoalScheduleChange,
    ReviewItem,
    ScheduleBehaviourLog,
    ScheduleBlock,
    StudySession,
    UserStreak,
)

logger = logging.getLogger(__name__)


class ProgressRepository:
    """Data access for progress-related entities."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Goals
    # -----------------------------------------------------------------------

    async def find_goal(self, goal_id: str, user_id: str) -> Goal | None:
        async with await self._session() as session:
            stmt = select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_goals_for_prep(self, prep_id: str) -> list[Goal]:
        """Every goal attached to one preparation, whatever its status.

        Unscoped by learner on purpose: `Goal.prepId` is only ever set from a preparation the learner
        owns, and the caller — `prep_outcome_service` — has already loaded that preparation through a
        user-scoped read. It filters on `userId` itself rather than trusting that, which is belt and
        braces on a join that cannot currently cross learners.

        All statuses, because the caller needs to know a goal is already `COMPLETED` or `ARCHIVED` rather
        than resolving it a second time.
        """
        async with await self._session() as session:
            return list(
                (await session.execute(select(Goal).where(Goal.prep_id == prep_id))).scalars().all()
            )

    async def list_goals(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list[Goal], int]:
        async with await self._session() as session:
            conditions = [Goal.user_id == user_id]
            conditions.extend(self._build_goal_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Goal).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch
            stmt = select(Goal).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Goal, self._to_goal_attr(col_name), Goal.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())
            else:
                stmt = stmt.order_by(Goal.created_at.desc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def create_goal(self, data: dict[str, Any]) -> Goal:
        async with await self._session() as session:
            goal = Goal(**self._map_goal_data(data))
            session.add(goal)
            await session.commit()
            await session.refresh(goal)
            return goal

    async def update_goal(self, goal_id: str, data: dict[str, Any]) -> Goal:
        async with await self._session() as session:
            mapped = self._map_goal_data(data)
            stmt = update(Goal).where(Goal.id == goal_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        # Refetch to return updated object
        async with await self._session() as session:
            result = await session.execute(select(Goal).where(Goal.id == goal_id))
            return result.scalar_one()

    async def delete_goal(self, goal_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Goal).where(Goal.id == goal_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Goal schedule changes
    # -----------------------------------------------------------------------

    async def create_lifecycle_action(self, data: dict[str, Any]) -> GoalLifecycleAction:
        """Record what the nightly ladder did to a goal. Insert only.

        Written whether or not the notification that accompanied it survived quiet hours or the daily
        cap, because what is recorded is the decision, not the message. Counting only delivered messages
        would let a suppressed warning re-escalate the same goal every night.
        """
        async with await self._session() as session:
            action = GoalLifecycleAction(**self._map_lifecycle_action_data(data))
            session.add(action)
            await session.commit()
            await session.refresh(action)
            return action

    async def find_latest_lifecycle_action(self, goal_id: str) -> GoalLifecycleAction | None:
        """The most recent thing the ladder did to this goal, answered or not.

        The learner's reply attaches to this row. Latest rather than latest-unanswered, so a learner who
        changes their mind updates the answer they already gave instead of silently reaching past it to an
        older nudge that had already been dealt with.
        """
        async with await self._session() as session:
            stmt = (
                select(GoalLifecycleAction)
                .where(GoalLifecycleAction.goal_id == goal_id)
                .order_by(GoalLifecycleAction.created_at.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def record_lifecycle_response(
        self, action_id: str, *, response: str, responded_at: datetime
    ) -> None:
        """Attach the learner's reply to one action. Both halves together, or the CHECK refuses the row."""
        async with await self._session() as session:
            await session.execute(
                update(GoalLifecycleAction)
                .where(GoalLifecycleAction.id == action_id)
                .values(learner_response=response, responded_at=responded_at)
            )
            await session.commit()

    async def latest_unanswered_actions(self, goal_ids: list[str]) -> dict[str, str]:
        """`{goalId: action}` for goals whose most recent nudge is still unanswered, in one query.

        What lets a client render the question. Without it the only route to answering is the notification,
        and a notification can be held until morning, deferred to tomorrow by the learner's daily allowance,
        or expire — so the affordance has to exist somewhere the learner can find it on their own. The same
        argument put `AWAITING_REVIEW` on the prepare dashboard.

        Only the *most recent* action counts. An older unanswered nudge that has since been superseded is
        not a live question, and surfacing it would ask the learner about a decision the system has already
        moved past.
        """
        if not goal_ids:
            return {}

        latest = (
            select(
                GoalLifecycleAction.goal_id,
                func.max(GoalLifecycleAction.created_at).label("newest"),
            )
            .where(GoalLifecycleAction.goal_id.in_(goal_ids))
            .group_by(GoalLifecycleAction.goal_id)
            .subquery()
        )
        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(GoalLifecycleAction.goal_id, GoalLifecycleAction.action)
                    .join(
                        latest,
                        and_(
                            GoalLifecycleAction.goal_id == latest.c.goal_id,
                            GoalLifecycleAction.created_at == latest.c.newest,
                        ),
                    )
                    .where(GoalLifecycleAction.learner_response.is_(None))
                )
            ).all()
        return {goal_id: action for goal_id, action in rows}

    async def list_goals_for_lifecycle_review(
        self, *, now: datetime, horizon: datetime, not_acted_since: datetime, limit: int = 500
    ) -> list[Goal]:
        """Active dated goals close enough to their deadline to be worth a decision, off cooldown.

        **Bounded in SQL to the goals the ladder can actually act on.** Whether a goal is *at risk* is a
        question about derived progress and cannot be asked in SQL — it needs the measured current value,
        which is one query per metric kind. So the filter here is the part that is expressible: the two
        rungs that do anything both require the deadline to be near or past, and `horizon` is that line.
        A goal at risk with three months left gets no action from this pass, so loading it to measure it
        would be work spent to decide to do nothing.

        `targetDate IS NOT NULL`, because every rung is a statement about a deadline. A goal with no
        deadline cannot be behind one — the same rule `is_at_risk` applies when it returns `False` for an
        open-ended goal rather than calling slow progress a problem.

        The cooldown is a `NOT EXISTS` against `GoalLifecycleAction` rather than a column on the goal.
        A stamp on the row would say when the ladder last acted but not what it did, and "was this goal
        extended or merely warned" is what the next decision turns on.

        Ordered by deadline so that a fleet larger than ``limit`` serves the most urgent goals first.
        """
        recent_action = (
            select(GoalLifecycleAction.id)
            .where(
                GoalLifecycleAction.goal_id == Goal.id,
                GoalLifecycleAction.created_at >= not_acted_since,
            )
            .exists()
        )
        async with await self._session() as session:
            stmt = (
                select(Goal)
                .where(
                    Goal.status == "ACTIVE",
                    Goal.target_date.is_not(None),
                    Goal.target_date < horizon,
                    ~recent_action,
                )
                .order_by(Goal.target_date.asc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def create_schedule_change(self, data: dict[str, Any]) -> GoalScheduleChange:
        """Record that a goal's deadline moved.

        Insert only; there is no update or delete. An entry describes a moment that has passed, and a
        log that can be rewritten answers a different question from the one it is being asked.
        """
        async with await self._session() as session:
            change = GoalScheduleChange(**self._map_schedule_change_data(data))
            session.add(change)
            await session.commit()
            await session.refresh(change)
            return change

    # -----------------------------------------------------------------------
    # Goal milestones
    # -----------------------------------------------------------------------
    #
    # Every read and write is reached through the goal, and the goal is scoped to its owner by the
    # caller. A milestone has no `userId` of its own, so ownership is only ever established by way
    # of `find_goal(goal_id, user_id)` — which is why none of these methods takes a user id and why
    # the service must never call them without that check first.

    async def list_milestones(self, goal_id: str) -> list[GoalMilestone]:
        """A goal's milestones in the learner's chosen order."""
        async with await self._session() as session:
            stmt = (
                select(GoalMilestone)
                .where(GoalMilestone.goal_id == goal_id)
                .order_by(GoalMilestone.order_index.asc(), GoalMilestone.created_at.asc())
            )
            return list((await session.execute(stmt)).scalars().all())

    async def find_milestone(self, milestone_id: str, goal_id: str) -> GoalMilestone | None:
        """One milestone, scoped to its goal so an id from another goal cannot be reached."""
        async with await self._session() as session:
            stmt = select(GoalMilestone).where(
                GoalMilestone.id == milestone_id, GoalMilestone.goal_id == goal_id
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create_milestone(self, data: dict[str, Any]) -> GoalMilestone:
        async with await self._session() as session:
            milestone = GoalMilestone(**self._map_milestone_data(data))
            session.add(milestone)
            await session.commit()
            await session.refresh(milestone)
            return milestone

    async def update_milestone(self, milestone_id: str, data: dict[str, Any]) -> GoalMilestone:
        async with await self._session() as session:
            mapped = self._map_milestone_data(data)
            if mapped:
                await session.execute(
                    update(GoalMilestone).where(GoalMilestone.id == milestone_id).values(**mapped)
                )
                await session.commit()
        async with await self._session() as session:
            result = await session.execute(
                select(GoalMilestone).where(GoalMilestone.id == milestone_id)
            )
            return result.scalar_one()

    async def delete_milestone(self, milestone_id: str) -> None:
        async with await self._session() as session:
            await session.execute(delete(GoalMilestone).where(GoalMilestone.id == milestone_id))
            await session.commit()

    # -----------------------------------------------------------------------
    # Study Blocks (ScheduleBlock)
    # -----------------------------------------------------------------------

    async def find_block(self, block_id: str, user_id: str) -> ScheduleBlock | None:
        async with await self._session() as session:
            stmt = select(ScheduleBlock).where(
                ScheduleBlock.id == block_id, ScheduleBlock.user_id == user_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_blocks(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 50,
        order: dict | None = None,
    ) -> tuple[list[ScheduleBlock], int]:
        async with await self._session() as session:
            conditions = [ScheduleBlock.user_id == user_id]
            conditions.extend(self._build_block_conditions(where))

            count_stmt = select(func.count()).select_from(ScheduleBlock).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = select(ScheduleBlock).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(ScheduleBlock, self._to_block_attr(col_name), ScheduleBlock.start_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())
            else:
                stmt = stmt.order_by(ScheduleBlock.start_at.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def create_block(self, data: dict[str, Any]) -> ScheduleBlock:
        async with await self._session() as session:
            block = ScheduleBlock(**self._map_block_data(data))
            session.add(block)
            await session.commit()
            await session.refresh(block)
            return block

    async def update_block(self, block_id: str, data: dict[str, Any]) -> ScheduleBlock:
        async with await self._session() as session:
            mapped = self._map_block_data(data)
            stmt = update(ScheduleBlock).where(ScheduleBlock.id == block_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        async with await self._session() as session:
            result = await session.execute(
                select(ScheduleBlock).where(ScheduleBlock.id == block_id)
            )
            return result.scalar_one()

    async def delete_block(self, block_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(ScheduleBlock).where(ScheduleBlock.id == block_id)
            await session.execute(stmt)
            await session.commit()

    async def delete_blocks_for_goal(self, goal_id: str) -> int:
        """Delete all schedule blocks linked to a goal. Returns count deleted."""
        async with await self._session() as session:
            count_stmt = (
                select(func.count())
                .select_from(ScheduleBlock)
                .where(ScheduleBlock.goal_id == goal_id)
            )
            count = (await session.execute(count_stmt)).scalar() or 0
            stmt = delete(ScheduleBlock).where(ScheduleBlock.goal_id == goal_id)
            await session.execute(stmt)
            await session.commit()
            return count

    # -----------------------------------------------------------------------
    # Study Sessions
    # -----------------------------------------------------------------------

    async def find_active_session(self, user_id: str) -> StudySession | None:
        async with await self._session() as session:
            stmt = (
                select(StudySession)
                .where(StudySession.user_id == user_id, StudySession.end_time.is_(None))
                .order_by(StudySession.start_time.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def find_session(self, session_id: str) -> StudySession | None:
        async with await self._session() as session:
            stmt = select(StudySession).where(StudySession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_session(self, data: dict[str, Any]) -> StudySession:
        async with await self._session() as session:
            study_session = StudySession(**self._map_session_data(data))
            session.add(study_session)
            await session.commit()
            await session.refresh(study_session)
            return study_session

    async def update_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_session_data(data)
            stmt = update(StudySession).where(StudySession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def list_sessions(
        self, user_id: str, *, since: datetime | None = None, course_id: str | None = None
    ) -> list[StudySession]:
        async with await self._session() as session:
            conditions = [StudySession.user_id == user_id, StudySession.end_time.isnot(None)]
            if since:
                conditions.append(StudySession.start_time >= since)
            if course_id:
                conditions.append(StudySession.course_id == course_id)

            stmt = select(StudySession).where(*conditions).order_by(StudySession.start_time.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Streaks
    # -----------------------------------------------------------------------

    async def get_streak(self, user_id: str) -> UserStreak | None:
        async with await self._session() as session:
            stmt = select(UserStreak).where(UserStreak.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def upsert_streak(self, user_id: str, data: dict[str, Any]) -> UserStreak:
        async with await self._session() as session:
            stmt = select(UserStreak).where(UserStreak.user_id == user_id)
            result = await session.execute(stmt)
            streak = result.scalar_one_or_none()

            mapped = self._map_streak_data(data)
            if streak:
                for key, value in mapped.items():
                    setattr(streak, key, value)
                await session.commit()
                await session.refresh(streak)
                return streak
            else:
                streak = UserStreak(user_id=user_id, **mapped)
                session.add(streak)
                await session.commit()
                await session.refresh(streak)
                return streak

    # -----------------------------------------------------------------------
    # Achievements
    # -----------------------------------------------------------------------

    async def list_achievements(self, user_id: str) -> list[Achievement]:
        async with await self._session() as session:
            stmt = (
                select(Achievement)
                .where(Achievement.user_id == user_id)
                .order_by(Achievement.unlocked_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_achievement(self, data: dict[str, Any]) -> Achievement:
        async with await self._session() as session:
            achievement = Achievement(**self._map_achievement_data(data))
            session.add(achievement)
            await session.commit()
            await session.refresh(achievement)
            return achievement

    async def get_achievement_types(self, user_id: str) -> set:
        async with await self._session() as session:
            stmt = select(Achievement.achievement_type).where(Achievement.user_id == user_id)
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

    # -----------------------------------------------------------------------
    # Review Items (Spaced Repetition)
    # -----------------------------------------------------------------------

    async def list_due_reviews(
        self, user_id: str, *, before: datetime | None = None
    ) -> list[ReviewItem]:
        async with await self._session() as session:
            conditions = [ReviewItem.user_id == user_id]
            if before:
                conditions.append(ReviewItem.next_review_at <= before)
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(*conditions)
                .order_by(ReviewItem.next_review_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_review(self, review_id: str, user_id: str) -> ReviewItem | None:
        async with await self._session() as session:
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic), selectinload(ReviewItem.schedule_block))
                .where(ReviewItem.id == review_id, ReviewItem.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_review_by_topic(self, user_id: str, topic_id: str) -> ReviewItem | None:
        async with await self._session() as session:
            stmt = select(ReviewItem).where(
                ReviewItem.user_id == user_id, ReviewItem.topic_id == topic_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_review_item(self, data: dict[str, Any]) -> ReviewItem:
        async with await self._session() as session:
            review = ReviewItem(**self._map_review_data(data))
            session.add(review)
            await session.commit()
            await session.refresh(review)
            return review

    async def update_review(self, review_id: str, data: dict[str, Any]) -> ReviewItem:
        async with await self._session() as session:
            mapped = self._map_review_data(data)
            stmt = update(ReviewItem).where(ReviewItem.id == review_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()
        async with await self._session() as session:
            result = await session.execute(
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(ReviewItem.id == review_id)
            )
            return result.scalar_one()

    async def list_all_reviews(self, user_id: str) -> list[ReviewItem]:
        """All review items for a user (for stats)."""
        async with await self._session() as session:
            stmt = (
                select(ReviewItem)
                .options(selectinload(ReviewItem.topic))
                .where(ReviewItem.user_id == user_id)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Schedule Behaviour Logs
    # -----------------------------------------------------------------------

    async def create_behaviour_log(self, data: dict[str, Any]) -> ScheduleBehaviourLog:
        async with await self._session() as session:
            log = ScheduleBehaviourLog(**self._map_behaviour_data(data))
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    def _build_goal_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "status" in where:
            conditions.append(Goal.status == where["status"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Goal.space_id.is_(None))
            else:
                conditions.append(Goal.space_id == where["spaceId"])
        if "courseId" in where:
            conditions.append(Goal.course_id == where["courseId"])
        return conditions

    def _build_block_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "courseId" in where:
            conditions.append(ScheduleBlock.course_id == where["courseId"])
        if "goalId" in where:
            conditions.append(ScheduleBlock.goal_id == where["goalId"])
        if "endAt" in where and isinstance(where["endAt"], dict):
            if "gte" in where["endAt"]:
                conditions.append(ScheduleBlock.end_at >= where["endAt"]["gte"])
        if "startAt" in where and isinstance(where["startAt"], dict):
            if "lte" in where["startAt"]:
                conditions.append(ScheduleBlock.start_at <= where["startAt"]["lte"])
        return conditions

    _GOAL_FIELD_MAP = {
        "userId": "user_id",
        "title": "title",
        "description": "description",
        "targetDate": "target_date",
        "status": "status",
        "progress": "progress",
        "courseId": "course_id",
        "topicId": "topic_id",
        "spaceId": "space_id",
        "prepId": "prep_id",
        # What the goal measures and against what. Absent from this map, `map_fields` refuses the
        # write and names the mapper — which is the guard working, and would have meant four columns
        # migration 042 added being unreachable through the API.
        "metricKind": "metric_kind",
        "targetValue": "target_value",
        "unit": "unit",
        # Accepted here because a `manual` goal's current value is the learner's own figure. The
        # service refuses it for every other `metricKind`, where the value is derived on read —
        # storing one would create a second version of a number that already exists.
        "currentValue": "current_value",
    }

    #: A log row is assembled by the service, not by a client — no route accepts one. The map is here
    #: anyway so the write goes through the same allowlist as every other, which is what makes adding a
    #: column to the table without wiring it a loud failure rather than a silent drop.
    _SCHEDULE_CHANGE_FIELD_MAP = {
        "goalId": "goal_id",
        "userId": "user_id",
        "previousDate": "previous_date",
        "newDate": "new_date",
        "reason": "reason",
        "dateAuthority": "date_authority",
    }

    #: Assembled by the ladder, never by a client. Mapped anyway so a column added to the table without
    #: being wired here fails loudly rather than being dropped.
    _LIFECYCLE_ACTION_FIELD_MAP = {
        "goalId": "goal_id",
        "userId": "user_id",
        "action": "action",
        "trigger": "trigger",
        # Never set at creation: the learner cannot have answered a question that has not been asked.
        # Written by `record_lifecycle_response`, and mapped here so adding a column without wiring it
        # fails loudly rather than being dropped.
        "learnerResponse": "learner_response",
        "respondedAt": "responded_at",
    }

    _MILESTONE_FIELD_MAP = {
        "goalId": "goal_id",
        "title": "title",
        "detail": "detail",
        "targetValue": "target_value",
        "orderIndex": "order_index",
        "achievedAt": "achieved_at",
    }

    _BLOCK_FIELD_MAP = {
        "userId": "user_id",
        "title": "title",
        "description": "description",
        "startAt": "start_at",
        "endAt": "end_at",
        "recurringRule": "recurring_rule",
        "googleCalendarEventId": "google_calendar_event_id",
        "googleCalendarSyncedAt": "google_calendar_synced_at",
        "courseId": "course_id",
        "topicId": "topic_id",
        "goalId": "goal_id",
        "reviewItemId": "review_item_id",
        "examPrepId": "exam_prep_id",
        "completedAt": "completed_at",
        "startedAt": "started_at",
    }

    _SESSION_FIELD_MAP = {
        "userId": "user_id",
        "startTime": "start_time",
        "endTime": "end_time",
        "duration": "duration",
        "courseId": "course_id",
        "topicId": "topic_id",
        # No `spaceId`. Removed with the column in migration 032: a mapping for a field nothing sends,
        # onto a column that no longer exists, is two ways to be wrong at once.
        "metadata": "metadata_json",
    }

    _STREAK_FIELD_MAP = {
        "currentStreak": "current_streak",
        "longestStreak": "longest_streak",
        "lastStudyDate": "last_study_date",
    }

    _REVIEW_FIELD_MAP = {
        "userId": "user_id",
        "topicId": "topic_id",
        "nextReviewAt": "next_review_at",
        "intervalDays": "interval_days",
        "repetitionCount": "repetition_count",
        "easeFactor": "ease_factor",
        "lastQuality": "last_quality",
        "lapseCount": "lapse_count",
        "lastReviewedAt": "last_reviewed_at",
    }

    _ACHIEVEMENT_FIELD_MAP = {
        "userId": "user_id",
        "achievementType": "achievement_type",
        "title": "title",
        "description": "description",
        "icon": "icon",
        "metadata": "metadata_json",
        "unlockedAt": "unlocked_at",
    }

    _BEHAVIOUR_FIELD_MAP = {
        "userId": "user_id",
        "behaviourType": "behaviour_type",
        "entityType": "entity_type",
        "entityId": "entity_id",
        "scheduledAt": "scheduled_at",
        "actualAt": "actual_at",
        "metadata": "metadata_json",
    }

    def _map_goal_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._GOAL_FIELD_MAP, entity="_map_goal_data")

    def _map_schedule_change_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SCHEDULE_CHANGE_FIELD_MAP, entity="_map_schedule_change_data")

    def _map_lifecycle_action_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(
            data, self._LIFECYCLE_ACTION_FIELD_MAP, entity="_map_lifecycle_action_data"
        )

    def _map_milestone_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._MILESTONE_FIELD_MAP, entity="_map_milestone_data")

    def _map_block_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._BLOCK_FIELD_MAP, entity="_map_block_data")

    def _map_session_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SESSION_FIELD_MAP, entity="_map_session_data")

    def _map_streak_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._STREAK_FIELD_MAP, entity="_map_streak_data")

    def _map_review_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._REVIEW_FIELD_MAP, entity="_map_review_data")

    def _map_achievement_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._ACHIEVEMENT_FIELD_MAP, entity="_map_achievement_data")

    def _map_behaviour_data(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._BEHAVIOUR_FIELD_MAP, entity="_map_behaviour_data")

    def _to_goal_attr(self, col_name: str) -> str:
        return self._GOAL_FIELD_MAP.get(col_name, col_name)

    def _to_block_attr(self, col_name: str) -> str:
        return self._BLOCK_FIELD_MAP.get(col_name, col_name)


# Singleton
progress_repo = ProgressRepository()
