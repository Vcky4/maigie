"""
Knowledge domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Course, Module, Topic, Resource.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from src.shared.database import get_session_factory
from src.shared.field_mapping import map_fields

from .db_models import (
    Course,
    CourseOutlineSatisfaction,
    CourseRating,
    Module,
    Resource,
    Topic,
    TopicSection,
)

logger = logging.getLogger(__name__)


class KnowledgeRepository:
    """Data access for courses, modules, topics, and resources."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    async def get_course_dashboard_stats(self, user_id: str) -> tuple[int, int]:
        """Return unarchived course and completed-topic counts for one user."""
        async with await self._session() as session:
            course_count_stmt = (
                select(func.count())
                .select_from(Course)
                .where(Course.user_id == user_id, Course.archived.is_(False))
            )
            completed_topics_stmt = (
                select(func.count())
                .select_from(Topic)
                .join(Module, Topic.module_id == Module.id)
                .join(Course, Module.course_id == Course.id)
                .where(
                    Course.user_id == user_id,
                    Course.archived.is_(False),
                    Topic.completed.is_(True),
                )
            )
            active_courses = (await session.execute(course_count_stmt)).scalar_one() or 0
            completed_topics = (await session.execute(completed_topics_stmt)).scalar_one() or 0
            return active_courses, completed_topics

    # -----------------------------------------------------------------------
    # Courses
    # -----------------------------------------------------------------------

    async def find_course(self, course_id: str, user_id: str) -> Course | None:
        async with await self._session() as session:
            stmt = select(Course).where(Course.id == course_id, Course.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_course_with_modules(self, course_id: str, user_id: str) -> Course | None:
        async with await self._session() as session:
            stmt = (
                select(Course)
                .options(selectinload(Course.modules).selectinload(Module.topics))
                .where(Course.id == course_id, Course.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_courses(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        order: dict | None = None,
    ) -> tuple[list[Course], int]:
        async with await self._session() as session:
            conditions = [Course.user_id == user_id]
            conditions.extend(self._build_course_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Course).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch. Deliberately without the modules and topics: the list response carries counts,
            # not rows, and `course_progress_totals` produces them in one grouped query. Loading
            # every topic of every course on the page to call `len()` on them is what this replaces.
            stmt = select(Course).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Course, self._to_attr(col_name), Course.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def topic_position(self, course_id: str, topic_id: str) -> tuple[int, int]:
        """``(position, total)`` of a topic among its course's topics, in outline order.

        Ordered by module then topic order, which is how the outline reads, and tie-broken by id so a
        course with two topics sharing an order does not renumber itself between requests.

        `(0, total)` when the topic is not in the course, which the caller has already ruled out by
        ownership — reported rather than raised, so a numbering quirk cannot 500 a page that had
        everything else it needed.
        """
        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(Topic.id)
                    .join(Module, Topic.module_id == Module.id)
                    .where(Module.course_id == course_id)
                    .order_by(Module.order.asc(), Topic.order.asc(), Topic.id.asc())
                )
            ).scalars().all()

        ids = list(rows)
        return (ids.index(topic_id) + 1 if topic_id in ids else 0), len(ids)

    async def recount_course_progress(self, course_id: str) -> float:
        """Recompute a course's progress from its topics, and store it.

        `Course.progress` existed and **nothing wrote it**, so it was `0` for every course ever
        created while the true figure was recomputed per request by whoever needed it. That is not a
        harmless unused column: two readers outside this domain took it at face value — the assigned
        courses list a classroom shows, and the course summary handed to the model as memory context.
        Both reported every course as 0% complete.

        Derived and stored, rather than incremented on completion, for the reason
        `recount_plan_progress` gives: an increment cannot express uncompleting, and it drifts the
        moment a topic is added or removed. Recomputing removes the class of bug rather than the
        instance, so whatever happened to the topics, the stored figure is what they say.

        Returns the stored percentage.
        """
        async with await self._session() as session:
            row = (
                await session.execute(
                    select(
                        func.count(Topic.id),
                        func.count(Topic.id).filter(Topic.completed.is_(True)),
                    )
                    .select_from(Topic)
                    .join(Module, Topic.module_id == Module.id)
                    .where(Module.course_id == course_id)
                )
            ).one()
            total, completed = int(row[0] or 0), int(row[1] or 0)
            # Rounded to one decimal place, matching `calculate_course_progress` and the list
            # endpoint, so the stored value and the computed one cannot disagree in the last digit.
            progress = round(completed / total * 100, 1) if total else 0.0
            await session.execute(
                update(Course).where(Course.id == course_id).values(progress=progress)
            )
            await session.commit()
            return progress

    async def library_topic_totals(self, user_id: str) -> tuple[int, int]:
        """``(total_topics, completed_topics)`` across all of the learner's unarchived courses.

        Library-wide, so the figure describes what the learner is working through rather than the page
        that happens to be loaded. Archived courses are excluded for the same reason they are excluded
        from the default list: archiving is the learner saying "not now", and counting shelved work
        towards their progress ignores that.
        """
        async with await self._session() as session:
            row = (
                await session.execute(
                    select(
                        func.count(Topic.id),
                        func.count(Topic.id).filter(Topic.completed.is_(True)),
                    )
                    .select_from(Topic)
                    .join(Module, Topic.module_id == Module.id)
                    .join(Course, Module.course_id == Course.id)
                    .where(Course.user_id == user_id, Course.archived.is_(False))
                )
            ).one()
        return int(row[0] or 0), int(row[1] or 0)

    async def completed_topic_dates(
        self, user_id: str, *, since: datetime | None = None
    ) -> list[datetime]:
        """When this learner completed topics, newest first.

        The source for a course streak and for "this week". Topics completed before `completedAt`
        existed have no time and are excluded — they count towards progress, which is all that is
        actually known about them.
        """
        conditions = [
            Course.user_id == user_id,
            Topic.completed.is_(True),
            Topic.completed_at.is_not(None),
        ]
        if since is not None:
            conditions.append(Topic.completed_at >= since)

        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(Topic.completed_at)
                    .join(Module, Topic.module_id == Module.id)
                    .join(Course, Module.course_id == Course.id)
                    .where(*conditions)
                    .order_by(Topic.completed_at.desc())
                )
            ).scalars().all()
        return [row for row in rows if row is not None]

    async def completed_hours_between(
        self, user_id: str, start: datetime, end: datetime
    ) -> float:
        """Estimated hours on the topics this learner completed in a window.

        Estimated, not measured. Nothing observes how long a topic actually took, so a caller must
        label this as planned effort — the same wording study plans use for the same reason. Topics
        with no estimate contribute nothing rather than a guess.
        """
        async with await self._session() as session:
            total = (
                await session.execute(
                    select(func.sum(Topic.estimated_hours))
                    .join(Module, Topic.module_id == Module.id)
                    .join(Course, Module.course_id == Course.id)
                    .where(
                        Course.user_id == user_id,
                        Topic.completed.is_(True),
                        Topic.completed_at.is_not(None),
                        Topic.completed_at >= start,
                        Topic.completed_at < end,
                    )
                )
            ).scalar()
        return float(total or 0.0)

    async def recently_completed_topics(
        self, user_id: str, *, limit: int = 5
    ) -> list[tuple[Topic, str, str]]:
        """The learner's most recently completed topics as ``(topic, course_id, course_title)``.

        The course is joined in rather than fetched per row, which is what "recently active" needs to
        say where each item came from — a list of topic titles with no course beside them is not
        readable.
        """
        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(Topic, Course.id, Course.title)
                    .join(Module, Topic.module_id == Module.id)
                    .join(Course, Module.course_id == Course.id)
                    .where(
                        Course.user_id == user_id,
                        Topic.completed.is_(True),
                        Topic.completed_at.is_not(None),
                    )
                    .order_by(Topic.completed_at.desc(), Topic.id.desc())
                    .limit(limit)
                )
            ).all()
        return [(topic, course_id, title) for topic, course_id, title in rows]

    async def next_topics(self, course_ids: list[str]) -> dict[str, Topic]:
        """The next incomplete topic of each course, in outline order.

        What a library card means by "Up next". One row per course through a window function rather
        than a query per course, because a page of twenty courses would otherwise be twenty round
        trips to print twenty lines of text.

        Ordered by module then topic order, which is the order the outline reads in, and tie-broken by
        id so a course with two topics sharing an order does not change its "Up next" between
        requests. Courses with nothing incomplete are absent: the course is finished, and that wants
        a different label rather than a blank one.
        """
        if not course_ids:
            return {}

        ranked = (
            select(
                Topic,
                Module.course_id.label("course_id"),
                func.row_number()
                .over(
                    partition_by=Module.course_id,
                    order_by=(Module.order.asc(), Topic.order.asc(), Topic.id.asc()),
                )
                .label("rank"),
            )
            .join(Module, Topic.module_id == Module.id)
            .where(Module.course_id.in_(course_ids), Topic.completed.is_(False))
            .subquery()
        )
        entity = aliased(Topic, ranked)

        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(entity, ranked.c.course_id).where(ranked.c.rank == 1)
                )
            ).all()

        return {course_id: topic for topic, course_id in rows}

    async def remaining_hours(self, course_ids: list[str]) -> dict[str, float]:
        """Estimated hours left on each course: the sum over its incomplete topics.

        Separate from `course_progress_totals` because it sums a nullable column and that one counts
        rows — folding them together would make a course whose topics carry no estimate
        indistinguishable from one with nothing left, since `SUM` over no rows is null either way.

        Courses with no estimate on any remaining topic are absent, so the caller can say "no estimate"
        rather than printing a confident `0h` for work that has simply never been sized.
        """
        if not course_ids:
            return {}

        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(Module.course_id, func.sum(Topic.estimated_hours))
                    .join(Topic, Topic.module_id == Module.id)
                    .where(
                        Module.course_id.in_(course_ids),
                        Topic.completed.is_(False),
                        Topic.estimated_hours.is_not(None),
                    )
                    .group_by(Module.course_id)
                )
            ).all()

        return {course_id: float(total) for course_id, total in rows if total is not None}

    async def course_progress_totals(
        self, course_ids: list[str]
    ) -> dict[str, tuple[int, int, int]]:
        """``(module_count, total_topics, completed_topics)`` per course, in one query.

        The library used to get these by calling `calculate_course_progress` for each course on the
        page, which issues its own `list_modules` query — so a page of twenty courses was twenty-one
        round trips to print sixty numbers. Worse, the page query *also* eager-loaded every module
        and every topic to count them, so the rows were fetched twice and thrown away both times.

        A `LEFT JOIN`, so a module with no topics still counts towards `module_count`; and
        `COUNT(DISTINCT)` on the module id, or a module with three topics would count three times.

        Courses with no modules are absent from the result rather than present as zeros — the caller
        already has to handle a course that is not in the map, and inventing rows for them here would
        mean two places deciding what "no modules" looks like.
        """
        if not course_ids:
            return {}

        async with await self._session() as session:
            rows = (
                await session.execute(
                    select(
                        Module.course_id,
                        func.count(func.distinct(Module.id)),
                        func.count(Topic.id),
                        func.count(Topic.id).filter(Topic.completed.is_(True)),
                    )
                    .select_from(Module)
                    .outerjoin(Topic, Topic.module_id == Module.id)
                    .where(Module.course_id.in_(course_ids))
                    .group_by(Module.course_id)
                )
            ).all()

        return {
            course_id: (int(modules or 0), int(total or 0), int(completed or 0))
            for course_id, modules, total, completed in rows
        }

    async def create_course(self, data: dict[str, Any]) -> Course:
        async with await self._session() as session:
            course = Course(**self._map_course_data(data))
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course

    async def update_course(self, course_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = (
                update(Course).where(Course.id == course_id).values(**self._map_course_data(data))
            )
            await session.execute(stmt)
            await session.commit()

    async def delete_course(self, course_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Course).where(Course.id == course_id)
            await session.execute(stmt)
            await session.commit()

    async def count_courses(self, where: dict[str, Any]) -> int:
        async with await self._session() as session:
            conditions = self._build_course_conditions(where)
            stmt = (
                select(func.count()).select_from(Course).where(*conditions)
                if conditions
                else select(func.count()).select_from(Course)
            )
            return (await session.execute(stmt)).scalar() or 0

    # -----------------------------------------------------------------------
    # Modules
    # -----------------------------------------------------------------------

    async def find_module(self, module_id: str) -> Module | None:
        async with await self._session() as session:
            stmt = select(Module).options(selectinload(Module.course)).where(Module.id == module_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_module_with_topics(self, module_id: str) -> Module | None:
        async with await self._session() as session:
            stmt = (
                select(Module)
                .options(selectinload(Module.topics), selectinload(Module.course))
                .where(Module.id == module_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_modules(self, course_id: str) -> list[Module]:
        async with await self._session() as session:
            stmt = (
                select(Module)
                .options(selectinload(Module.topics))
                .where(Module.course_id == course_id)
                .order_by(Module.order.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_module(self, data: dict[str, Any]) -> Module:
        async with await self._session() as session:
            module = Module(
                course_id=data.get("courseId"),
                title=data["title"],
                order=data.get("order", 0),
                description=data.get("description"),
            )
            session.add(module)
            await session.commit()
            await session.refresh(module)
            return module

    async def update_module(self, module_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = {}
            if "title" in data:
                mapped["title"] = data["title"]
            if "order" in data:
                mapped["order"] = data["order"]
            if "description" in data:
                mapped["description"] = data["description"]
            if mapped:
                stmt = update(Module).where(Module.id == module_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

    async def delete_module(self, module_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Module).where(Module.id == module_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Topics
    # -----------------------------------------------------------------------

    async def find_topic(self, topic_id: str) -> Topic | None:
        async with await self._session() as session:
            stmt = (
                select(Topic)
                .options(selectinload(Topic.module).selectinload(Module.course))
                .where(Topic.id == topic_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_topic(self, data: dict[str, Any]) -> Topic:
        async with await self._session() as session:
            topic = Topic(
                module_id=data.get("moduleId"),
                title=data["title"],
                order=data.get("order", 0),
                content=data.get("content"),
                estimated_hours=data.get("estimatedHours"),
                summary=data.get("summary"),
                objectives=data.get("objectives"),
                knowledge_check=data.get("knowledgeCheck"),
            )
            session.add(topic)
            await session.commit()
            await session.refresh(topic)
            return topic

    async def update_topic(self, topic_id: str, data: dict[str, Any]) -> Topic:
        async with await self._session() as session:
            mapped = {}
            if "title" in data:
                mapped["title"] = data["title"]
            if "order" in data:
                mapped["order"] = data["order"]
            if "content" in data:
                mapped["content"] = data["content"]
            if "estimatedHours" in data:
                mapped["estimated_hours"] = data["estimatedHours"]
            if "completed" in data:
                mapped["completed"] = data["completed"]
            # `in data`, not truthiness: clearing this on reopen means writing `None`, and a
            # truthiness check would skip that and leave a pending topic looking completed.
            if "completedAt" in data:
                mapped["completed_at"] = data["completedAt"]
            # Same `in data` reasoning: an explicit null clears the objectives or the check, and a
            # truthiness test would silently ignore the clear and leave the old value on the page.
            if "summary" in data:
                mapped["summary"] = data["summary"]
            if "objectives" in data:
                mapped["objectives"] = data["objectives"]
            if "knowledgeCheck" in data:
                mapped["knowledge_check"] = data["knowledgeCheck"]
            if mapped:
                stmt = update(Topic).where(Topic.id == topic_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()
            # Refetch
            return await self.find_topic(topic_id)

    async def delete_topic(self, topic_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Topic).where(Topic.id == topic_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Topic sections
    # -----------------------------------------------------------------------

    async def list_topic_sections(self, topic_id: str) -> list[TopicSection]:
        """The sections of one topic in reading order.

        Ordered by `order` then `id`, not `order` alone: two sections can legitimately share an order
        after a bulk insert that did not spread them, and an unstable sort would let the lesson
        reshuffle itself between two loads of the same page.
        """
        async with await self._session() as session:
            rows = await session.execute(
                select(TopicSection)
                .where(TopicSection.topic_id == topic_id)
                .order_by(TopicSection.order, TopicSection.id)
            )
            return list(rows.scalars().all())

    async def find_topic_section(self, section_id: str) -> TopicSection | None:
        async with await self._session() as session:
            rows = await session.execute(
                select(TopicSection).where(TopicSection.id == section_id)
            )
            return rows.scalar_one_or_none()

    async def create_topic_section(self, data: dict[str, Any]) -> TopicSection:
        async with await self._session() as session:
            section = TopicSection(**self._map_section_data(data))
            session.add(section)
            await session.commit()
            await session.refresh(section)
            return section

    async def create_topic_sections(self, topic_id: str, items: list[dict[str, Any]]) -> int:
        """Insert a whole lesson's sections in one transaction.

        Generation produces every section of a topic at once, and inserting them one request at a time
        would leave a half-written lesson on screen if any single insert failed. Returns the count so
        the caller reports what it wrote rather than assuming.
        """
        if not items:
            return 0

        async with await self._session() as session:
            session.add_all(
                [
                    TopicSection(**self._map_section_data({**item, "topicId": topic_id}))
                    for item in items
                ]
            )
            await session.commit()
        return len(items)

    async def update_topic_section(self, section_id: str, data: dict[str, Any]) -> TopicSection | None:
        async with await self._session() as session:
            mapped = self._map_section_data(data)
            if mapped:
                await session.execute(
                    update(TopicSection).where(TopicSection.id == section_id).values(**mapped)
                )
                await session.commit()
        return await self.find_topic_section(section_id)

    async def delete_topic_section(self, section_id: str) -> None:
        async with await self._session() as session:
            await session.execute(delete(TopicSection).where(TopicSection.id == section_id))
            await session.commit()

    async def delete_topic_sections(self, topic_id: str) -> None:
        """Clear a topic's sections. Used when regeneration replaces a lesson wholesale, so the new
        body cannot end up appended to the old one."""
        async with await self._session() as session:
            await session.execute(delete(TopicSection).where(TopicSection.topic_id == topic_id))
            await session.commit()

    async def set_topic_section_completed(
        self, section_id: str, completed: bool
    ) -> TopicSection | None:
        """Mark a section done or reopen it, writing the timestamp in the same statement.

        `completedAt` is set from the database clock on completion and cleared on reopen, so a pending
        section can never carry a completion time — the same contract as `Topic.completedAt`.
        """
        async with await self._session() as session:
            await session.execute(
                update(TopicSection)
                .where(TopicSection.id == section_id)
                .values(
                    completed=completed,
                    completed_at=func.now() if completed else None,
                )
            )
            await session.commit()
        return await self.find_topic_section(section_id)

    def _map_section_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Translate wire names to attribute names for the three that differ.

        An explicit allowlist rather than a passthrough: a section has ten optional content fields and
        a typo in any of them would otherwise reach the constructor as an unknown keyword — or, on the
        update path, be silently dropped. Membership is tested with `in`, so an explicit null clears a
        field instead of being mistaken for "not sent".
        """
        field_map = {
            "topicId": "topic_id",
            "order": "order",
            "kind": "kind",
            "title": "title",
            "eyebrow": "eyebrow",
            "summary": "summary",
            "durationMinutes": "duration_minutes",
            "paragraphs": "paragraphs",
            "keyIdea": "key_idea",
            "steps": "steps",
            "bullets": "bullets",
            "code": "code",
            "completed": "completed",
        }
        return map_fields(data, field_map, entity="_map_section_data")

    # -----------------------------------------------------------------------
    # Course ratings
    # -----------------------------------------------------------------------

    async def rate_course(
        self, course_id: str, user_id: str, value: int, comment: str | None = None
    ) -> CourseRating:
        """Record or change one learner's rating of a course.

        An upsert on the `(courseId, userId)` unique constraint rather than a read-then-write: two
        submissions racing would both find no existing row and the second insert would abort the
        transaction, so the learner would see a failure for having clicked twice.
        """
        async with await self._session() as session:
            stmt = (
                pg_insert(CourseRating)
                .values(
                    course_id=course_id,
                    user_id=user_id,
                    value=value,
                    comment=comment,
                )
                .on_conflict_do_update(
                    constraint="CourseRating_courseId_userId_key",
                    set_={"value": value, "comment": comment, "updatedAt": func.now()},
                )
                .returning(CourseRating)
            )
            row = (await session.execute(stmt)).scalar_one()
            await session.commit()
            return row

    async def course_rating_summary(
        self, course_id: str, user_id: str | None = None
    ) -> tuple[float | None, int, int | None]:
        """`(average, count, this learner's rating)` for one course.

        The average is null rather than zero when nobody has rated it, because the page distinguishes
        "unrated" from "rated badly" and only one of those is ever true of a new course. Rounded to
        one decimal place, matching how the figure is printed, so the stored aggregate cannot disagree
        with the rendered one in the last digit.
        """
        async with await self._session() as session:
            row = (
                await session.execute(
                    select(func.avg(CourseRating.value), func.count(CourseRating.id)).where(
                        CourseRating.course_id == course_id
                    )
                )
            ).one()
            average, count = row[0], int(row[1] or 0)

            mine: int | None = None
            if user_id is not None:
                mine = (
                    await session.execute(
                        select(CourseRating.value).where(
                            CourseRating.course_id == course_id,
                            CourseRating.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()

        return (round(float(average), 1) if average is not None else None, count, mine)

    # -----------------------------------------------------------------------
    # Resources
    # -----------------------------------------------------------------------

    async def list_resources(
        self, *, where: dict[str, Any], skip: int = 0, take: int = 20, order: dict | None = None
    ) -> tuple[list[Resource], int]:
        async with await self._session() as session:
            conditions = self._build_resource_conditions(where)

            count_stmt = (
                select(func.count()).select_from(Resource).where(*conditions)
                if conditions
                else select(func.count()).select_from(Resource)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = select(Resource).where(*conditions).offset(skip).limit(take)
            if order:
                col_name, direction = next(iter(order.items()))
                col = getattr(Resource, self._to_attr(col_name), Resource.created_at)
                stmt = stmt.order_by(col.desc() if direction == "desc" else col.asc())

            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    async def find_resource(self, resource_id: str, user_id: str) -> Resource | None:
        async with await self._session() as session:
            stmt = select(Resource).where(Resource.id == resource_id, Resource.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_resource(self, data: dict[str, Any]) -> Resource:
        async with await self._session() as session:
            resource = Resource(
                user_id=data["userId"],
                title=data["title"],
                url=data["url"],
                type=data.get("type", "OTHER"),
                description=data.get("description"),
                metadata_json=data.get("metadata"),
                is_recommended=data.get("isRecommended", False),
                recommendation_score=data.get("recommendationScore"),
                recommendation_source=data.get("recommendationSource"),
                course_id=data.get("courseId"),
                topic_id=data.get("topicId"),
                space_id=data.get("spaceId"),
            )
            session.add(resource)
            await session.commit()
            await session.refresh(resource)
            return resource

    async def update_resource(self, resource_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = update(Resource).where(Resource.id == resource_id).values(**data)
            await session.execute(stmt)
            await session.commit()

    async def delete_resource(self, resource_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Resource).where(Resource.id == resource_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Outline Satisfaction
    # -----------------------------------------------------------------------

    async def record_outline_satisfaction(self, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            record = CourseOutlineSatisfaction(
                user_id=data["userId"],
                course_id=data["courseId"],
                kind=data["kind"],
                feedback=data.get("feedback"),
            )
            session.add(record)
            await session.commit()

    async def has_outline_satisfaction(self, user_id: str, course_id: str) -> bool:
        async with await self._session() as session:
            stmt = (
                select(func.count())
                .select_from(CourseOutlineSatisfaction)
                .where(
                    CourseOutlineSatisfaction.user_id == user_id,
                    CourseOutlineSatisfaction.course_id == course_id,
                )
            )
            count = (await session.execute(stmt)).scalar() or 0
            return count > 0

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_course_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "userId" in where:
            conditions.append(Course.user_id == where["userId"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Course.space_id.is_(None))
            else:
                conditions.append(Course.space_id == where["spaceId"])
        if "archived" in where:
            conditions.append(Course.archived == where["archived"])
        if "difficulty" in where:
            conditions.append(Course.difficulty == where["difficulty"])
        if "isAIGenerated" in where:
            conditions.append(Course.is_ai_generated == where["isAIGenerated"])
        if where.get("search"):
            # A plain term matched against title and description, rather than the Prisma-shaped
            # `{"OR": [{"title": {"contains": ...}}]}` the route used to build — which nothing here
            # understood, so `search` was accepted and dropped without a trace.
            #
            # `ilike` with the term escaped, so a title containing `%` or `_` is searched for
            # literally instead of turning into a wildcard that matches most of the library.
            pattern = f"%{self._escape_like(where['search'])}%"
            conditions.append(
                or_(
                    Course.title.ilike(pattern, escape="\\"),
                    Course.description.ilike(pattern, escape="\\"),
                )
            )
        if "createdAt" in where and isinstance(where["createdAt"], dict):
            gte = where["createdAt"].get("gte")
            if gte:
                conditions.append(Course.created_at >= gte)
        return conditions

    def _build_resource_conditions(self, where: dict[str, Any]) -> list:
        conditions = []
        if "userId" in where:
            conditions.append(Resource.user_id == where["userId"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Resource.space_id.is_(None))
            else:
                conditions.append(Resource.space_id == where["spaceId"])
        if "topicId" in where:
            conditions.append(Resource.topic_id == where["topicId"])
        if "courseId" in where:
            conditions.append(Resource.course_id == where["courseId"])
        if "type" in where:
            conditions.append(Resource.type == where["type"])
        return conditions

    def _map_course_data(self, data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "isAIGenerated": "is_ai_generated",
            "targetDate": "target_date",
            "spaceId": "space_id",
            "instructorName": "instructor_name",
            "instructorRole": "instructor_role",
        }
        # `category`, `tags` and `outcomes` are absent from the map on purpose: their column names and
        # their attribute names are the same word, so the passthrough below is already correct for
        # them. Anything whose wire name differs from its attribute name must be listed, or it
        # arrives here as an unknown keyword and fails at the constructor.
        # Previously a passthrough: an unknown key became an ORM keyword argument and failed with a
        # `TypeError` naming SQLAlchemy internals rather than the field the caller sent. Strict mapping
        # names the field instead, and refuses rather than guessing.
        for own_name in ("title", "description", "difficulty", "archived", "progress",
                         "category", "tags", "outcomes"):
            field_map.setdefault(own_name, own_name)
        return map_fields(data, field_map, entity="_map_course_data")

    @staticmethod
    def _escape_like(term: str) -> str:
        """Escape the wildcards `LIKE` treats as syntax.

        Without this, a learner searching for `100%` matches everything, and one searching for `a_b`
        matches `axb` — the search silently returns the wrong rows rather than failing, which is the
        harder kind of wrong to notice. The backslash is escaped first, or it would double-escape
        the escapes added after it.
        """
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _to_attr(self, col_name: str) -> str:
        mapping = {
            "createdAt": "created_at",
            "updatedAt": "updated_at",
        }
        return mapping.get(col_name, col_name)


# Singleton
knowledge_repo = KnowledgeRepository()
