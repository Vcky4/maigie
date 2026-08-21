"""
Personal Learning domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Notes, NoteTag, NoteAttachment,
ExamPrep, and GeneratedDocument.

Session management:
    - All public methods accept an optional `session: AsyncSession | None` parameter.
    - When provided: the caller owns the transaction (no commit/rollback here).
    - When None: a new session is created and committed/rolled back automatically.
    - Use `unit_of_work()` context manager for multi-operation transactions.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, noload, selectinload

from src.shared.database import get_session_factory, ilike_any
from src.shared.field_mapping import map_fields

from .db_models import (
    ActivityFeedEntry,
    Collection,
    CollectionItem,
    DailyLearningSnapshot,
    DiscoveryRecommendation,
    ExamPrep,
    Flashcard,
    FlashcardDeck,
    FlashcardReview,
    GeneratedDocument,
    LearningProfile,
    Note,
    NoteAttachment,
    NoteHistory,
    NoteTag,
    Notification,
    PracticeObservation,
    PrepMaterial,
    PrepQuestion,
    PrepQuestionFlag,
    PrepReadinessSnapshot,
    PrepTopic,
    QuizAnswer,
    QuizSession,
    QuizSessionQuestion,
    Reflection,
    ReflectionNote,
    SavedResource,
    StudyPlan,
    StudyPlanCourse,
    StudyPlanItem,
    StudyPlanMaterial,
)

logger = logging.getLogger(__name__)

#: Interval at which a card counts as mastered, in days.
#:
#: One definition, in one place, because there were two. This layer used
#: ``intervalDays > 21`` while the web deck page used ``repetitionCount >= 5 and
#: intervalDays >= 21``, so the same library could be reported as 62% mastered on the
#: dashboard and 48% on the deck page. Twenty-one days is the conventional SM-2
#: maturity boundary; the comparison is inclusive because a card scheduled exactly 21
#: days out has reached it, and the previous strict ``>`` excluded it for no stated
#: reason.
MASTERED_INTERVAL_DAYS = 21


def _as_date(value: Any) -> date | None:
    """Coerce whatever the driver returned for a date expression into a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _local_day(column: Any, timezone_name: str | None) -> Any:
    """A SQL expression for the calendar day an instant fell on, in a given zone.

    Day boundaries are the reason this exists. Grouping a ``timestamptz`` by its UTC
    date puts a learner in Auckland or Los Angeles into the wrong day for part of
    every day, which silently corrupts streaks and "this week" counts — the same
    defect ``shared.time.learner_timezone`` was written to close elsewhere. When the
    learner's zone is unknown this falls back to UTC, which is a fallback rather than
    a claim, and callers that assert something about the learner's local day are
    expected to check ``is_known`` first.
    """
    if not timezone_name:
        return func.date(column)
    return func.date(func.timezone(timezone_name, column))


def _zone(timezone_name: str | None) -> Any:
    """Resolve a zone name, tolerating a bad one by falling back to UTC."""
    if not timezone_name:
        return UTC
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unresolvable timezone in flashcard read", extra={"tz": timezone_name})
        return UTC


def _in_zone(instant: datetime, timezone_name: str | None) -> datetime:
    """The learner's wall clock for an instant."""
    aware = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
    return aware.astimezone(_zone(timezone_name))


def _zone_midnight(day: date, timezone_name: str | None) -> datetime:
    """The UTC instant at which a given local calendar day begins."""
    return datetime.combine(day, datetime.min.time(), tzinfo=_zone(timezone_name)).astimezone(UTC)


def _streak_length(active_dates: set[date], today: date) -> int:
    """Consecutive active days ending today, or ending yesterday if today is unused.

    Allowing the run to end yesterday is deliberate: a learner who reviewed for six
    days and has not opened the app yet this morning is on a six-day streak, not a
    broken one. The run is only broken once a full day has passed with no review.
    """
    if not active_dates:
        return 0
    cursor = today
    if cursor not in active_dates:
        cursor = today - timedelta(days=1)
        if cursor not in active_dates:
            return 0
    length = 0
    while cursor in active_dates:
        length += 1
        cursor -= timedelta(days=1)
    return length


class PersonalLearningRepository:
    """Data access for notes, exam prep, and documents.

    Session injection pattern:
        # Single operation (auto-managed session):
        note = await repo.find_note(note_id, user_id)

        # Multi-operation transaction (caller-managed session):
        async with repo.unit_of_work() as session:
            plan = await repo.create_study_plan(data, session=session)
            for item in items:
                await repo.create_plan_item(item_data, session=session)
            # Commits on exit; rolls back on exception
    """

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncGenerator[AsyncSession, None]:
        """Context manager that provides a single transactional session.

        All operations within the block share one session and one transaction.
        Commits on successful exit; rolls back on exception.
        """
        factory = get_session_factory()
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def _use_session(
        self, session: AsyncSession | None
    ) -> AsyncGenerator[AsyncSession, None]:
        """Internal helper: use provided session or create a new auto-managed one.

        - If session is provided: yield it as-is (caller owns commit/rollback).
        - If session is None: create a new session, auto-commit on success.

        For read-only work prefer `_read_session`, which skips the transaction this
        one opens and commits. This stays the default because a silent
        commit-if-needed heuristic is not safe here: much of this repository issues
        core `update()` / `delete()` statements, which never appear in
        `session.new`/`dirty`/`deleted`, so "nothing looks dirty, skip the commit"
        would discard writes.
        """
        if session is not None:
            yield session
        else:
            factory = get_session_factory()
            async with factory() as new_session:
                try:
                    yield new_session
                    await new_session.commit()
                except Exception:
                    await new_session.rollback()
                    raise

    @asynccontextmanager
    async def _read_session(
        self, session: AsyncSession | None
    ) -> AsyncGenerator[AsyncSession, None]:
        """A session for reads: no transaction, therefore no COMMIT round trip.

        Measured, not assumed. A round trip to the hosted database costs ~349 ms and
        one repository call cost ~1830 ms — roughly five round trips for a single
        `SELECT COUNT(*)`. `_use_session` opens an implicit transaction on first
        execute, commits it on exit, and the pool then resets the connection with a
        rollback, so a read paid for a BEGIN, a COMMIT and a reset it had no use for.

        `AUTOCOMMIT` means no implicit transaction is opened at all, so there is
        nothing to commit and nothing for the pool to unwind.

        **This changes no isolation guarantee that this code relied on.** Every
        standalone repository call was already its own transaction, so no caller
        ever got a consistent snapshot across two calls. A single statement under
        AUTOCOMMIT is still atomic and still sees a consistent snapshot of the rows
        it reads. What is lost is multi-statement read consistency, which was never
        available through this path.

        A caller-supplied session is yielded untouched. Inside a `unit_of_work` the
        caller owns the transaction, reads must see that transaction's uncommitted
        writes, and switching isolation mid-transaction is an error.
        """
        if session is not None:
            yield session
            return

        factory = get_session_factory()
        async with factory() as new_session:
            # Acquired eagerly so the option is set before the first statement,
            # which is what prevents the implicit BEGIN.
            await new_session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            yield new_session

    # -----------------------------------------------------------------------
    # Learn dashboard (bounded read helpers)
    # -----------------------------------------------------------------------

    async def count_overdue_flashcards(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> int:
        """Count cards due before the start of the current UTC day."""
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.next_review_at < start_of_today,
                )
            )
            return (await s.execute(stmt)).scalar_one() or 0

    async def list_recent_resources(
        self,
        user_id: str,
        *,
        take: int,
        session: AsyncSession | None = None,
    ) -> tuple[list[SavedResource], int]:
        """Return a bounded saved-resource page ordered by last use, then creation."""
        async with self._read_session(session) as s:
            condition = SavedResource.user_id == user_id
            total_stmt = select(func.count()).select_from(SavedResource).where(condition)
            total = (await s.execute(total_stmt)).scalar_one() or 0
            occurred_at = func.coalesce(
                SavedResource.last_accessed_at,
                SavedResource.created_at,
            )
            stmt = select(SavedResource).where(condition).order_by(occurred_at.desc()).limit(take)
            resources = list((await s.execute(stmt)).scalars().all())
            return resources, total

    async def list_dashboard_study_plans(
        self,
        user_id: str,
        *,
        take: int,
        session: AsyncSession | None = None,
    ) -> tuple[list[StudyPlan], int]:
        """Return bounded active plans and their full active count."""
        async with self._read_session(session) as s:
            condition = (StudyPlan.user_id == user_id) & (StudyPlan.status == "ACTIVE")
            total_stmt = select(func.count()).select_from(StudyPlan).where(condition)
            total = (await s.execute(total_stmt)).scalar_one() or 0
            stmt = select(StudyPlan).where(condition).order_by(StudyPlan.deadline.asc()).limit(take)
            plans = list((await s.execute(stmt)).scalars().all())
            return plans, total

    async def list_dashboard_exam_preps(
        self,
        user_id: str,
        *,
        take: int,
        session: AsyncSession | None = None,
    ) -> tuple[list[ExamPrep], int]:
        """Return bounded unfinished preparations and their full unfinished count.

        Returns ``(items, total)`` to match ``list_dashboard_study_plans``: the
        Learn dashboard renders both as paths, so it needs a total for each or its
        card count and its total disagree.
        """
        async with self._read_session(session) as s:
            condition = (ExamPrep.user_id == user_id) & (ExamPrep.status != "COMPLETED")
            total_stmt = select(func.count()).select_from(ExamPrep).where(condition)
            total = (await s.execute(total_stmt)).scalar_one() or 0
            stmt = select(ExamPrep).where(condition).order_by(ExamPrep.exam_date.asc()).limit(take)
            return list((await s.execute(stmt)).scalars().all()), total

    # -----------------------------------------------------------------------
    # Notes
    # -----------------------------------------------------------------------

    async def find_note(
        self, note_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Note | None:
        async with self._read_session(session) as s:
            stmt = (
                select(Note)
                .options(
                    selectinload(Note.tags),
                    selectinload(Note.attachments),
                )
                .where(Note.id == note_id, Note.user_id == user_id)
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def count_user_notes(self, user_id: str, *, session: AsyncSession | None = None) -> int:
        """Count total notes for a user (excluding archived)."""
        async with self._read_session(session) as s:
            stmt = (
                select(func.count())
                .select_from(Note)
                .where(Note.user_id == user_id)
                .where(Note.archived.is_(False))
            )
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    #: Orderings the note list supports, each ending in `id` so paging is stable.
    #:
    #: Without the tiebreaker, rows sharing an `updatedAt` — or a title — have no defined
    #: order between them, and the database is free to return them differently for page 1
    #: and page 2. That loses and duplicates notes across a pager rather than merely
    #: shuffling them.
    NOTE_SORTS: dict[str, tuple[Any, ...]] = {
        "recent": (Note.updated_at.desc(), Note.id.asc()),
        "title": (func.lower(Note.title).asc(), Note.id.asc()),
    }

    async def list_notes(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        sort: str = "recent",
        session: AsyncSession | None = None,
    ) -> tuple[list[Note], int]:
        async with self._read_session(session) as s:
            conditions = [Note.user_id == user_id]
            conditions.extend(self._build_note_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Note).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            # Items
            order_by = self.NOTE_SORTS.get(sort, self.NOTE_SORTS["recent"])
            stmt = (
                select(Note)
                .options(
                    selectinload(Note.tags),
                    selectinload(Note.attachments),
                )
                .where(*conditions)
                .order_by(*order_by)
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def create_note(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Note:
        async with self._use_session(session) as s:
            note = Note(**self._map_note(data))

            # Handle nested tags
            tags_data = data.get("tags")
            if tags_data and isinstance(tags_data, dict):
                create_list = tags_data.get("create", [])
                for tag_item in create_list:
                    note.tags.append(NoteTag(tag=tag_item["tag"]))

            s.add(note)
            await s.flush()
            await s.refresh(note)
            return note

    async def update_note(
        self, note_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Note | None:
        async with self._use_session(session) as s:
            mapped = self._map_note(data)
            if mapped:
                stmt = update(Note).where(Note.id == note_id).values(**mapped)
                await s.execute(stmt)

        return await self.find_note(note_id, data.get("userId", ""))

    async def delete_note(self, note_id: str, *, session: AsyncSession | None = None) -> None:
        async with self._use_session(session) as s:
            stmt = delete(Note).where(Note.id == note_id)
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Note Attachments
    # -----------------------------------------------------------------------

    async def create_attachment(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> NoteAttachment:
        async with self._use_session(session) as s:
            attachment = NoteAttachment(**self._map_attachment(data))
            s.add(attachment)
            await s.flush()
            await s.refresh(attachment)
            return attachment

    async def delete_attachment(
        self, attachment_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = delete(NoteAttachment).where(NoteAttachment.id == attachment_id)
            await s.execute(stmt)

    async def find_attachment(
        self, attachment_id: str, note_id: str, *, session: AsyncSession | None = None
    ) -> NoteAttachment | None:
        async with self._read_session(session) as s:
            stmt = select(NoteAttachment).where(
                NoteAttachment.id == attachment_id,
                NoteAttachment.note_id == note_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Note Tags
    # -----------------------------------------------------------------------

    async def delete_note_tags(self, note_id: str, *, session: AsyncSession | None = None) -> None:
        async with self._use_session(session) as s:
            stmt = delete(NoteTag).where(NoteTag.note_id == note_id)
            await s.execute(stmt)

    async def create_note_tags(
        self, note_id: str, tags: list[str], *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            for tag in tags:
                s.add(NoteTag(note_id=note_id, tag=tag))
            await s.flush()

    # -----------------------------------------------------------------------
    # Exam Prep
    # -----------------------------------------------------------------------

    async def find_exam_prep(
        self, prep_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> ExamPrep | None:
        async with self._read_session(session) as s:
            stmt = select(ExamPrep).where(
                ExamPrep.id == prep_id,
                ExamPrep.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_exam_preps(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[ExamPrep]:
        async with self._read_session(session) as s:
            stmt = (
                select(ExamPrep)
                .where(ExamPrep.user_id == user_id)
                .order_by(ExamPrep.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def search_exam_preps(
        self,
        user_id: str,
        *,
        status: str | None = None,
        search: str | None = None,
        sort_by: str | None = None,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[ExamPrep], int]:
        """Filtered, paginated preparations with optional sorting.

        Returns ``(items, total)`` where ``total`` counts every match, not just
        the returned page. Separate from ``list_dashboard_exam_preps``, which
        the Learn dashboard depends on filtering to non-completed rows.

        Sorting:
        - `None` or `"date"`: ordered by target date ascending (default)
        - `"readiness"`: ordered by average topic mastery descending, nulls last
        """
        async with self._read_session(session) as s:
            filters = [ExamPrep.user_id == user_id]
            if status:
                filters.append(ExamPrep.status == status)
            if search:
                filters.append(ilike_any(search, ExamPrep.subject))

            total = (
                await s.execute(select(func.count()).select_from(ExamPrep).where(*filters))
            ).scalar_one() or 0

            # Build order clause based on sort parameter
            if sort_by == "readiness":
                # Calculate average mastery as a subquery for sorting
                # Average of all topics' mastery scores, descending (higher = more ready)
                # Preparations with no topics appear last (nulls_last)
                mastery_subq = (
                    select(
                        PrepTopic.prep_id,
                        func.avg(PrepTopic.mastery_score).label("avg_mastery"),
                    )
                    .where(PrepTopic.prep_id == ExamPrep.id)
                    .group_by(PrepTopic.prep_id)
                    .scalar_subquery()
                )
                stmt = (
                    select(ExamPrep)
                    .where(*filters)
                    .order_by(mastery_subq.desc().nulls_last(), ExamPrep.exam_date.asc())
                    .offset(skip)
                    .limit(take)
                )
            else:
                # Default: order by target date
                stmt = (
                    select(ExamPrep)
                    .where(*filters)
                    .order_by(ExamPrep.exam_date.asc())
                    .offset(skip)
                    .limit(take)
                )

            items = list((await s.execute(stmt)).scalars().all())
            return items, total

    async def create_exam_prep(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> ExamPrep:
        async with self._use_session(session) as s:
            prep = ExamPrep(**self._map_exam_prep(data))
            s.add(prep)
            await s.flush()
            await s.refresh(prep)
            return prep

    async def update_exam_prep(
        self, prep_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> ExamPrep | None:
        async with self._use_session(session) as s:
            mapped = self._map_exam_prep(data)
            if mapped:
                stmt = update(ExamPrep).where(ExamPrep.id == prep_id).values(**mapped)
                await s.execute(stmt)

        # Re-fetch to return updated object
        async with self._use_session(None) as s:
            stmt = select(ExamPrep).where(ExamPrep.id == prep_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_exam_prep(self, prep_id: str, *, session: AsyncSession | None = None) -> None:
        async with self._use_session(session) as s:
            stmt = delete(ExamPrep).where(ExamPrep.id == prep_id)
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Generated Documents
    # -----------------------------------------------------------------------

    async def find_document(
        self, doc_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> GeneratedDocument | None:
        async with self._read_session(session) as s:
            stmt = select(GeneratedDocument).where(
                GeneratedDocument.id == doc_id,
                GeneratedDocument.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def find_document_by_share_id(
        self, share_id: str, *, session: AsyncSession | None = None
    ) -> GeneratedDocument | None:
        async with self._read_session(session) as s:
            stmt = select(GeneratedDocument).where(GeneratedDocument.share_id == share_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_documents(
        self,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
        search: str | None = None,
        doc_format: str | None = None,
        doc_type: str | None = None,
        session: AsyncSession | None = None,
    ) -> tuple[list[GeneratedDocument], int]:
        """A page of this learner's documents, newest first.

        ``search`` matches the title or the filename, because a learner looking for a document
        remembers one or the other. ``doc_format`` and ``doc_type`` are exact matches.

        Filtering by type matches only documents written after migration 034 — earlier rows have no
        recorded type and are deliberately not guessed at. Every filter is applied to the count as
        well as the page: filtering only the page is how a pager comes to advertise five pages of
        results for a query that matched three rows.
        """
        async with self._read_session(session) as s:
            conditions = [GeneratedDocument.user_id == user_id]
            if search:
                conditions.append(
                    ilike_any(search, GeneratedDocument.title, GeneratedDocument.filename)
                )
            if doc_format:
                conditions.append(GeneratedDocument.format == doc_format)
            if doc_type:
                conditions.append(GeneratedDocument.doc_type == doc_type)

            count_stmt = select(func.count()).select_from(GeneratedDocument).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            stmt = (
                select(GeneratedDocument)
                .where(*conditions)
                .order_by(GeneratedDocument.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def list_document_formats(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[tuple[str, int]]:
        """Every format this learner has produced, with a count each.

        The library's format breakdown was computed from the loaded page, so it reported "formats
        used" for twenty documents and labelled it as the library's. This answers the whole library
        in one grouped count.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(GeneratedDocument.format, func.count())
                .where(GeneratedDocument.user_id == user_id)
                .group_by(GeneratedDocument.format)
                .order_by(func.count().desc(), GeneratedDocument.format)
            )
            result = await s.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]

    async def count_documents(
        self, user_id: str, *, since: datetime, session: AsyncSession | None = None
    ) -> tuple[int, int, int]:
        """``(total, published, created_since)`` in one round trip.

        Three conditional counts rather than three queries. A round trip to the hosted database
        costs enough that the difference is visible, and these are the numbers one page renders
        together.
        """
        async with self._read_session(session) as s:
            owned = GeneratedDocument.user_id == user_id
            stmt = select(
                func.count(),
                func.count().filter(GeneratedDocument.is_public.is_(True)),
                func.count().filter(GeneratedDocument.created_at >= since),
            ).where(owned)
            row = (await s.execute(stmt)).one()
            return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    async def count_documents_since(
        self, user_id: str, since: datetime, *, session: AsyncSession | None = None
    ) -> int:
        """Count documents generated by user since a given datetime."""
        async with self._read_session(session) as s:
            stmt = (
                select(func.count())
                .select_from(GeneratedDocument)
                .where(GeneratedDocument.user_id == user_id)
                .where(GeneratedDocument.created_at >= since)
            )
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    async def create_document(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> GeneratedDocument:
        async with self._use_session(session) as s:
            doc = GeneratedDocument(**self._map_document(data))
            s.add(doc)
            await s.flush()
            await s.refresh(doc)
            return doc

    async def update_document(
        self, doc_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> GeneratedDocument | None:
        async with self._use_session(session) as s:
            mapped = self._map_document(data)
            if mapped:
                stmt = (
                    update(GeneratedDocument).where(GeneratedDocument.id == doc_id).values(**mapped)
                )
                await s.execute(stmt)

        async with self._use_session(None) as s:
            stmt = select(GeneratedDocument).where(GeneratedDocument.id == doc_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_document(
        self, doc_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete a document row. Scoped by owner, so a wrong id cannot delete anyone else's.

        The stored file is not this method's business — ``document_impl.delete_document`` removes the
        objects first and calls this second.
        """
        async with self._use_session(session) as s:
            stmt = delete(GeneratedDocument).where(
                GeneratedDocument.id == doc_id,
                GeneratedDocument.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    # -----------------------------------------------------------------------
    # Note versions
    # -----------------------------------------------------------------------

    async def create_note_history(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> NoteHistory:
        async with self._use_session(session) as s:
            entry = NoteHistory(**self._map_note_history(data))
            s.add(entry)
            await s.flush()
            await s.refresh(entry)
            return entry

    async def list_note_history(
        self,
        note_id: str,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[NoteHistory], int]:
        """Versions of one note, newest first. Scoped by owner as well as by note."""
        async with self._read_session(session) as s:
            conditions = [NoteHistory.note_id == note_id, NoteHistory.user_id == user_id]

            count_stmt = select(func.count()).select_from(NoteHistory).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            stmt = (
                select(NoteHistory)
                .where(*conditions)
                .order_by(NoteHistory.created_at.desc(), NoteHistory.id.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            return list(result.scalars().all()), total

    async def find_note_history(
        self,
        history_id: str,
        note_id: str,
        user_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> NoteHistory | None:
        async with self._read_session(session) as s:
            stmt = select(NoteHistory).where(
                NoteHistory.id == history_id,
                NoteHistory.note_id == note_id,
                NoteHistory.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def count_note_summary(
        self, user_id: str, *, archived: bool = False, session: AsyncSession | None = None
    ) -> tuple[int, int, int, int]:
        """``(total, tagged, linked_to_course, with_attachments)`` in one round trip.

        Scoped like ``list_notes`` is by default — personal notes matching ``archived`` — so a tile
        counts what the list below it would show. `EXISTS` rather than a join: a note with three tags
        must count once, and a join would count it three times.
        """
        async with self._read_session(session) as s:
            owned = [
                Note.user_id == user_id,
                Note.space_id.is_(None),
                Note.archived == archived,
            ]
            has_tag = select(NoteTag.id).where(NoteTag.note_id == Note.id).exists()
            has_attachment = (
                select(NoteAttachment.id).where(NoteAttachment.note_id == Note.id).exists()
            )
            stmt = select(
                func.count(),
                func.count().filter(has_tag),
                func.count().filter(Note.course_id.is_not(None)),
                func.count().filter(has_attachment),
            ).where(*owned)
            row = (await s.execute(stmt)).one()
            return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)

    async def list_note_creation_times(
        self,
        user_id: str,
        *,
        since: datetime,
        archived: bool = False,
        session: AsyncSession | None = None,
    ) -> list[datetime]:
        """Creation instants for notes made since ``since``.

        Returned as instants and bucketed into local days by the caller, rather than grouped in SQL.
        Day boundaries depend on the learner's timezone, and `timezone(name, timestamptz)` is
        Postgres-only — doing it here would make the query untestable anywhere else for the sake of a
        set bounded by one week.
        """
        async with self._read_session(session) as s:
            stmt = select(Note.created_at).where(
                Note.user_id == user_id,
                Note.space_id.is_(None),
                Note.archived == archived,
                Note.created_at >= since,
            )
            return [row[0] for row in (await s.execute(stmt)).all() if row[0] is not None]

    async def count_note_tags(
        self, user_id: str, *, archived: bool = False, session: AsyncSession | None = None
    ) -> list[tuple[str, int]]:
        """Every tag on this learner's personal notes, with a count each, commonest first.

        Scoped the same way ``list_notes`` is by default — personal notes only (``spaceId IS NULL``)
        and matching ``archived`` — so a chip's count is the number of notes the chip would show.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(NoteTag.tag, func.count())
                .join(Note, Note.id == NoteTag.note_id)
                .where(
                    Note.user_id == user_id,
                    Note.space_id.is_(None),
                    Note.archived == archived,
                )
                .group_by(NoteTag.tag)
                .order_by(func.count().desc(), NoteTag.tag)
            )
            result = await s.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]

    # -----------------------------------------------------------------------
    # Field mapping helpers (camelCase dict keys → snake_case model attrs)
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_note(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "content": "content",
            "summary": "summary",
            "courseId": "course_id",
            "topicId": "topic_id",
            "spaceId": "space_id",
            "lastEditedById": "last_edited_by_id",
            "archived": "archived",
            "voiceRecordingUrl": "voice_recording_url",
        }
        return map_fields(data, field_map, entity="_map_note")

    @staticmethod
    def _map_note_history(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "noteId": "note_id",
            "userId": "user_id",
            "title": "title",
            "content": "content",
        }
        return map_fields(data, field_map, entity="_map_note_history")

    @staticmethod
    def _map_attachment(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "noteId": "note_id",
            "filename": "filename",
            "url": "url",
            "size": "size",
        }
        return map_fields(data, field_map, entity="_map_attachment")

    @staticmethod
    def _map_exam_prep(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "subject": "subject",
            "type": "prep_type",
            "confidence": "confidence",
            "pace": "pace",
            "targetReadiness": "target_readiness",
            "examDate": "exam_date",
            "description": "description",
            "status": "status",
            "spaceId": "space_id",
        }
        return map_fields(data, field_map, entity="_map_exam_prep")

    @staticmethod
    def _map_document(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "format": "format",
            "docType": "doc_type",
            "style": "style",
            "filename": "filename",
            "fileUrl": "file_url",
            "previewUrl": "preview_url",
            "size": "size",
            "contentType": "content_type",
            "isPublic": "is_public",
            "shareId": "share_id",
        }
        return map_fields(data, field_map, entity="_map_document")

    @staticmethod
    def _build_note_conditions(where: dict[str, Any]) -> list:
        conditions = []
        if "archived" in where:
            conditions.append(Note.archived == where["archived"])
        if "courseId" in where:
            conditions.append(Note.course_id == where["courseId"])
        if "topicId" in where:
            conditions.append(Note.topic_id == where["topicId"])
        if "spaceId" in where:
            if where["spaceId"] is None:
                conditions.append(Note.space_id.is_(None))
            else:
                conditions.append(Note.space_id == where["spaceId"])
        if "title" in where and isinstance(where["title"], dict):
            contains = where["title"].get("contains", "")
            if contains:
                conditions.append(ilike_any(contains, Note.title))
        # OR search: match title OR content (case-insensitive)
        if "OR" in where:
            from sqlalchemy import or_

            or_conditions = []
            for clause in where["OR"]:
                if "title" in clause and isinstance(clause["title"], dict):
                    text = clause["title"].get("contains", "")
                    if text:
                        or_conditions.append(ilike_any(text, Note.title))
                if "content" in clause and isinstance(clause["content"], dict):
                    text = clause["content"].get("contains", "")
                    if text:
                        or_conditions.append(ilike_any(text, Note.content))
            if or_conditions:
                conditions.append(or_(*or_conditions))
        # Tag filter: match notes that have a specific tag
        if "tags" in where and isinstance(where["tags"], dict):
            some = where["tags"].get("some", {})
            tag_value = some.get("tag")
            if tag_value:
                conditions.append(Note.tags.any(NoteTag.tag == tag_value))
        return conditions

    # -----------------------------------------------------------------------
    # Flashcards
    # -----------------------------------------------------------------------

    async def create_flashcard(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Flashcard:
        async with self._use_session(session) as s:
            flashcard = Flashcard(**self._map_flashcard(data))
            s.add(flashcard)
            await s.flush()
            await s.refresh(flashcard)
            return flashcard

    async def get_flashcard(
        self, card_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Flashcard | None:
        async with self._read_session(session) as s:
            stmt = select(Flashcard).where(
                Flashcard.id == card_id,
                Flashcard.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_flashcard(
        self, card_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Flashcard | None:
        async with self._use_session(session) as s:
            mapped = self._map_flashcard(data)
            if mapped:
                stmt = update(Flashcard).where(Flashcard.id == card_id).values(**mapped)
                await s.execute(stmt)

        # Re-fetch to return updated object
        async with self._use_session(None) as s:
            stmt = select(Flashcard).where(Flashcard.id == card_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_due_flashcards(
        self,
        user_id: str,
        *,
        limit: int | None = None,
        deck_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[Flashcard]:
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            conditions: list[Any] = [
                Flashcard.user_id == user_id,
                Flashcard.next_review_at <= now,
            ]
            if deck_id is not None:
                conditions.append(Flashcard.deck_id == deck_id)
            stmt = (
                select(Flashcard)
                .options(selectinload(Flashcard.deck))
                .where(*conditions)
                # Most overdue first, so a bounded session spends its cards on the
                # work that has decayed furthest rather than on an arbitrary slice.
                .order_by(Flashcard.next_review_at.asc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def get_flashcard_stats(
        self,
        user_id: str,
        *,
        deck_id: str | None = None,
        timezone_name: str | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Card-state counts, plus review-history counts drawn from ``FlashcardReview``.

        Card-state figures (total, due, mastered, learning, new, average ease, recall)
        come from ``Flashcard`` columns and are correct for every learner immediately.

        Frequency figures (reviews this week, active days, streak) come from the review
        log and are therefore empty until reviews accumulate after migration 020. They
        used to be derived from ``Flashcard.lastReviewedAt``, which holds one date per
        card: re-reviewing a card moved its only date forward and erased the day it had
        been counted under, so a streak could shrink because the learner studied. An
        empty history reported honestly is better than a number that moves the wrong
        way.

        ``deck_id`` scopes every figure to one deck. Review-log figures are scoped by
        the deck the grade was given in, which is snapshotted on the review row, so
        moving a card between decks does not move its past grades with it.
        """
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            # "Today" and "this week" are claims about the learner's calendar, so they
            # are resolved in their zone when it is known and in UTC when it is not.
            # The SQL day grouping below uses the same zone, so the two cannot
            # disagree about where a day starts.
            local_now = _in_zone(now, timezone_name)
            today = local_now.date()
            week_start_date = today - timedelta(days=today.weekday())
            week_start = _zone_midnight(week_start_date, timezone_name)

            card_conditions = [Flashcard.user_id == user_id]
            if deck_id is not None:
                card_conditions.append(Flashcard.deck_id == deck_id)

            # Every card-level figure in one statement, as filtered aggregates over a single scan.
            #
            # This was six separate `SELECT`s against `Flashcard` with the same `WHERE` — total, due,
            # mastered, new, average ease, recall — issued back to back on one session, so they could
            # not even overlap. Six scans of the same rows, and six round trips: against a hosted
            # database that is the dominant cost of the whole call, far more than the scans.
            #
            # `count().filter()` is standard `FILTER (WHERE ...)` on Postgres and is emulated with
            # `CASE` on SQLite, so this runs unchanged under the test suite.
            #
            # `overdue` is folded in here too. It was a seventh query in a *different* repository
            # method reached through a different branch of the dashboard's gather, asking about the
            # same table with an almost identical predicate — due-before-midnight rather than
            # due-by-now. Two genuinely different questions, but not two round trips' worth.
            aggregate_row = (
                await s.execute(
                    select(
                        func.count(),
                        func.count().filter(Flashcard.next_review_at <= now),
                        func.count().filter(Flashcard.next_review_at < _zone_midnight(today, timezone_name)),
                        func.count().filter(Flashcard.interval_days >= MASTERED_INTERVAL_DAYS),
                        func.count().filter(Flashcard.last_reviewed_at.is_(None)),
                        func.avg(Flashcard.ease_factor),
                        func.avg(Flashcard.last_quality).filter(Flashcard.last_reviewed_at.is_not(None)),
                        func.count().filter(Flashcard.last_reviewed_at.is_not(None)),
                    )
                    .select_from(Flashcard)
                    .where(*card_conditions)
                )
            ).one()

            total = aggregate_row[0] or 0
            due_today = aggregate_row[1] or 0
            overdue_count = aggregate_row[2] or 0
            mastered_count = aggregate_row[3] or 0
            # "New" is never reviewed at all; "learning" is reviewed but not yet
            # mature. The two plus mastered partition the library exactly, which is
            # what lets the page show three tiles that add up to the total.
            new_count = aggregate_row[4] or 0
            learning_count = max(0, total - mastered_count - new_count)
            avg_ease_factor = aggregate_row[5] or 2.5

            # Recall across the library: the mean of each card's most recent grade,
            # as a percentage of the 0-5 scale. Negative `lastQuality` is the
            # never-reviewed sentinel and is excluded by the timestamp filter rather
            # than by value, because 0 is a real grade.
            recall_row = (aggregate_row[6], aggregate_row[7])
            reviewed_card_count = int(recall_row[1] or 0)
            recall_percent = (
                round(float(recall_row[0]) / 5 * 100)
                if reviewed_card_count and recall_row[0] is not None
                else None
            )

            review_conditions = [FlashcardReview.user_id == user_id]
            if deck_id is not None:
                review_conditions.append(FlashcardReview.deck_id == deck_id)

            # Both review counts in one statement, for the same reason as the card aggregates above:
            # same table, same `WHERE`, one of them a strict subset of the other.
            review_row = (
                await s.execute(
                    select(
                        func.count(),
                        func.count().filter(FlashcardReview.reviewed_at >= week_start),
                    )
                    .select_from(FlashcardReview)
                    .where(*review_conditions)
                )
            ).one()
            reviewed_total = review_row[0] or 0
            reviewed_this_week = review_row[1] or 0

            # Distinct review days, newest first, bounded to a year. A streak longer
            # than that is not a figure any surface shows, and the bound keeps this a
            # small read for a heavy user.
            day = _local_day(FlashcardReview.reviewed_at, timezone_name)
            days_stmt = (
                select(day)
                .where(
                    *review_conditions,
                    FlashcardReview.reviewed_at >= now - timedelta(days=366),
                )
                .group_by(day)
                .order_by(day.desc())
            )
            day_values = (await s.execute(days_stmt)).scalars().all()
            activity_dates = {_as_date(value) for value in day_values if value is not None}
            activity_dates.discard(None)

            active_days_this_week = sorted(
                value.isoformat() for value in activity_dates if value >= week_start_date
            )
            current_streak = _streak_length(activity_dates, today)

            return {
                "total": total,
                "due_today": due_today,
                # Cards due before the start of today, as distinct from due by now. Published here so
                # the Learn dashboard can stop asking `count_overdue_flashcards` for it in a separate
                # round trip on a separate connection.
                "overdue_count": overdue_count,
                "mastered_count": mastered_count,
                "learning_count": learning_count,
                "new_count": new_count,
                "avg_ease_factor": round(float(avg_ease_factor), 2),
                "recall_percent": recall_percent,
                "reviewed_card_count": reviewed_card_count,
                "reviewed_total": reviewed_total,
                "reviewed_this_week": reviewed_this_week,
                "active_days_this_week": active_days_this_week,
                "current_streak": current_streak,
            }

    # -----------------------------------------------------------------------
    # Flashcard Decks
    # -----------------------------------------------------------------------

    async def create_deck(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> FlashcardDeck:
        async with self._use_session(session) as s:
            deck = FlashcardDeck(**self._map_deck(data))
            s.add(deck)
            await s.flush()
            await s.refresh(deck)
            return deck

    async def list_decks(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[FlashcardDeck]:
        async with self._read_session(session) as s:
            stmt = (
                select(FlashcardDeck)
                .where(FlashcardDeck.user_id == user_id)
                .order_by(FlashcardDeck.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_decks_with_counts(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[tuple[FlashcardDeck, int, int]]:
        """List decks with their card and due-card counts.

        Counts are aggregated in a single grouped query rather than one query
        per deck. Returns `(deck, card_count, due_count)` tuples.
        """
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            card_count = func.count(Flashcard.id)
            due_count = func.count(Flashcard.id).filter(Flashcard.next_review_at <= now)
            stmt = (
                select(FlashcardDeck, card_count, due_count)
                .outerjoin(
                    Flashcard,
                    (Flashcard.deck_id == FlashcardDeck.id)
                    & (Flashcard.user_id == FlashcardDeck.user_id),
                )
                .where(FlashcardDeck.user_id == user_id)
                .group_by(FlashcardDeck.id)
                .order_by(FlashcardDeck.created_at.desc())
            )
            result = await s.execute(stmt)
            return [(deck, cards or 0, due or 0) for deck, cards, due in result.all()]

    async def list_deck_flashcards(
        self, deck_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> list[Flashcard]:
        async with self._read_session(session) as s:
            stmt = (
                select(Flashcard)
                .where(
                    Flashcard.deck_id == deck_id,
                    Flashcard.user_id == user_id,
                )
                .order_by(Flashcard.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Flashcards — write paths added in stage 2
    # -----------------------------------------------------------------------

    async def apply_flashcard_review(
        self,
        card_id: str,
        user_id: str,
        *,
        card_update: dict[str, Any],
        review: dict[str, Any],
    ) -> Flashcard | None:
        """Advance a card's schedule and append its review row in one transaction.

        The two writes are inseparable. A card whose schedule moved without a logged
        review makes the streak understate what the learner did; a logged review
        whose card did not move would double-count. Neither is recoverable after the
        fact, so they share a transaction rather than being two repository calls.
        """
        async with self.unit_of_work() as s:
            stmt = (
                update(Flashcard)
                .where(Flashcard.id == card_id, Flashcard.user_id == user_id)
                .values(**self._map_flashcard(card_update))
            )
            result = await s.execute(stmt)
            if result.rowcount == 0:
                # Not the caller's card, or gone. Nothing is logged either way.
                return None

            s.add(
                FlashcardReview(
                    user_id=user_id,
                    flashcard_id=card_id,
                    deck_id=review.get("deckId"),
                    quality=review["quality"],
                    interval_days=review["intervalDays"],
                    ease_factor=review["easeFactor"],
                    repetition_count=review["repetitionCount"],
                    was_lapse=review["wasLapse"],
                    reviewed_at=review["reviewedAt"],
                )
            )
            await s.flush()

            refreshed = await s.execute(select(Flashcard).where(Flashcard.id == card_id))
            return refreshed.scalar_one_or_none()

    async def list_flashcards(
        self,
        user_id: str,
        *,
        deck_id: str | None = None,
        unfiled: bool = False,
        search: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        state: str | None = None,
        sort: str = "recent",
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[Flashcard], int]:
        """A page of the learner's cards.

        ``state`` filters on scheduling state using the same definitions as the stats
        read — ``due``, ``new`` (never reviewed), ``learning`` (reviewed, not mature)
        and ``mastered`` — so a filtered list can never disagree with the counts shown
        above it.

        ``sort`` is part of the contract rather than a client concern because ordering
        and pagination are inseparable: a page boundary is only meaningful against a
        defined order, and a client that re-sorts the page it received produces a list
        that is sorted within each page and unsorted across them.

        - ``recent`` — newest first. The default, and what a library view wants.
        - ``due`` — soonest due first, so a deck's page one is the work waiting.

        Both are tie-broken by id. Without it, rows sharing a timestamp can be returned
        in a different relative order on each query, which silently duplicates or drops
        cards across page boundaries.

        ``unfiled`` asks for the cards in no deck. It is a separate flag rather than a
        ``deck_id`` value because ``deck_id=None`` already means "do not filter by
        deck", so there was previously no way to express the question at all — and
        unfiled cards are precisely the ones missing from the deck list, so they were
        unreachable from any listing surface.

        ``source_id`` narrows to one origin, which is what makes "the cards from this
        note" answerable for cards created before decks were assigned by origin.
        """
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            conditions: list[Any] = [Flashcard.user_id == user_id]
            if unfiled:
                conditions.append(Flashcard.deck_id.is_(None))
            elif deck_id is not None:
                conditions.append(Flashcard.deck_id == deck_id)
            if source_type:
                conditions.append(Flashcard.source_type == source_type)
            if source_id:
                conditions.append(Flashcard.source_id == source_id)
            if search:
                conditions.append(ilike_any(search, Flashcard.front, Flashcard.back))
            if state == "due":
                conditions.append(Flashcard.next_review_at <= now)
            elif state == "new":
                conditions.append(Flashcard.last_reviewed_at.is_(None))
            elif state == "mastered":
                conditions.append(Flashcard.interval_days >= MASTERED_INTERVAL_DAYS)
            elif state == "learning":
                conditions.append(Flashcard.last_reviewed_at.is_not(None))
                conditions.append(Flashcard.interval_days < MASTERED_INTERVAL_DAYS)

            total_stmt = select(func.count()).select_from(Flashcard).where(*conditions)
            total = (await s.execute(total_stmt)).scalar_one() or 0

            order = (
                (Flashcard.next_review_at.asc(), Flashcard.id.asc())
                if sort == "due"
                else (Flashcard.created_at.desc(), Flashcard.id.asc())
            )
            stmt = select(Flashcard).where(*conditions).order_by(*order).offset(skip).limit(take)
            rows = list((await s.execute(stmt)).scalars().all())
            return rows, total

    async def update_flashcard_fields(
        self,
        card_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> Flashcard | None:
        """Update a card the caller owns, returning ``None`` when they do not.

        The ownership predicate is in the ``UPDATE`` itself rather than in a
        preceding ``SELECT``, so there is no window between the check and the write.
        """
        async with self._use_session(session) as s:
            mapped = self._map_flashcard(data)
            if mapped:
                stmt = (
                    update(Flashcard)
                    .where(Flashcard.id == card_id, Flashcard.user_id == user_id)
                    .values(**mapped)
                )
                result = await s.execute(stmt)
                if result.rowcount == 0:
                    return None
            refreshed = await s.execute(
                select(Flashcard).where(Flashcard.id == card_id, Flashcard.user_id == user_id)
            )
            return refreshed.scalar_one_or_none()

    async def delete_flashcard(
        self, card_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete a card the caller owns. Its review rows survive, detached."""
        async with self._use_session(session) as s:
            stmt = delete(Flashcard).where(Flashcard.id == card_id, Flashcard.user_id == user_id)
            result = await s.execute(stmt)
            return result.rowcount > 0

    # -----------------------------------------------------------------------
    # Decks — detail, update, delete, aggregates
    # -----------------------------------------------------------------------

    async def get_deck(
        self, deck_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> FlashcardDeck | None:
        async with self._read_session(session) as s:
            stmt = select(FlashcardDeck).where(
                FlashcardDeck.id == deck_id, FlashcardDeck.user_id == user_id
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def find_deck_by_origin(
        self,
        user_id: str,
        origin_type: str,
        origin_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> FlashcardDeck | None:
        """The deck the server created for this origin, or ``None``.

        Backs the get-or-create that keeps generation idempotent: without a lookup by
        origin, "the deck for this note" is unanswerable and every press of Generate
        starts another pile. Matched on the origin id rather than on a title, because a
        learner may rename either the deck or the note and a title match would then
        miss or collide — the same reasoning as ``StudyPlan.reviewDeckId``.

        At most one row can match: a partial unique index covers
        ``(userId, originType, originId)``.
        """
        async with self._read_session(session) as s:
            stmt = select(FlashcardDeck).where(
                FlashcardDeck.user_id == user_id,
                FlashcardDeck.origin_type == origin_type,
                FlashcardDeck.origin_id == origin_id,
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def count_unfiled_flashcards(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> int:
        """Cards the learner owns that sit in no deck.

        The dashboard needs this to be self-consistent. Its header figures come from
        ``get_flashcard_stats``, which is scoped to ``userId`` and therefore counts
        unfiled cards, while its deck list is a ``LEFT JOIN`` from ``FlashcardDeck`` and
        structurally cannot. Reporting the count is what lets the page explain the gap
        instead of leaving the learner to notice it.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(Flashcard.user_id == user_id, Flashcard.deck_id.is_(None))
            )
            return (await s.execute(stmt)).scalar_one() or 0

    async def update_deck(
        self,
        deck_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> FlashcardDeck | None:
        async with self._use_session(session) as s:
            mapped = self._map_deck(data)
            if mapped:
                stmt = (
                    update(FlashcardDeck)
                    .where(FlashcardDeck.id == deck_id, FlashcardDeck.user_id == user_id)
                    .values(**mapped)
                )
                result = await s.execute(stmt)
                if result.rowcount == 0:
                    return None
            refreshed = await s.execute(
                select(FlashcardDeck).where(
                    FlashcardDeck.id == deck_id, FlashcardDeck.user_id == user_id
                )
            )
            return refreshed.scalar_one_or_none()

    async def delete_deck(
        self, deck_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete a deck and detach its cards, leaving them in the library.

        Detaching rather than cascading is the resolution of a contradiction: the
        foreign key on ``Flashcard.deckId`` is ``SET NULL`` while the ORM
        relationship declared ``delete-orphan``, so the same request destroyed or
        preserved cards depending on which layer executed the delete. A deck is a
        container the learner organises with; removing it should not destroy cards
        they wrote or the review history attached to them. Cards are removed
        deliberately, one at a time, through the card delete route.

        The detach is explicit rather than left to the database so it happens in the
        same transaction as the delete and cannot be half-applied.
        """
        async with self._use_session(session) as s:
            owned = await s.execute(
                select(FlashcardDeck.id).where(
                    FlashcardDeck.id == deck_id, FlashcardDeck.user_id == user_id
                )
            )
            if owned.scalar_one_or_none() is None:
                return False
            await s.execute(
                update(Flashcard)
                .where(Flashcard.deck_id == deck_id, Flashcard.user_id == user_id)
                .values(deck_id=None)
            )
            await s.execute(delete(FlashcardDeck).where(FlashcardDeck.id == deck_id))
            return True

    async def list_decks_with_stats(
        self,
        user_id: str,
        *,
        deck_id: str | None = None,
        origin_type: str | None = None,
        origin_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Decks with every per-deck figure the library card shows, in one query.

        Card count, due count, mastered count, newest review, next scheduled review
        and recall all come from one grouped ``LEFT JOIN`` rather than a query per
        deck. The previous helper returned only card and due counts, which is why the
        page had to invent the rest.

        Recall is the mean of each card's most recent grade over the 0-5 scale, and is
        ``None`` for a deck where nothing has been reviewed — an unreviewed deck has
        no recall, and reporting 0% would state that the learner is failing it.

        ``origin_type``/``origin_id`` narrow to the deck the server created for one
        source, so a note page can ask for "the deck for this note" and receive it with
        the same aggregates the library card shows, rather than fetching the deck and
        then counting its cards separately.
        """
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            reviewed = Flashcard.last_reviewed_at.is_not(None)
            stmt = (
                select(
                    FlashcardDeck,
                    func.count(Flashcard.id).label("card_count"),
                    func.count(Flashcard.id)
                    .filter(Flashcard.next_review_at <= now)
                    .label("due_count"),
                    func.count(Flashcard.id)
                    .filter(Flashcard.interval_days >= MASTERED_INTERVAL_DAYS)
                    .label("mastered_count"),
                    func.count(Flashcard.id).filter(reviewed).label("reviewed_count"),
                    func.max(Flashcard.last_reviewed_at).label("last_reviewed_at"),
                    func.min(Flashcard.next_review_at).label("next_review_at"),
                    func.avg(Flashcard.last_quality).filter(reviewed).label("avg_quality"),
                )
                .outerjoin(
                    Flashcard,
                    (Flashcard.deck_id == FlashcardDeck.id)
                    & (Flashcard.user_id == FlashcardDeck.user_id),
                )
                .where(FlashcardDeck.user_id == user_id)
                .group_by(FlashcardDeck.id)
                .order_by(FlashcardDeck.created_at.desc())
            )
            if deck_id is not None:
                stmt = stmt.where(FlashcardDeck.id == deck_id)
            if origin_type is not None:
                stmt = stmt.where(FlashcardDeck.origin_type == origin_type)
            if origin_id is not None:
                stmt = stmt.where(FlashcardDeck.origin_id == origin_id)

            rows = (await s.execute(stmt)).all()
            return [
                {
                    "deck": row[0],
                    "card_count": row[1] or 0,
                    "due_count": row[2] or 0,
                    "mastered_count": row[3] or 0,
                    "reviewed_count": row[4] or 0,
                    "last_reviewed_at": row[5],
                    "next_review_at": row[6],
                    "recall_percent": (
                        round(float(row[7]) / 5 * 100) if row[4] and row[7] is not None else None
                    ),
                }
                for row in rows
            ]

    # -----------------------------------------------------------------------
    # Flashcard review history (reads)
    # -----------------------------------------------------------------------

    async def get_review_forecast(
        self,
        user_id: str,
        *,
        days: int,
        timezone_name: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Cards scheduled for each of the next ``days`` local calendar days.

        Two figures per day, which partition the scheduled cards rather than
        overlapping: ``due`` is cards already in repetition, ``new`` is cards never
        reviewed. The first bucket absorbs everything overdue, because a card that was
        due last week is work waiting today and putting it anywhere else would hide
        it.

        One query with conditional aggregates, not one query per day.
        """
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            first_day = _in_zone(now, timezone_name).date()
            boundaries = [
                _zone_midnight(first_day + timedelta(days=offset), timezone_name)
                for offset in range(days + 1)
            ]

            columns: list[Any] = []
            for index in range(days):
                start, end = boundaries[index], boundaries[index + 1]
                # The first bucket has no lower bound so overdue work lands in it.
                window = Flashcard.next_review_at < end
                if index > 0:
                    window = window & (Flashcard.next_review_at >= start)
                columns.append(
                    func.count(Flashcard.id)
                    .filter(window, Flashcard.last_reviewed_at.is_not(None))
                    .label(f"due_{index}")
                )
                columns.append(
                    func.count(Flashcard.id)
                    .filter(window, Flashcard.last_reviewed_at.is_(None))
                    .label(f"new_{index}")
                )

            row = (await s.execute(select(*columns).where(Flashcard.user_id == user_id))).one()
            return [
                {
                    "date": first_day + timedelta(days=index),
                    "due": row[index * 2] or 0,
                    "new": row[index * 2 + 1] or 0,
                }
                for index in range(days)
            ]

    async def list_review_events(
        self,
        user_id: str,
        *,
        since: datetime,
        limit: int = 2000,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Raw review rows in a window, newest first, for read-side derivation.

        Returned as plain dictionaries because every consumer aggregates them —
        grouping into sessions, bucketing by hour — and none needs an ORM identity.
        Bounded by ``limit`` so a heavy reviewer cannot turn a page render into an
        unbounded read.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(
                    FlashcardReview.reviewed_at,
                    FlashcardReview.quality,
                    FlashcardReview.deck_id,
                    FlashcardReview.flashcard_id,
                    FlashcardReview.was_lapse,
                    FlashcardReview.interval_days,
                )
                .where(
                    FlashcardReview.user_id == user_id,
                    FlashcardReview.reviewed_at >= since,
                )
                .order_by(FlashcardReview.reviewed_at.desc())
                .limit(limit)
            )
            return [
                {
                    "reviewed_at": row[0],
                    "quality": row[1],
                    "deck_id": row[2],
                    "flashcard_id": row[3],
                    "was_lapse": row[4],
                    "interval_days": row[5],
                }
                for row in (await s.execute(stmt)).all()
            ]

    async def list_graduation_events(
        self,
        user_id: str,
        *,
        since: datetime,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """Reviews at which a card crossed into maturity for the first time in a run.

        A graduation is not a stored fact, it is a transition: this review put the card
        at or past the mastery interval and the one before it did not. Detected with a
        window function over the card's whole history rather than over the requested
        slice, because a card whose previous review predates the window would otherwise
        look like a first-time graduation every time the window moved.

        Re-graduation after a lapse counts. A card the learner lost and rebuilt to
        maturity has been mastered again, and suppressing that would make the feed go
        quiet exactly when the learner recovered something hard.
        """
        async with self._read_session(session) as s:
            previous = func.lag(FlashcardReview.interval_days).over(
                partition_by=FlashcardReview.flashcard_id,
                order_by=FlashcardReview.reviewed_at.asc(),
            )
            history = (
                select(
                    FlashcardReview.reviewed_at.label("reviewed_at"),
                    FlashcardReview.deck_id.label("deck_id"),
                    FlashcardReview.flashcard_id.label("flashcard_id"),
                    FlashcardReview.interval_days.label("interval_days"),
                    previous.label("previous_interval"),
                )
                .where(
                    FlashcardReview.user_id == user_id,
                    FlashcardReview.flashcard_id.is_not(None),
                )
                .subquery()
            )
            stmt = select(history.c.reviewed_at, history.c.deck_id, history.c.flashcard_id).where(
                history.c.interval_days >= MASTERED_INTERVAL_DAYS,
                or_(
                    history.c.previous_interval.is_(None),
                    history.c.previous_interval < MASTERED_INTERVAL_DAYS,
                ),
                history.c.reviewed_at >= since,
            )
            return [
                {"occurred_at": row[0], "deck_id": row[1], "flashcard_id": row[2]}
                for row in (await s.execute(stmt)).all()
            ]

    async def list_card_creations(
        self,
        user_id: str,
        *,
        since: datetime,
        limit: int = 2000,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        """When the learner added cards, and to which deck.

        Creation is already recorded by ``Flashcard.createdAt``; this reads it rather
        than adding another log. Attributed to the card's current deck, which is the
        one honest option available — the deck a card was created in is not stored, and
        inventing it from the deck it now sits in would be presented as history.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(Flashcard.created_at, Flashcard.deck_id, Flashcard.id)
                .where(Flashcard.user_id == user_id, Flashcard.created_at >= since)
                .order_by(Flashcard.created_at.desc())
                .limit(limit)
            )
            return [
                {"occurred_at": row[0], "deck_id": row[1], "flashcard_id": row[2]}
                for row in (await s.execute(stmt)).all()
            ]

    async def count_mastered_by_deck_as_of(
        self,
        user_id: str,
        *,
        cutoff: datetime,
        session: AsyncSession | None = None,
    ) -> dict[str, int]:
        """How many of each deck's cards were mature at an earlier instant.

        Reconstructed by replay: for each card, the interval recorded by its most
        recent review before ``cutoff``. This is the reason ``FlashcardReview`` stores
        SM-2 state and not just a grade — without it, "mastery went up 6 points this
        week" would have no earlier number to subtract and would have to be invented.

        Cards are grouped by the deck they are in *now*, so that the comparison is
        between two measurements of the same deck rather than of two different sets.
        Cards with no review before the cutoff are absent, which is correct: they were
        not mature then.
        """
        async with self._read_session(session) as s:
            latest = (
                select(
                    FlashcardReview.flashcard_id.label("card_id"),
                    func.max(FlashcardReview.reviewed_at).label("reviewed_at"),
                )
                .where(
                    FlashcardReview.user_id == user_id,
                    FlashcardReview.reviewed_at < cutoff,
                    FlashcardReview.flashcard_id.is_not(None),
                )
                .group_by(FlashcardReview.flashcard_id)
                .subquery()
            )
            stmt = (
                select(Flashcard.deck_id, func.count())
                .select_from(latest)
                .join(
                    FlashcardReview,
                    (FlashcardReview.flashcard_id == latest.c.card_id)
                    & (FlashcardReview.reviewed_at == latest.c.reviewed_at),
                )
                .join(Flashcard, Flashcard.id == latest.c.card_id)
                .where(
                    Flashcard.user_id == user_id,
                    FlashcardReview.interval_days >= MASTERED_INTERVAL_DAYS,
                    Flashcard.deck_id.is_not(None),
                )
                .group_by(Flashcard.deck_id)
            )
            return {row[0]: row[1] or 0 for row in (await s.execute(stmt)).all() if row[0]}

    async def count_lapsing_flashcards(
        self,
        user_id: str,
        *,
        min_lapses: int,
        session: AsyncSession | None = None,
    ) -> int:
        """Cards the learner has forgotten at least ``min_lapses`` times."""
        async with self._read_session(session) as s:
            stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(Flashcard.user_id == user_id, Flashcard.lapse_count >= min_lapses)
            )
            return (await s.execute(stmt)).scalar_one() or 0

    # -----------------------------------------------------------------------
    # Field mapping helpers — Flashcards & Decks
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_flashcard(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "deckId": "deck_id",
            "front": "front",
            "back": "back",
            "intervalDays": "interval_days",
            "repetitionCount": "repetition_count",
            "easeFactor": "ease_factor",
            "nextReviewAt": "next_review_at",
            "lastReviewedAt": "last_reviewed_at",
            "lastQuality": "last_quality",
            "lapseCount": "lapse_count",
            "sourceType": "source_type",
            "sourceId": "source_id",
            # The three review aids. This mapper is an allowlist — `if k in field_map` — so a key
            # missing from it is accepted by the request model and silently dropped here, which is how
            # a field can appear in the contract and never reach the database.
            "hint": "hint",
            "explanation": "explanation",
            "memoryHook": "memory_hook",
        }
        return map_fields(data, field_map, entity="_map_flashcard")

    @staticmethod
    def _map_deck(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "description": "description",
            "subject": "subject",
            "accent": "accent",
            "dailyGoal": "daily_goal",
            "courseId": "course_id",
            "topicId": "topic_id",
            "prepId": "prep_id",
            # This mapper is an allowlist — a key missing from it is accepted by the
            # request model and silently dropped here, which is how a field reaches the
            # contract and never reaches the database.
            "originType": "origin_type",
            "originId": "origin_id",
        }
        return map_fields(data, field_map, entity="_map_deck")

    # -----------------------------------------------------------------------
    # Saved Resources
    # -----------------------------------------------------------------------

    async def create_resource(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> SavedResource:
        async with self._use_session(session) as s:
            resource = SavedResource(**self._map_resource(data))
            s.add(resource)
            await s.flush()
            await s.refresh(resource)
            return resource

    async def list_resources(
        self,
        user_id: str,
        *,
        source_type: str | None = None,
        search: str | None = None,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[SavedResource], int]:
        async with self._read_session(session) as s:
            conditions = [SavedResource.user_id == user_id]

            if source_type is not None:
                conditions.append(SavedResource.source_type == source_type)
            if search:
                conditions.append(ilike_any(search, SavedResource.title))

            # Count
            count_stmt = select(func.count()).select_from(SavedResource).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(SavedResource)
                .where(*conditions)
                .order_by(SavedResource.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def delete_resource(
        self, resource_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        async with self._use_session(session) as s:
            stmt = delete(SavedResource).where(
                SavedResource.id == resource_id,
                SavedResource.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    async def update_resource_tags(
        self,
        resource_id: str,
        user_id: str,
        tags: list[str],
        *,
        session: AsyncSession | None = None,
    ) -> SavedResource | None:
        async with self._use_session(session) as s:
            stmt = (
                update(SavedResource)
                .where(
                    SavedResource.id == resource_id,
                    SavedResource.user_id == user_id,
                )
                .values(tags=tags)
            )
            result = await s.execute(stmt)
            if result.rowcount == 0:
                return None

        # Re-fetch updated resource
        async with self._use_session(None) as s:
            stmt = select(SavedResource).where(SavedResource.id == resource_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_last_accessed(
        self, resource_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Stamp the access time. Scoped by owner, and reports whether a row matched."""
        async with self._use_session(session) as s:
            stmt = (
                update(SavedResource)
                .where(
                    SavedResource.id == resource_id,
                    SavedResource.user_id == user_id,
                )
                .values(last_accessed_at=datetime.now(UTC))
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    # -----------------------------------------------------------------------
    # Field mapping helpers — Saved Resources
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_resource(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "url": "url",
            "sourceType": "source_type",
            "sourceId": "source_id",
            "tags": "tags",
        }
        return map_fields(data, field_map, entity="_map_resource")

    # -----------------------------------------------------------------------
    # Learning Profiles
    # -----------------------------------------------------------------------

    async def create_profile(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> LearningProfile:
        async with self._use_session(session) as s:
            profile = LearningProfile(**self._map_profile(data))
            s.add(profile)
            await s.flush()
            await s.refresh(profile)
            return profile

    async def get_profile_by_user(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> LearningProfile | None:
        async with self._read_session(session) as s:
            stmt = select(LearningProfile).where(LearningProfile.user_id == user_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_profile(
        self, user_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> LearningProfile | None:
        async with self._use_session(session) as s:
            mapped = self._map_profile(data)
            if mapped:
                stmt = (
                    update(LearningProfile)
                    .where(LearningProfile.user_id == user_id)
                    .values(**mapped)
                )
                await s.execute(stmt)

        return await self.get_profile_by_user(user_id)

    async def update_profile_behaviour(
        self, user_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> None:
        behaviour_fields = {
            "preferredStudyTimes": "preferred_study_times",
            "avgSessionMinutes": "avg_session_minutes",
            "consistencyScore": "consistency_score",
            "bestDayOfWeek": "best_day_of_week",
            "dropoutRisk": "dropout_risk",
        }
        mapped = map_fields(data, behaviour_fields, entity="update_profile_behaviour")
        if not mapped:
            return

        async with self._use_session(session) as s:
            stmt = (
                update(LearningProfile).where(LearningProfile.user_id == user_id).values(**mapped)
            )
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Field mapping helpers — Learning Profile
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_profile(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "purpose": "purpose",
            "subjects": "subjects",
            "goalsText": "goals_text",
            "preferredExplanationStyle": "preferred_explanation_style",
            "proficiencyMap": "proficiency_map",
            "onboardingCompletedAt": "onboarding_completed_at",
            "maturityDays": "maturity_days",
            "quietHoursStart": "quiet_hours_start",
            "quietHoursEnd": "quiet_hours_end",
            "maxDailyNotifications": "max_daily_notifications",
            "preferredStudyTimes": "preferred_study_times",
            "avgSessionMinutes": "avg_session_minutes",
            "consistencyScore": "consistency_score",
            "bestDayOfWeek": "best_day_of_week",
            "dropoutRisk": "dropout_risk",
            "preferredLlmProvider": "preferred_llm_provider",
            # Commercial fields
            "trialStartedAt": "trial_started_at",
            "trialEndsAt": "trial_ends_at",
            "lastTrialEndedAt": "last_trial_ended_at",
            "lastTriggerShownAt": "last_trigger_shown_at",
            "triggerDismissalCount": "trigger_dismissal_count",
            "lastTriggerDismissedAt": "last_trigger_dismissed_at",
            "educatorReadinessMetAt": "educator_readiness_met_at",
            "educatorSuggestionShownAt": "educator_suggestion_shown_at",
            "spaceTrialStartedAt": "space_trial_started_at",
            "lastValueSummaryAt": "last_value_summary_at",
            "plusFeaturesUsedThisPeriod": "plus_features_used_this_period",
        }
        return map_fields(data, field_map, entity="_map_profile")

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    async def create_notification(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Notification:
        async with self._use_session(session) as s:
            notification = Notification(**self._map_notification(data))
            s.add(notification)
            await s.flush()
            await s.refresh(notification)
            return notification

    async def list_unread(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[Notification]:
        async with self._read_session(session) as s:
            stmt = (
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.status.notin_(["READ", "DISMISSED"]),
                )
                .order_by(Notification.priority.asc(), Notification.scheduled_at.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def mark_read(
        self, notification_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
                .values(status="READ", read_at=datetime.now(UTC))
            )
            await s.execute(stmt)

    async def mark_dismissed(
        self, notification_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
                .values(status="DISMISSED", dismissed_at=datetime.now(UTC))
            )
            await s.execute(stmt)

    async def count_today_delivered(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> int:
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            stmt = (
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.delivered_at >= start_of_day,
                    Notification.delivered_at <= end_of_day,
                )
            )
            result = (await s.execute(stmt)).scalar() or 0
            return result

    async def list_pending_for_delivery(
        self, *, session: AsyncSession | None = None
    ) -> list[Notification]:
        async with self._read_session(session) as s:
            now = datetime.now(UTC)
            stmt = (
                select(Notification)
                .where(
                    Notification.status == "PENDING",
                    Notification.scheduled_at <= now,
                )
                .order_by(Notification.scheduled_at.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def update_status(
        self,
        notification_id: str,
        status: str,
        delivered_at: datetime | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        async with self._use_session(session) as s:
            values: dict[str, Any] = {"status": status}
            if delivered_at is not None:
                values["delivered_at"] = delivered_at
            stmt = update(Notification).where(Notification.id == notification_id).values(**values)
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Field mapping helpers — Notifications
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_notification(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "type": "type",
            "title": "title",
            "body": "body",
            "priority": "priority",
            "actionData": "action_data",
            "scheduledAt": "scheduled_at",
            "status": "status",
        }
        return map_fields(data, field_map, entity="_map_notification")

    # -----------------------------------------------------------------------
    # Prep Topics
    # -----------------------------------------------------------------------

    async def create_prep_topic(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PrepTopic:
        async with self._use_session(session) as s:
            topic = PrepTopic(**self._map_prep_topic(data))
            s.add(topic)
            await s.flush()
            await s.refresh(topic)
            return topic

    async def list_prep_topics(
        self, prep_id: str, *, session: AsyncSession | None = None
    ) -> list[PrepTopic]:
        async with self._read_session(session) as s:
            stmt = (
                select(PrepTopic)
                .where(PrepTopic.prep_id == prep_id)
                .order_by(PrepTopic.order_index.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def update_topic_mastery(
        self,
        topic_id: str,
        mastery_score: float,
        status: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(PrepTopic)
                .where(PrepTopic.id == topic_id)
                .values(mastery_score=mastery_score, status=status)
            )
            await s.execute(stmt)

    async def get_prep_progress_aggregates(
        self,
        prep_ids: list[str],
        *,
        strong_threshold: float,
        focus_threshold: float,
        session: AsyncSession | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-preparation topic and practice aggregates.

        Four grouped queries regardless of how many preparations are passed, so
        adding preparations does not add queries. Returns a mapping keyed by
        preparation id; preparations with no rows are simply absent, and callers
        substitute zeroes.

        Thresholds are parameters rather than constants so the mastery ladder
        stays owned by `services.prep_readiness` without this module importing
        it, which would be a cycle.
        """
        if not prep_ids:
            return {}

        ready = strong_threshold
        focus = focus_threshold
        aggregates: dict[str, dict[str, Any]] = {}

        def bucket(prep_id: str) -> dict[str, Any]:
            return aggregates.setdefault(
                prep_id,
                {
                    "topics_total": 0,
                    "mastery_sum": 0.0,
                    "topics_strong": 0,
                    "topics_focus": 0,
                    "topics_assessed": 0,
                    "answers_total": 0,
                    "answers_correct": 0,
                    "quizzes_total": 0,
                    "quizzes_completed": 0,
                    "practice_seconds": 0,
                },
            )

        async with self._read_session(session) as s:
            topic_rows = await s.execute(
                select(
                    PrepTopic.prep_id,
                    func.count(PrepTopic.id),
                    func.coalesce(func.sum(PrepTopic.mastery_score), 0.0),
                    func.count(PrepTopic.id).filter(PrepTopic.mastery_score >= ready),
                    func.count(PrepTopic.id).filter(PrepTopic.mastery_score < focus),
                )
                .where(PrepTopic.prep_id.in_(prep_ids))
                .group_by(PrepTopic.prep_id)
            )
            for prep_id, total, mastery_sum, strong, focus_count in topic_rows.all():
                entry = bucket(prep_id)
                entry["topics_total"] = total or 0
                entry["mastery_sum"] = float(mastery_sum or 0.0)
                entry["topics_strong"] = strong or 0
                entry["topics_focus"] = focus_count or 0

            answer_rows = await s.execute(
                select(
                    QuizSession.prep_id,
                    func.count(QuizAnswer.id),
                    func.count(QuizAnswer.id).filter(QuizAnswer.is_correct.is_(True)),
                )
                .join(QuizAnswer, QuizAnswer.quiz_session_id == QuizSession.id)
                .where(QuizSession.prep_id.in_(prep_ids))
                .group_by(QuizSession.prep_id)
            )
            for prep_id, answered, correct in answer_rows.all():
                entry = bucket(prep_id)
                entry["answers_total"] = answered or 0
                entry["answers_correct"] = correct or 0

            session_rows = await s.execute(
                select(
                    QuizSession.prep_id,
                    func.count(QuizSession.id),
                    func.count(QuizSession.id).filter(QuizSession.status == "COMPLETED"),
                    func.coalesce(func.sum(QuizSession.duration_seconds), 0),
                )
                .where(QuizSession.prep_id.in_(prep_ids))
                .group_by(QuizSession.prep_id)
            )
            for prep_id, quizzes, completed, seconds in session_rows.all():
                entry = bucket(prep_id)
                entry["quizzes_total"] = quizzes or 0
                entry["quizzes_completed"] = completed or 0
                entry["practice_seconds"] = int(seconds or 0)

            # A topic counts as assessed once it has an answered question, which
            # is stricter than reading `status`: a topic answered entirely wrong
            # keeps mastery 0 and would otherwise look untouched.
            # Counted from the banked question's own `prepId` rather than through
            # the session, now that a question belongs to the preparation.
            assessed_rows = await s.execute(
                select(
                    PrepQuestion.prep_id,
                    func.count(func.distinct(PrepQuestion.prep_topic_id)),
                )
                .join(QuizAnswer, QuizAnswer.question_id == PrepQuestion.id)
                .where(
                    PrepQuestion.prep_id.in_(prep_ids),
                    PrepQuestion.prep_topic_id.is_not(None),
                )
                .group_by(PrepQuestion.prep_id)
            )
            for prep_id, assessed in assessed_rows.all():
                bucket(prep_id)["topics_assessed"] = assessed or 0

        return aggregates

    async def list_exam_preps_by_ids(
        self, prep_ids: list[str], *, session: AsyncSession | None = None
    ) -> list[ExamPrep]:
        """Load several preparations in one query, without user scoping.

        For internal batch jobs only — the daily snapshot writer works across every
        learner by design. Anything serving a request must go through
        ``find_exam_prep`` or ``search_exam_preps``, which scope by ``user_id``.
        """
        if not prep_ids:
            return []
        async with self._read_session(session) as s:
            stmt = select(ExamPrep).where(ExamPrep.id.in_(prep_ids))
            return list((await s.execute(stmt)).scalars().all())

    async def get_prep_topic_question_counts(
        self, prep_ids: list[str], *, session: AsyncSession | None = None
    ) -> dict[str, dict[str, int]]:
        """Banked and answered question counts per topic.

        Two grouped queries regardless of how many preparations or topics are
        passed. The workspace shows "43 of 46 questions" per topic, which is not a
        column on anything and previously would have meant one request per topic
        against the paginated bank endpoint.

        Keyed by topic id, with ``question_count`` and ``answered_count``. Topics
        with no banked questions are absent; callers substitute zeroes. Unattributed
        questions (null ``prepTopicId``) are excluded, because they belong to no
        topic and adding them anywhere would overstate that topic's coverage.

        ``answered_count`` is distinct by question, so meeting the same banked
        question again in a later session does not count twice.
        """
        if not prep_ids:
            return {}

        counts: dict[str, dict[str, int]] = {}

        def bucket(topic_id: str) -> dict[str, int]:
            return counts.setdefault(topic_id, {"question_count": 0, "answered_count": 0})

        async with self._read_session(session) as s:
            banked = await s.execute(
                select(PrepQuestion.prep_topic_id, func.count(PrepQuestion.id))
                .where(
                    PrepQuestion.prep_id.in_(prep_ids),
                    PrepQuestion.prep_topic_id.is_not(None),
                )
                .group_by(PrepQuestion.prep_topic_id)
            )
            for topic_id, total in banked.all():
                bucket(topic_id)["question_count"] = total or 0

            answered = await s.execute(
                select(
                    PrepQuestion.prep_topic_id,
                    func.count(func.distinct(QuizAnswer.question_id)),
                )
                .join(QuizAnswer, QuizAnswer.question_id == PrepQuestion.id)
                .where(
                    PrepQuestion.prep_id.in_(prep_ids),
                    PrepQuestion.prep_topic_id.is_not(None),
                )
                .group_by(PrepQuestion.prep_topic_id)
            )
            for topic_id, total in answered.all():
                bucket(topic_id)["answered_count"] = total or 0

        return counts

    async def list_prep_milestone_items(
        self,
        prep_ids: list[str],
        user_id: str,
        *,
        take: int = 8,
        session: AsyncSession | None = None,
    ) -> list[tuple[StudyPlanItem, str]]:
        """Study-plan items across several preparations, nearest scheduled date first.

        Returns ``(item, prep_id)``. The dashboard's milestone rail is the same
        derivation as a single preparation's timeline, flattened, so it does not
        need one request per preparation.

        Ordering is by scheduled date rather than by status so the rail reads as a
        calendar. Scoped by ``user_id`` as well as by preparation, so a plan id is
        never a way into another learner's schedule.
        """
        if not prep_ids:
            return []
        async with self._read_session(session) as s:
            stmt = (
                select(StudyPlanItem, StudyPlan.prep_id)
                .join(StudyPlan, StudyPlanItem.plan_id == StudyPlan.id)
                .where(
                    StudyPlan.prep_id.in_(prep_ids),
                    StudyPlan.user_id == user_id,
                )
                .order_by(StudyPlanItem.scheduled_date.asc())
                .limit(take)
            )
            return [(row[0], row[1]) for row in (await s.execute(stmt)).all()]

    async def list_weakest_prep_topics(
        self, prep_ids: list[str], *, take: int = 8, session: AsyncSession | None = None
    ) -> list[PrepTopic]:
        """Weakest topics across the given preparations, lowest mastery first."""
        if not prep_ids:
            return []
        async with self._read_session(session) as s:
            stmt = (
                select(PrepTopic)
                .where(PrepTopic.prep_id.in_(prep_ids))
                .order_by(PrepTopic.mastery_score.asc(), PrepTopic.order_index.asc())
                .limit(take)
            )
            return list((await s.execute(stmt)).scalars().all())

    async def list_recent_quiz_sessions(
        self, user_id: str, *, take: int = 6, session: AsyncSession | None = None
    ) -> list[QuizSession]:
        """The learner's most recent quiz sessions across all preparations.

        `FAILED` and `GENERATING` sessions are excluded. They record a generation
        failure or an attempt still being prepared, neither of which is something
        the learner did, so surfacing them as practice history would misrepresent
        the account.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(QuizSession)
                .where(
                    QuizSession.user_id == user_id,
                    QuizSession.status.notin_(("FAILED", "GENERATING")),
                )
                .order_by(QuizSession.created_at.desc())
                .limit(take)
            )
            return list((await s.execute(stmt)).scalars().all())

    async def list_quiz_sessions_since(
        self,
        user_id: str,
        *,
        since: datetime,
        session: AsyncSession | None = None,
    ) -> list[QuizSession]:
        """Every practice session a learner started within a window.

        For behaviour analysis, which needs a *period* rather than a page: a
        `take`-limited list cannot tell consistency over 30 days from 30 sessions
        crammed into one weekend.

        `FAILED` and `GENERATING` are excluded on the same grounds as
        `list_recent_quiz_sessions` — neither is something the learner did, and
        counting a generation failure as a study session would inflate both
        consistency and time-of-day evidence.

        Ordered oldest first, because every consumer walks it chronologically to
        measure gaps.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(QuizSession)
                .where(
                    QuizSession.user_id == user_id,
                    QuizSession.status.notin_(("FAILED", "GENERATING")),
                    QuizSession.created_at >= since,
                )
                .order_by(QuizSession.created_at.asc())
            )
            return list((await s.execute(stmt)).scalars().all())

    async def find_prep_topic(
        self, topic_id: str, prep_id: str, *, session: AsyncSession | None = None
    ) -> PrepTopic | None:
        """Find a topic scoped to its preparation.

        Callers must have already verified the preparation belongs to the user,
        so scoping by ``prep_id`` is what prevents cross-preparation access.
        """
        async with self._read_session(session) as s:
            stmt = select(PrepTopic).where(
                PrepTopic.id == topic_id,
                PrepTopic.prep_id == prep_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_prep_topic(
        self, topic_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PrepTopic | None:
        async with self._use_session(session) as s:
            mapped = self._map_prep_topic(data)
            if mapped:
                await s.execute(update(PrepTopic).where(PrepTopic.id == topic_id).values(**mapped))

        async with self._use_session(None) as s:
            result = await s.execute(select(PrepTopic).where(PrepTopic.id == topic_id))
            return result.scalar_one_or_none()

    async def delete_prep_topic(
        self, topic_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            await s.execute(delete(PrepTopic).where(PrepTopic.id == topic_id))

    async def count_flashcards(self, user_id: str, *, session: AsyncSession | None = None) -> int:
        """How many flashcards a learner has. Used by the onboarding progress read."""
        async with self._read_session(session) as s:
            stmt = select(func.count()).select_from(Flashcard).where(Flashcard.user_id == user_id)
            return (await s.execute(stmt)).scalar_one() or 0

    async def count_study_plans(self, user_id: str, *, session: AsyncSession | None = None) -> int:
        """How many study plans a learner has, in any state."""
        async with self._read_session(session) as s:
            stmt = select(func.count()).select_from(StudyPlan).where(StudyPlan.user_id == user_id)
            return (await s.execute(stmt)).scalar_one() or 0

    async def count_prep_topics(self, prep_id: str, *, session: AsyncSession | None = None) -> int:
        async with self._read_session(session) as s:
            stmt = select(func.count()).select_from(PrepTopic).where(PrepTopic.prep_id == prep_id)
            return (await s.execute(stmt)).scalar_one() or 0

    # -----------------------------------------------------------------------
    # Prep Materials
    # -----------------------------------------------------------------------

    async def create_prep_material(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PrepMaterial:
        async with self._use_session(session) as s:
            material = PrepMaterial(**self._map_prep_material(data))
            s.add(material)
            await s.flush()
            await s.refresh(material)
            return material

    async def list_prep_materials(
        self, prep_id: str, *, session: AsyncSession | None = None
    ) -> list[PrepMaterial]:
        async with self._read_session(session) as s:
            stmt = (
                select(PrepMaterial)
                .where(PrepMaterial.prep_id == prep_id)
                .order_by(PrepMaterial.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def find_prep_material(
        self, material_id: str, prep_id: str, *, session: AsyncSession | None = None
    ) -> PrepMaterial | None:
        async with self._read_session(session) as s:
            stmt = select(PrepMaterial).where(
                PrepMaterial.id == material_id,
                PrepMaterial.prep_id == prep_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_prep_material(
        self, material_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PrepMaterial | None:
        async with self._use_session(session) as s:
            mapped = self._map_prep_material(data)
            if mapped:
                await s.execute(
                    update(PrepMaterial).where(PrepMaterial.id == material_id).values(**mapped)
                )

        async with self._use_session(None) as s:
            result = await s.execute(select(PrepMaterial).where(PrepMaterial.id == material_id))
            return result.scalar_one_or_none()

    async def delete_prep_material(
        self, material_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            await s.execute(delete(PrepMaterial).where(PrepMaterial.id == material_id))

    # -----------------------------------------------------------------------
    # Quiz Sessions, Questions & Answers
    # -----------------------------------------------------------------------

    async def create_quiz_session(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> QuizSession:
        async with self._use_session(session) as s:
            quiz = QuizSession(**self._map_quiz_session(data))
            s.add(quiz)
            await s.flush()
            await s.refresh(quiz)
            return quiz

    async def get_quiz_session(
        self, quiz_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> QuizSession | None:
        async with self._read_session(session) as s:
            stmt = (
                select(QuizSession)
                .options(selectinload(QuizSession.answers))
                .where(
                    QuizSession.id == quiz_id,
                    QuizSession.user_id == user_id,
                )
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def update_quiz_session(
        self, quiz_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> QuizSession | None:
        async with self._use_session(session) as s:
            mapped = self._map_quiz_session(data)
            if mapped:
                stmt = update(QuizSession).where(QuizSession.id == quiz_id).values(**mapped)
                await s.execute(stmt)

        # Re-fetch to return updated object
        async with self._use_session(None) as s:
            stmt = select(QuizSession).where(QuizSession.id == quiz_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def create_prep_question(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PrepQuestion:
        """Add a question to a preparation's bank."""
        async with self._use_session(session) as s:
            question = PrepQuestion(**self._map_prep_question(data))
            s.add(question)
            await s.flush()
            await s.refresh(question)
            return question

    async def attach_question_to_session(
        self,
        *,
        quiz_session_id: str,
        prep_question_id: str,
        order_index: int,
        session: AsyncSession | None = None,
    ) -> QuizSessionQuestion:
        """Record that a session asked a banked question, at a given position."""
        async with self._use_session(session) as s:
            link = QuizSessionQuestion(
                quiz_session_id=quiz_session_id,
                prep_question_id=prep_question_id,
                order_index=order_index,
            )
            s.add(link)
            await s.flush()
            await s.refresh(link)
            return link

    async def search_prep_questions(
        self,
        prep_id: str,
        *,
        user_id: str,
        topic_id: str | None = None,
        difficulty: str | None = None,
        source: str | None = None,
        flagged_only: bool = False,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[tuple[PrepQuestion, PrepQuestionFlag | None]], int]:
        """A page of a preparation's question bank, plus the full match count.

        The query the old schema could not express: questions belonged to a
        session, so "every question for this preparation" had no meaning.

        Each row is ``(question, flag_or_none)``. The learner's own flag is
        outer-joined in one query rather than fetched per question, and the join is
        scoped to ``user_id`` so one learner never sees another's flags.
        """
        async with self._read_session(session) as s:
            flag_join = (PrepQuestionFlag.prep_question_id == PrepQuestion.id) & (
                PrepQuestionFlag.user_id == user_id
            )

            condition = PrepQuestion.prep_id == prep_id
            if topic_id:
                condition = condition & (PrepQuestion.prep_topic_id == topic_id)
            if difficulty:
                condition = condition & (PrepQuestion.difficulty == difficulty)
            if source:
                condition = condition & (PrepQuestion.source == source)
            if flagged_only:
                condition = condition & PrepQuestionFlag.id.is_not(None)

            total = (
                await s.execute(
                    select(func.count())
                    .select_from(PrepQuestion)
                    .outerjoin(PrepQuestionFlag, flag_join)
                    .where(condition)
                )
            ).scalar_one() or 0

            stmt = (
                select(PrepQuestion, PrepQuestionFlag)
                .outerjoin(PrepQuestionFlag, flag_join)
                .where(condition)
                .order_by(PrepQuestion.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            return [(row[0], row[1]) for row in (await s.execute(stmt)).all()], total

    async def list_prep_study_plans(
        self, prep_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> list[StudyPlan]:
        """Study plans generated from a preparation, with their items eagerly loaded.

        The preparation timeline is derived from these items rather than from a
        separate milestone entity, so the items must come back with the plan.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(
                    StudyPlan.prep_id == prep_id,
                    StudyPlan.user_id == user_id,
                )
                .order_by(StudyPlan.created_at.desc())
            )
            return list((await s.execute(stmt)).scalars().all())

    async def record_practice_observation(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> PracticeObservation:
        """Append one observation. Never updated after it is written."""
        async with self._use_session(session) as s:
            observation = PracticeObservation(**self._map_practice_observation(data))
            s.add(observation)
            await s.flush()
            await s.refresh(observation)
            return observation

    async def list_bank_questions_for_reuse(
        self,
        *,
        prep_id: str,
        topic_id: str,
        difficulty: str | None = None,
        exclude_ids: list[str] | None = None,
        take: int = 5,
        session: AsyncSession | None = None,
    ) -> list[PrepQuestion]:
        """Banked questions available to ask again, least-recently-used first.

        Only possible since questions were promoted out of the session that created
        them. Ordered by ``timesAnswered`` so a session reaches for material the
        learner has seen least, rather than repeating the same few questions.
        """
        async with self._read_session(session) as s:
            condition = (PrepQuestion.prep_id == prep_id) & (PrepQuestion.prep_topic_id == topic_id)
            if difficulty:
                condition = condition & (PrepQuestion.difficulty == difficulty)
            if exclude_ids:
                condition = condition & PrepQuestion.id.notin_(exclude_ids)

            stmt = (
                select(PrepQuestion)
                .where(condition)
                .order_by(
                    PrepQuestion.times_answered.asc(),
                    PrepQuestion.created_at.asc(),
                )
                .limit(take)
            )
            return list((await s.execute(stmt)).scalars().all())

    async def list_topic_observations(
        self,
        *,
        user_id: str,
        topic_ids: list[str],
        since: datetime,
        session: AsyncSession | None = None,
    ) -> list[PracticeObservation]:
        """Observations for several topics in one read, newest first.

        Bounded by ``since`` because the competence model decays old evidence to
        near-nothing anyway — reading it would cost rows and change no answer.
        """
        if not topic_ids:
            return []
        async with self._read_session(session) as s:
            stmt = (
                select(PracticeObservation)
                .where(
                    PracticeObservation.user_id == user_id,
                    PracticeObservation.prep_topic_id.in_(topic_ids),
                    PracticeObservation.observed_at >= since,
                )
                .order_by(PracticeObservation.observed_at.desc())
            )
            return list((await s.execute(stmt)).scalars().all())

    async def list_prep_observations(
        self,
        *,
        user_id: str,
        prep_id: str,
        since: datetime,
        session: AsyncSession | None = None,
    ) -> list[PracticeObservation]:
        """Every observation for one preparation, newest first."""
        async with self._read_session(session) as s:
            stmt = (
                select(PracticeObservation)
                .where(
                    PracticeObservation.user_id == user_id,
                    PracticeObservation.prep_id == prep_id,
                    PracticeObservation.observed_at >= since,
                )
                .order_by(PracticeObservation.observed_at.desc())
            )
            return list((await s.execute(stmt)).scalars().all())

    async def find_session_question_link(
        self, *, quiz_session_id: str, prep_question_id: str, session: AsyncSession | None = None
    ) -> QuizSessionQuestion | None:
        """The link recording that a session asked a question, and its hint count."""
        async with self._read_session(session) as s:
            stmt = select(QuizSessionQuestion).where(
                QuizSessionQuestion.quiz_session_id == quiz_session_id,
                QuizSessionQuestion.prep_question_id == prep_question_id,
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def increment_session_question_hints(
        self, *, quiz_session_id: str, prep_question_id: str, session: AsyncSession | None = None
    ) -> int:
        """Count one hint taken, returning the new total.

        Incremented in SQL and returned by the same statement, so two concurrent
        hint requests cannot both read the same starting value.
        """
        async with self._use_session(session) as s:
            stmt = (
                update(QuizSessionQuestion)
                .where(
                    QuizSessionQuestion.quiz_session_id == quiz_session_id,
                    QuizSessionQuestion.prep_question_id == prep_question_id,
                )
                .values(hint_count=QuizSessionQuestion.hint_count + 1)
                .returning(QuizSessionQuestion.hint_count)
            )
            return (await s.execute(stmt)).scalar_one()

    async def upsert_readiness_snapshot(
        self,
        *,
        prep_id: str,
        captured_on: date,
        values: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> PrepReadinessSnapshot:
        """Write one day's readiness for a preparation.

        Idempotent on ``(prepId, capturedOn)``, so a retry, a re-run, or two
        workers on the same day update the row instead of duplicating the day.
        """
        async with self._use_session(session) as s:
            existing = (
                await s.execute(
                    select(PrepReadinessSnapshot).where(
                        PrepReadinessSnapshot.prep_id == prep_id,
                        PrepReadinessSnapshot.captured_on == captured_on,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                for field, value in values.items():
                    setattr(existing, field, value)
                await s.flush()
                return existing

            snapshot = PrepReadinessSnapshot(prep_id=prep_id, captured_on=captured_on, **values)
            s.add(snapshot)
            await s.flush()
            await s.refresh(snapshot)
            return snapshot

    async def list_readiness_snapshots(
        self,
        prep_id: str,
        *,
        since: date,
        session: AsyncSession | None = None,
    ) -> list[PrepReadinessSnapshot]:
        """A preparation's snapshots from `since` onwards, oldest first.

        Oldest first because a chart reads left to right, and bounded by `since`
        because a trend does not need a preparation's entire history.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(PrepReadinessSnapshot)
                .where(
                    PrepReadinessSnapshot.prep_id == prep_id,
                    PrepReadinessSnapshot.captured_on >= since,
                )
                .order_by(PrepReadinessSnapshot.captured_on.asc())
            )
            return list((await s.execute(stmt)).scalars().all())

    async def list_snapshot_candidate_preps(
        self, *, skip: int = 0, take: int = 100, session: AsyncSession | None = None
    ) -> list[ExamPrep]:
        """Unfinished preparations, for the daily snapshot writer.

        Completed preparations are excluded: their readiness no longer moves, so a
        further snapshot would add a row per day saying nothing new.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(ExamPrep)
                .where(ExamPrep.status != "COMPLETED")
                .order_by(ExamPrep.created_at.asc())
                .offset(skip)
                .limit(take)
            )
            return list((await s.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Daily learning snapshots (Reflect trends)
    # ------------------------------------------------------------------

    async def upsert_daily_snapshot(
        self,
        *,
        user_id: str,
        snapshot_date: date,
        values: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> DailyLearningSnapshot:
        """Write one of a learner's days.

        Idempotent on ``(userId, snapshotDate)``, so the nightly task retrying, a manual
        re-run, a backfill crossing a day already recorded, or two workers on the same night
        update the row instead of duplicating the day.

        Select-then-set rather than an ``ON CONFLICT`` upsert, matching
        ``upsert_readiness_snapshot``: the unique constraint is the backstop, not the
        mechanism. ``values`` is keyed by Python attribute name because it is applied through
        ``setattr`` and the constructor.
        """
        async with self._use_session(session) as s:
            existing = (
                await s.execute(
                    select(DailyLearningSnapshot).where(
                        DailyLearningSnapshot.user_id == user_id,
                        DailyLearningSnapshot.snapshot_date == snapshot_date,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                for field, value in values.items():
                    setattr(existing, field, value)
                await s.flush()
                return existing

            snapshot = DailyLearningSnapshot(user_id=user_id, snapshot_date=snapshot_date, **values)
            s.add(snapshot)
            await s.flush()
            await s.refresh(snapshot)
            return snapshot

    async def list_daily_snapshots(
        self,
        user_id: str,
        *,
        since: date,
        until: date | None = None,
        session: AsyncSession | None = None,
    ) -> list[DailyLearningSnapshot]:
        """A learner's snapshots across a bounded range, oldest first.

        Oldest first because a chart reads left to right. ``until`` is inclusive and optional;
        the trend read leaves it open, and the backfill uses it to ask what a window already
        holds without loading a learner's whole history.

        Returns only days that were captured. A gap stays a gap — the caller must not fill it
        by carrying the previous value forward, which would draw a flat line through days the
        learner was never observed on.
        """
        async with self._read_session(session) as s:
            stmt = select(DailyLearningSnapshot).where(
                DailyLearningSnapshot.user_id == user_id,
                DailyLearningSnapshot.snapshot_date >= since,
            )
            if until is not None:
                stmt = stmt.where(DailyLearningSnapshot.snapshot_date <= until)
            stmt = stmt.order_by(DailyLearningSnapshot.snapshot_date.asc())
            return list((await s.execute(stmt)).scalars().all())

    async def upsert_question_flag(
        self,
        *,
        user_id: str,
        prep_question_id: str,
        note: str | None = None,
        session: AsyncSession | None = None,
    ) -> PrepQuestionFlag:
        """Flag a question, or update the note on an existing flag.

        Idempotent, matching answer submission: pressing the button twice is not an
        error, and the unique constraint means it cannot produce a duplicate.
        """
        async with self._use_session(session) as s:
            existing = (
                await s.execute(
                    select(PrepQuestionFlag).where(
                        PrepQuestionFlag.user_id == user_id,
                        PrepQuestionFlag.prep_question_id == prep_question_id,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                if note is not None:
                    existing.note = note
                    await s.flush()
                return existing

            flag = PrepQuestionFlag(user_id=user_id, prep_question_id=prep_question_id, note=note)
            s.add(flag)
            await s.flush()
            await s.refresh(flag)
            return flag

    async def delete_question_flag(
        self, *, user_id: str, prep_question_id: str, session: AsyncSession | None = None
    ) -> bool:
        """Remove a learner's flag. Returns whether one was there to remove."""
        async with self._use_session(session) as s:
            result = await s.execute(
                delete(PrepQuestionFlag).where(
                    PrepQuestionFlag.user_id == user_id,
                    PrepQuestionFlag.prep_question_id == prep_question_id,
                )
            )
            return bool(result.rowcount)

    async def find_prep_question(
        self, question_id: str, prep_id: str, *, session: AsyncSession | None = None
    ) -> PrepQuestion | None:
        """Find a banked question scoped to its preparation.

        Callers must have verified the preparation belongs to the user, so scoping
        by ``prep_id`` is what prevents reaching another learner's question.
        """
        async with self._read_session(session) as s:
            stmt = select(PrepQuestion).where(
                PrepQuestion.id == question_id,
                PrepQuestion.prep_id == prep_id,
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def record_question_attempt(
        self, question_id: str, *, correct: bool, session: AsyncSession | None = None
    ) -> None:
        """Increment a banked question's lifetime attempt statistics.

        Incremented in SQL rather than read-modify-written, so concurrent answers
        to the same question cannot lose a count.
        """
        async with self._use_session(session) as s:
            await s.execute(
                update(PrepQuestion)
                .where(PrepQuestion.id == question_id)
                .values(
                    times_answered=PrepQuestion.times_answered + 1,
                    times_correct=PrepQuestion.times_correct + (1 if correct else 0),
                )
            )

    async def create_quiz_answer(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> QuizAnswer:
        async with self._use_session(session) as s:
            answer = QuizAnswer(**self._map_quiz_answer(data))
            s.add(answer)
            await s.flush()
            await s.refresh(answer)
            return answer

    async def list_practice_days(
        self,
        user_id: str,
        *,
        since: datetime,
        prep_id: str | None = None,
        session: AsyncSession | None = None,
    ) -> list[date]:
        """Distinct UTC dates on which the learner completed a quiz session.

        Bounded by ``since`` so the streak calculation reads a fixed window rather
        than the learner's whole history. Grouping in SQL keeps one row per day
        instead of one per session.

        ``prep_id`` narrows the window to one preparation, which is what the
        workspace shows. Account-wide and per-preparation streaks are deliberately
        different numbers: practising chemistry does not advance a statistics
        streak, and a workspace claiming otherwise would be telling the learner
        they had prepared when they had not.
        """
        async with self._read_session(session) as s:
            day = func.date(QuizSession.completed_at)
            condition = (
                (QuizSession.user_id == user_id)
                & (QuizSession.status == "COMPLETED")
                & QuizSession.completed_at.is_not(None)
                & (QuizSession.completed_at >= since)
            )
            if prep_id:
                condition = condition & (QuizSession.prep_id == prep_id)
            stmt = select(day).where(condition).group_by(day).order_by(day.desc())
            rows = (await s.execute(stmt)).scalars().all()
            days: list[date] = []
            for row in rows:
                # `func.date` returns a date on Postgres and a string on SQLite.
                days.append(row if isinstance(row, date) else datetime.fromisoformat(row).date())
            return days

    async def find_quiz_question(
        self, question_id: str, quiz_id: str, *, session: AsyncSession | None = None
    ) -> PrepQuestion | None:
        """Find a question scoped to the session that asked it.

        Callers must have already verified the session belongs to the user, so this
        scoping is what stops a question id from another session — including
        another learner's — being answered and its answer key read back in the
        response.

        The scoping survives the move to a shared bank: a banked question is
        answerable only through a session that actually asked it, so a question
        the learner could legitimately see in the bank is still not answerable in
        an unrelated session.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(PrepQuestion)
                .join(
                    QuizSessionQuestion,
                    QuizSessionQuestion.prep_question_id == PrepQuestion.id,
                )
                .where(
                    PrepQuestion.id == question_id,
                    QuizSessionQuestion.quiz_session_id == quiz_id,
                )
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def find_quiz_answer(
        self, quiz_id: str, question_id: str, *, session: AsyncSession | None = None
    ) -> QuizAnswer | None:
        """The existing answer for a question in a session, if there is one.

        Returns the first match rather than requiring uniqueness: sessions
        created before answers were made idempotent may hold more than one row
        per question, and a read should not raise on that legacy data.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(QuizAnswer)
                .where(
                    QuizAnswer.quiz_session_id == quiz_id,
                    QuizAnswer.question_id == question_id,
                )
                .order_by(QuizAnswer.created_at.asc())
            )
            result = await s.execute(stmt)
            return result.scalars().first()

    async def sync_quiz_correct_count(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> None:
        """Set a session's cached ``correctCount`` from its persisted answers.

        One statement, with the count as a subquery. Reading it and then writing it
        back was two round trips for a value the database can compute in place —
        which the learner waits through on every answer — and it also left a window
        in which a concurrent answer could be counted and then overwritten.

        Still derived rather than incremented, so the count cannot drift from the
        answers or exceed the questions asked.
        """
        async with self._use_session(session) as s:
            correct = (
                select(func.count(func.distinct(QuizAnswer.question_id)))
                .where(
                    QuizAnswer.quiz_session_id == quiz_id,
                    QuizAnswer.is_correct.is_(True),
                )
                .scalar_subquery()
            )
            await s.execute(
                update(QuizSession).where(QuizSession.id == quiz_id).values(correct_count=correct)
            )

    async def count_correct_quiz_answers(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> int:
        """Count distinct correctly-answered questions in a session.

        Counting questions rather than answer rows is what makes the score
        authoritative: it is recomputed from persisted answers instead of
        accumulated, so it cannot drift, and duplicate rows on legacy sessions
        cannot inflate it.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(func.count(func.distinct(QuizAnswer.question_id)))
                .select_from(QuizAnswer)
                .where(
                    QuizAnswer.quiz_session_id == quiz_id,
                    QuizAnswer.is_correct.is_(True),
                )
            )
            return (await s.execute(stmt)).scalar_one() or 0

    async def list_quiz_answers(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> list[QuizAnswer]:
        async with self._read_session(session) as s:
            stmt = select(QuizAnswer).where(QuizAnswer.quiz_session_id == quiz_id)
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_quiz_questions(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> list[tuple[PrepQuestion, QuizSessionQuestion]]:
        """A session's questions paired with the link that placed them there.

        Returns ``(question, link)`` rather than bare questions, because everything
        session-specific lives on the link: the position, and how many hints were
        taken. The same banked question can appear at a different position, with a
        different hint count, in a later session.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(PrepQuestion, QuizSessionQuestion)
                .join(
                    QuizSessionQuestion,
                    QuizSessionQuestion.prep_question_id == PrepQuestion.id,
                )
                .where(QuizSessionQuestion.quiz_session_id == quiz_id)
                .order_by(QuizSessionQuestion.order_index.asc())
            )
            result = await s.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]

    async def list_prep_quizzes(
        self,
        prep_id: str,
        user_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[QuizSession]:
        """Return all quiz sessions for a preparation, newest first."""
        async with self._read_session(session) as s:
            stmt = (
                select(QuizSession)
                .where(
                    QuizSession.prep_id == prep_id,
                    QuizSession.user_id == user_id,
                )
                .order_by(QuizSession.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Field mapping helpers — Prep Topics, Materials & Quizzes
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_prep_topic(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "prepId": "prep_id",
            "title": "title",
            "description": "description",
            "category": "category",
            "estimatedMinutes": "estimated_minutes",
            "orderIndex": "order_index",
            "masteryScore": "mastery_score",
            "targetMastery": "target_mastery",
            "status": "status",
        }
        return map_fields(data, field_map, entity="_map_prep_topic")

    @staticmethod
    def _map_prep_material(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "prepId": "prep_id",
            "filename": "filename",
            "url": "url",
            "fileType": "file_type",
            "size": "size",
            "extractedText": "extracted_text",
            "category": "category",
            "label": "label",
        }
        return map_fields(data, field_map, entity="_map_prep_material")

    @staticmethod
    def _map_quiz_session(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "prepId": "prep_id",
            "mode": "mode",
            "topicId": "topic_id",
            "status": "status",
            "totalQuestions": "total_questions",
            "correctCount": "correct_count",
            "scorePercentage": "score_percentage",
            "durationSeconds": "duration_seconds",
            "generationMs": "generation_ms",
            "generationStage": "generation_stage",
            "completedAt": "completed_at",
        }
        return map_fields(data, field_map, entity="_map_quiz_session")

    @staticmethod
    def _map_prep_question(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "prepId": "prep_id",
            "prepTopicId": "prep_topic_id",
            "questionText": "question_text",
            "questionType": "question_type",
            "options": "options",
            "correctAnswer": "correct_answer",
            "explanation": "explanation",
            "difficulty": "difficulty",
            "source": "source",
            "sourceYear": "source_year",
            "examTip": "exam_tip",
            "hintNudge": "hint_nudge",
        }
        return map_fields(data, field_map, entity="_map_prep_question")

    @staticmethod
    def _map_practice_observation(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "prepId": "prep_id",
            "prepTopicId": "prep_topic_id",
            "prepQuestionId": "prep_question_id",
            "quizSessionId": "quiz_session_id",
            "isCorrect": "is_correct",
            "responseMs": "response_ms",
            "hintUsed": "hint_used",
            "hintCount": "hint_count",
            "difficulty": "difficulty",
            "observedAt": "observed_at",
        }
        return map_fields(data, field_map, entity="_map_practice_observation")

    @staticmethod
    def _map_quiz_answer(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "quizSessionId": "quiz_session_id",
            "questionId": "question_id",
            "userAnswer": "user_answer",
            "isCorrect": "is_correct",
            "timeTakenSeconds": "time_taken_seconds",
        }
        return map_fields(data, field_map, entity="_map_quiz_answer")

    # -----------------------------------------------------------------------
    # Study Plans
    # -----------------------------------------------------------------------

    async def create_study_plan(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> StudyPlan:
        async with self._use_session(session) as s:
            plan = StudyPlan(**self._map_study_plan(data))
            s.add(plan)
            await s.flush()
            await s.refresh(plan)
            return plan

    async def get_study_plan(
        self, plan_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> StudyPlan | None:
        async with self._read_session(session) as s:
            stmt = (
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(
                    StudyPlan.id == plan_id,
                    StudyPlan.user_id == user_id,
                )
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_active_plans(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[StudyPlan]:
        async with self._read_session(session) as s:
            stmt = (
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(
                    StudyPlan.user_id == user_id,
                    StudyPlan.status == "ACTIVE",
                )
                .order_by(StudyPlan.deadline.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_plans_paginated(
        self,
        user_id: str,
        *,
        status: str | None = None,
        search: str | None = None,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[StudyPlan], int]:
        """A page of the learner's plans, **without** their items.

        Two problems with the previous listing. It hard-filtered `status == "ACTIVE"`,
        so a "Completed" or "Paused" tab could never match anything. And it eager-loaded
        every item of every plan, which an "all plans" page cannot afford — a learner
        with ten 40-item plans paid for 400 rows to render ten cards showing counts that
        are already stored on the plan.

        Ordered by deadline, tie-broken by id so paging cannot repeat or drop a plan.
        """
        async with self._read_session(session) as s:
            conditions: list[Any] = [StudyPlan.user_id == user_id]
            if status:
                conditions.append(StudyPlan.status == status)
            if search:
                conditions.append(
                    ilike_any(search, StudyPlan.title, StudyPlan.goal_description)
                )

            total = (
                await s.execute(select(func.count()).select_from(StudyPlan).where(*conditions))
            ).scalar_one() or 0
            rows = await s.execute(
                select(StudyPlan)
                .where(*conditions)
                .order_by(StudyPlan.deadline.asc(), StudyPlan.id.asc())
                .offset(skip)
                .limit(take)
            )
            return list(rows.scalars().all()), total

    async def update_study_plan(
        self,
        plan_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> StudyPlan | None:
        """Update a plan the caller owns, returning ``None`` when they do not.

        Ownership is in the ``UPDATE`` predicate rather than a preceding ``SELECT``, so
        there is no window between the check and the write.
        """
        async with self._use_session(session) as s:
            mapped = self._map_study_plan(data)
            if mapped:
                result = await s.execute(
                    update(StudyPlan)
                    .where(StudyPlan.id == plan_id, StudyPlan.user_id == user_id)
                    .values(**mapped)
                )
                if result.rowcount == 0:
                    return None
            refreshed = await s.execute(
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(StudyPlan.id == plan_id, StudyPlan.user_id == user_id)
            )
            return refreshed.scalar_one_or_none()

    async def delete_study_plan(
        self, plan_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete a plan and its items.

        Cascading is right here, unlike deck deletion: a plan item is not content the
        learner authored independently of the plan — it is a scheduled slot that has no
        meaning without one. `StudyPlan.items` already declares
        ``cascade="all, delete-orphan"`` and the foreign key is ``CASCADE``, so both
        layers agree, which is what the deck relationship did not.
        """
        async with self._use_session(session) as s:
            owned = await s.execute(
                select(StudyPlan.id).where(StudyPlan.id == plan_id, StudyPlan.user_id == user_id)
            )
            if owned.scalar_one_or_none() is None:
                return False
            await s.execute(delete(StudyPlanItem).where(StudyPlanItem.plan_id == plan_id))
            await s.execute(delete(StudyPlan).where(StudyPlan.id == plan_id))
            return True

    async def list_items_due_by(
        self,
        user_id: str,
        *,
        until: datetime,
        statuses: tuple[str, ...] = ("PENDING",),
        session: AsyncSession | None = None,
    ) -> list[tuple[StudyPlanItem, StudyPlan]]:
        """Items scheduled on or before ``until``, across the learner's active plans.

        Returns each item with its plan, because a cross-plan list is unreadable without
        saying which plan each row came from — and fetching the plans separately would
        be a query per row.

        Paused and superseded plans are excluded. Pausing is a statement that the
        learner is not working on this now, so continuing to present its tasks as due
        today would ignore what they asked for.
        """
        async with self._read_session(session) as s:
            rows = await s.execute(
                select(StudyPlanItem, StudyPlan)
                .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
                .where(
                    StudyPlan.user_id == user_id,
                    StudyPlan.status == "ACTIVE",
                    StudyPlanItem.status.in_(statuses),
                    StudyPlanItem.scheduled_date <= until,
                )
                .order_by(StudyPlanItem.scheduled_date.asc(), StudyPlanItem.id.asc())
            )
            return [(item, plan) for item, plan in rows.all()]

    async def update_plan_status(
        self, plan_id: str, status: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = update(StudyPlan).where(StudyPlan.id == plan_id).values(status=status)
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Study Plan Items
    # -----------------------------------------------------------------------

    async def create_plan_item(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> StudyPlanItem:
        async with self._use_session(session) as s:
            item = StudyPlanItem(**self._map_plan_item(data))
            s.add(item)
            await s.flush()
            await s.refresh(item)
            return item

    async def update_plan_item(
        self,
        item_id: str,
        data: dict[str, Any],
        *,
        plan_id: str,
        session: AsyncSession | None = None,
    ) -> StudyPlanItem | None:
        """Update an item, scoped to the plan it must belong to.

        ``plan_id`` is required rather than optional. This method previously matched on
        the item id alone, and the only caller checked that the *plan* belonged to the
        learner without checking that the *item* belonged to the plan — so
        ``POST /study-plans/{myPlan}/items/{someoneElsesItem}/complete`` wrote
        ``status`` and ``completedAt`` onto a row the caller did not own. Making the
        parameter mandatory means a caller cannot reintroduce that by forgetting it.

        Returns ``None`` when the item is not in that plan, which callers render as a
        `404` — the same response as a missing item, so the route cannot be used to
        discover which item ids exist.
        """
        async with self._use_session(session) as s:
            mapped = self._map_plan_item(data)
            if mapped:
                stmt = (
                    update(StudyPlanItem)
                    .where(StudyPlanItem.id == item_id, StudyPlanItem.plan_id == plan_id)
                    .values(**mapped)
                )
                result = await s.execute(stmt)
                if result.rowcount == 0:
                    return None
            refreshed = await s.execute(
                select(StudyPlanItem).where(
                    StudyPlanItem.id == item_id, StudyPlanItem.plan_id == plan_id
                )
            )
            return refreshed.scalar_one_or_none()

    async def delete_plan_item(
        self, item_id: str, *, plan_id: str, session: AsyncSession | None = None
    ) -> bool:
        """Remove an item from a plan. Scoped to the plan for the same reason."""
        async with self._use_session(session) as s:
            stmt = delete(StudyPlanItem).where(
                StudyPlanItem.id == item_id, StudyPlanItem.plan_id == plan_id
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    async def list_plan_items(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> list[StudyPlanItem]:
        async with self._read_session(session) as s:
            stmt = (
                select(StudyPlanItem)
                .where(StudyPlanItem.plan_id == plan_id)
                .order_by(StudyPlanItem.scheduled_date.asc(), StudyPlanItem.id.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Study Plan — connected learning (linked courses, reference materials)
    # -----------------------------------------------------------------------

    async def link_plan_courses(
        self,
        plan_id: str,
        course_ids: list[str],
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Link courses to a plan, ignoring ones already linked.

        Existing links are read first and skipped rather than relying on the unique
        constraint to reject them, because a constraint violation aborts the whole
        transaction — so re-sending a selection that overlaps what is already linked
        would fail the request instead of being the no-op the learner expects.
        """
        if not course_ids:
            return 0
        async with self._use_session(session) as s:
            existing = set(
                (
                    await s.execute(
                        select(StudyPlanCourse.course_id).where(StudyPlanCourse.plan_id == plan_id)
                    )
                )
                .scalars()
                .all()
            )
            added = 0
            for course_id in dict.fromkeys(course_ids):
                if course_id in existing:
                    continue
                s.add(StudyPlanCourse(plan_id=plan_id, course_id=course_id))
                added += 1
            return added

    async def unlink_plan_course(
        self, plan_id: str, course_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        async with self._use_session(session) as s:
            result = await s.execute(
                delete(StudyPlanCourse).where(
                    StudyPlanCourse.plan_id == plan_id,
                    StudyPlanCourse.course_id == course_id,
                )
            )
            return result.rowcount > 0

    async def list_plan_courses(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> list[dict[str, Any]]:
        """Linked courses with their titles, in one join.

        The title comes from `Course` rather than being copied onto the link row, so a
        renamed course is renamed here too. One query rather than one per link.
        """
        from src.domains.knowledge.db_models import Course

        async with self._read_session(session) as s:
            rows = await s.execute(
                select(
                    StudyPlanCourse.course_id,
                    Course.title,
                    Course.difficulty,
                    StudyPlanCourse.created_at,
                )
                .join(Course, Course.id == StudyPlanCourse.course_id)
                .where(StudyPlanCourse.plan_id == plan_id)
                .order_by(StudyPlanCourse.created_at.asc())
            )
            return [
                {
                    "course_id": row[0],
                    "title": row[1],
                    "difficulty": row[2],
                    "linked_at": row[3],
                }
                for row in rows.all()
            ]

    async def find_courses_owned_by(
        self, user_id: str, course_ids: list[str], *, session: AsyncSession | None = None
    ) -> set[str]:
        """Which of these course ids the learner can actually link.

        Checked before writing, because the foreign key only proves a course exists —
        without this a learner could attach someone else's course to their plan and read
        its title off the detail page. Same hole that let cards be filed into another
        learner's deck.
        """
        if not course_ids:
            return set()
        from src.domains.knowledge.db_models import Course

        async with self._read_session(session) as s:
            rows = await s.execute(
                select(Course.id).where(Course.id.in_(course_ids), Course.user_id == user_id)
            )
            return set(rows.scalars().all())

    async def create_plan_material(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> StudyPlanMaterial:
        async with self._use_session(session) as s:
            material = StudyPlanMaterial(
                plan_id=data["planId"],
                filename=data["filename"],
                url=data["url"],
                file_type=data.get("fileType"),
                size=data.get("size"),
            )
            s.add(material)
            await s.flush()
            await s.refresh(material)
            return material

    async def list_plan_materials(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> list[StudyPlanMaterial]:
        async with self._read_session(session) as s:
            rows = await s.execute(
                select(StudyPlanMaterial)
                .where(StudyPlanMaterial.plan_id == plan_id)
                .order_by(StudyPlanMaterial.created_at.asc())
            )
            return list(rows.scalars().all())

    async def get_plan_material(
        self, material_id: str, *, plan_id: str, session: AsyncSession | None = None
    ) -> StudyPlanMaterial | None:
        """Scoped to the plan, so a material id from another plan cannot be reached."""
        async with self._read_session(session) as s:
            rows = await s.execute(
                select(StudyPlanMaterial).where(
                    StudyPlanMaterial.id == material_id,
                    StudyPlanMaterial.plan_id == plan_id,
                )
            )
            return rows.scalar_one_or_none()

    async def delete_plan_material(
        self, material_id: str, *, plan_id: str, session: AsyncSession | None = None
    ) -> bool:
        async with self._use_session(session) as s:
            result = await s.execute(
                delete(StudyPlanMaterial).where(
                    StudyPlanMaterial.id == material_id,
                    StudyPlanMaterial.plan_id == plan_id,
                )
            )
            return result.rowcount > 0

    async def list_plans_due_check_in(
        self,
        *,
        before: datetime,
        limit: int = 500,
        session: AsyncSession | None = None,
    ) -> list[StudyPlan]:
        """Active plans that opted into the weekly check-in and are due one.

        Due means never checked in, or last checked in before ``before``. Comparing
        against a stored timestamp rather than assuming the schedule fired on time is
        what makes the task idempotent: a retry inside the same week finds nothing, and a
        missed week sends one notification rather than catching up with several.
        """
        async with self._read_session(session) as s:
            rows = await s.execute(
                select(StudyPlan)
                .where(
                    StudyPlan.weekly_check_in.is_(True),
                    StudyPlan.status == "ACTIVE",
                    or_(
                        StudyPlan.last_check_in_at.is_(None),
                        StudyPlan.last_check_in_at < before,
                    ),
                )
                .order_by(StudyPlan.last_check_in_at.asc().nullsfirst())
                .limit(limit)
            )
            return list(rows.scalars().all())

    async def get_plan_metrics(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> dict[str, Any]:
        """Figures about a plan's progress that its columns do not already hold.

        All four are derived from the plan's own items, so they cannot contradict the
        item list rendered beside them, and all four are genuinely about *this plan* —
        deliberately not borrowed from another domain to fill a tile.

        - ``completed_minutes`` is the estimated minutes on completed items. That is
          *planned* effort for work that got done, not measured time at a desk; nothing
          here observes how long a learner actually spent, and callers must not label it
          as though it did.
        - ``practice_completed`` counts completed items that were practice or review
          rather than first-pass study.
        - ``skipped`` is reported because a plan where a third of the work was skipped
          reads very differently from one where it was all done.
        - ``active_dates`` are the distinct days something was completed, newest first,
          for a plan-scoped streak. Not the flashcard review streak, which measures a
          different activity and would be that number wearing this label.
        """
        async with self._read_session(session) as s:
            practice_types = ("REVIEW", "PRACTICE")
            row = (
                await s.execute(
                    select(
                        func.coalesce(
                            func.sum(StudyPlanItem.estimated_minutes).filter(
                                StudyPlanItem.status == "COMPLETED"
                            ),
                            0,
                        ),
                        func.coalesce(func.sum(StudyPlanItem.estimated_minutes), 0),
                        func.count(StudyPlanItem.id).filter(
                            StudyPlanItem.status == "COMPLETED",
                            StudyPlanItem.item_type.in_(practice_types),
                        ),
                        func.count(StudyPlanItem.id).filter(StudyPlanItem.status == "SKIPPED"),
                    ).where(StudyPlanItem.plan_id == plan_id)
                )
            ).one()

            day = func.date(StudyPlanItem.completed_at)
            days = (
                (
                    await s.execute(
                        select(day)
                        .where(
                            StudyPlanItem.plan_id == plan_id,
                            StudyPlanItem.completed_at.is_not(None),
                        )
                        .group_by(day)
                        .order_by(day.desc())
                    )
                )
                .scalars()
                .all()
            )

            return {
                "completed_minutes": int(row[0] or 0),
                "planned_minutes": int(row[1] or 0),
                "practice_completed": int(row[2] or 0),
                "skipped_items": int(row[3] or 0),
                "active_dates": [value for value in (_as_date(d) for d in days) if value],
            }

    async def count_plans_by_status(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> dict[str, int]:
        """How many plans the learner has in each status, and the active weekly target.

        One grouped query rather than a `COUNT` per tab. The plan library shows an active
        count, a completed count and a weekly goal at the same time, and reading each from a
        separate filtered list request would make three page-sized queries to produce three
        integers.

        The `"weeklyGoalTotal"` key is the sum of `weeklyGoalMinutes` over **active** plans
        only: a paused plan's target is not something the learner is working towards this
        week, and a completed plan's certainly is not.
        """
        async with self._read_session(session) as s:
            rows = (
                await s.execute(
                    select(StudyPlan.status, func.count(StudyPlan.id))
                    .where(StudyPlan.user_id == user_id)
                    .group_by(StudyPlan.status)
                )
            ).all()
            goal = (
                await s.execute(
                    select(func.sum(StudyPlan.weekly_goal_minutes)).where(
                        StudyPlan.user_id == user_id, StudyPlan.status == "ACTIVE"
                    )
                )
            ).scalar()

        counts = {str(status): int(count or 0) for status, count in rows}
        # `None`, not `0`, when no active plan states a target. Zero would render as a goal
        # of nothing and make any percentage against it a division by zero.
        counts["weeklyGoalTotal"] = int(goal) if goal else 0
        return counts

    async def completed_minutes_between(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Planned minutes on items the learner completed in a window, across their plans.

        Planned, not measured. `estimatedMinutes` is what the item was scheduled to take;
        nothing records how long it actually took, so a caller must not label this as time
        spent. Named for the window rather than "this week" because the caller decides where
        the learner's week begins — that depends on their timezone, which this does not know.
        """
        async with self._read_session(session) as s:
            total = (
                await s.execute(
                    select(func.sum(StudyPlanItem.estimated_minutes))
                    .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
                    .where(
                        StudyPlan.user_id == user_id,
                        StudyPlanItem.status == "COMPLETED",
                        StudyPlanItem.completed_at.is_not(None),
                        StudyPlanItem.completed_at >= start,
                        StudyPlanItem.completed_at < end,
                    )
                )
            ).scalar()
        return int(total or 0)

    async def list_plan_items_between(
        self,
        plan_id: str,
        start: datetime,
        end: datetime,
        *,
        session: AsyncSession | None = None,
    ) -> list[StudyPlanItem]:
        """A plan's items scheduled inside a window, in schedule order."""
        async with self._read_session(session) as s:
            rows = await s.execute(
                select(StudyPlanItem)
                .where(
                    StudyPlanItem.plan_id == plan_id,
                    StudyPlanItem.scheduled_date >= start,
                    StudyPlanItem.scheduled_date < end,
                )
                .order_by(StudyPlanItem.scheduled_date.asc(), StudyPlanItem.id.asc())
            )
            return list(rows.scalars().all())

    async def summarise_plan_phases(
        self, plan_ids: list[str], *, session: AsyncSession | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Each plan's phases, in schedule order, with their span and their counts.

        Aggregated in SQL rather than by loading the items and grouping in Python. The
        whole reason the list response omits items is that a page of ten 40-item plans is
        400 rows to render ten cards; fetching them here to derive a phase label would
        reintroduce exactly that cost behind a different name. This is one row per plan
        per phase — four or five per plan.

        A phase's ordinal, span and progress are all derived, which is why `phase` is a
        label on the item and not a table: there is nothing to store that these rows do
        not already say.

        Plans whose items carry no phase are absent from the result rather than present
        with an empty list, so a caller reads "not grouped" from the plan being missing.
        """
        if not plan_ids:
            return {}

        async with self._read_session(session) as s:
            rows = (
                await s.execute(
                    select(
                        StudyPlanItem.plan_id,
                        StudyPlanItem.phase,
                        func.min(StudyPlanItem.scheduled_date),
                        func.max(StudyPlanItem.scheduled_date),
                        func.count(StudyPlanItem.id),
                        func.count(StudyPlanItem.id).filter(StudyPlanItem.status == "COMPLETED"),
                    )
                    .where(
                        StudyPlanItem.plan_id.in_(plan_ids),
                        StudyPlanItem.phase.is_not(None),
                    )
                    .group_by(StudyPlanItem.plan_id, StudyPlanItem.phase)
                    # Ordered by when the phase starts, which is the order a roadmap reads
                    # in. Tie-broken by label so two phases beginning on the same day do
                    # not swap places between requests and renumber themselves.
                    .order_by(
                        StudyPlanItem.plan_id.asc(),
                        func.min(StudyPlanItem.scheduled_date).asc(),
                        StudyPlanItem.phase.asc(),
                    )
                )
            ).all()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for plan_id, phase, start, end, total, completed in rows:
            phases = grouped.setdefault(plan_id, [])
            phases.append(
                {
                    "label": phase,
                    # Position in the plan, 1-based. Assigned from the list length before
                    # the append, which the query's ordering makes the schedule order.
                    "number": len(phases) + 1,
                    "start": start,
                    "end": end,
                    "total_items": int(total or 0),
                    "completed_items": int(completed or 0),
                }
            )
        return grouped

    async def next_pending_items(
        self, plan_ids: list[str], *, session: AsyncSession | None = None
    ) -> dict[str, StudyPlanItem]:
        """The earliest still-pending item of each plan.

        What a plan card means by "Up next". One row per plan via a window function rather
        than a query per plan, because a page of twenty plans would otherwise be twenty
        round trips to print twenty lines of text.

        Plans with nothing pending are absent: the work is done, or all of it was skipped,
        and both cases want a different label rather than a blank one.
        """
        if not plan_ids:
            return {}

        ranked = (
            select(
                StudyPlanItem,
                func.row_number()
                .over(
                    partition_by=StudyPlanItem.plan_id,
                    # Tie-broken by id so a plan with two items on one day does not change
                    # its "Up next" between requests.
                    order_by=(StudyPlanItem.scheduled_date.asc(), StudyPlanItem.id.asc()),
                )
                .label("rank"),
            )
            .where(
                StudyPlanItem.plan_id.in_(plan_ids),
                StudyPlanItem.status == "PENDING",
            )
            .subquery()
        )
        entity = aliased(StudyPlanItem, ranked)

        async with self._read_session(session) as s:
            rows = (await s.execute(select(entity).where(ranked.c.rank == 1))).scalars().all()

        return {item.plan_id: item for item in rows}

    async def recount_plan_progress(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> tuple[int, int]:
        """Recompute a plan's item counts from its items, and store them.

        ``completedItems`` was maintained by incrementing it on every completion,
        without checking whether the item was already complete — so completing the same
        item twice counted twice, and progress could exceed 100%. Skipping and
        uncompleting an item make an incremented counter harder still to keep honest.

        Deriving it removes the class of bug rather than the instance: whatever the
        caller did to the items, the stored counts are what the items say. Returns
        ``(completed, total)``.
        """
        async with self._use_session(session) as s:
            counts = await s.execute(
                select(
                    func.count(StudyPlanItem.id),
                    func.count(StudyPlanItem.id).filter(StudyPlanItem.status == "COMPLETED"),
                ).where(StudyPlanItem.plan_id == plan_id)
            )
            total, completed = counts.one()
            total = int(total or 0)
            completed = int(completed or 0)
            await s.execute(
                update(StudyPlan)
                .where(StudyPlan.id == plan_id)
                .values(total_items=total, completed_items=completed)
            )
            return completed, total

    # -----------------------------------------------------------------------
    # Field mapping helpers — Study Plans
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_study_plan(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "goalDescription": "goal_description",
            "deadline": "deadline",
            "prepId": "prep_id",
            "status": "status",
            "strategy": "strategy",
            "weeklyGoalMinutes": "weekly_goal_minutes",
            "sessionsPerWeek": "sessions_per_week",
            "sessionMinutes": "session_minutes",
            "preferredDays": "preferred_days",
            "shape": "shape",
            "skills": "skills",
            "generateReviewCards": "generate_review_cards",
            "weeklyCheckIn": "weekly_check_in",
            "reviewDeckId": "review_deck_id",
            "lastCheckInAt": "last_check_in_at",
            "totalItems": "total_items",
            "completedItems": "completed_items",
        }
        return map_fields(data, field_map, entity="_map_study_plan")

    @staticmethod
    def _map_plan_item(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "planId": "plan_id",
            "title": "title",
            "description": "description",
            "scheduledDate": "scheduled_date",
            "estimatedMinutes": "estimated_minutes",
            "itemType": "item_type",
            "phase": "phase",
            "topicId": "topic_id",
            "prepTopicId": "prep_topic_id",
            "status": "status",
            "completedAt": "completed_at",
        }
        return map_fields(data, field_map, entity="_map_plan_item")

    # -----------------------------------------------------------------------
    # Reflections
    # -----------------------------------------------------------------------

    async def upsert_reflection(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Reflection:
        """Write one period's reflection, idempotent on ``(userId, type, periodStart)``.

        Replaces `create_reflection`. Nothing stopped two rows existing for the same week,
        and the library page counts rows — so the Sunday task plus one manual generate made
        the count a count of generation attempts rather than of weeks reflected on.

        Select-then-update rather than `ON CONFLICT`, matching `upsert_readiness_snapshot`.
        The unique constraint is still what makes it correct under a race; this is what makes
        the common path readable.

        `openedAt` is deliberately not overwritten on re-generation: a learner who read this
        week's reflection before it was regenerated has still read it.
        """
        mapped = self._map_reflection(data)
        async with self._use_session(session) as s:
            existing = (
                await s.execute(
                    select(Reflection).where(
                        Reflection.user_id == mapped["user_id"],
                        Reflection.type == mapped["type"],
                        Reflection.period_start == mapped["period_start"],
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                for field, value in mapped.items():
                    if field == "opened_at":
                        continue
                    setattr(existing, field, value)
                await s.flush()
                await s.refresh(existing)
                return existing

            reflection = Reflection(**mapped)
            s.add(reflection)
            await s.flush()
            await s.refresh(reflection)
            return reflection

    async def list_reflections(
        self,
        user_id: str,
        *,
        type_filter: str | None = None,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
        sort: str = "newest",
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[Reflection], int]:
        """A page of the learner's reflections, newest first by default.

        `period_from` / `period_to` bound on `periodEnd`, which is the date the library
        groups and labels by. Bounding on `periodStart` would put a month that began in June
        outside a July filter, which is not what "July reflections" means to a reader.
        """
        async with self._read_session(session) as s:
            conditions = [Reflection.user_id == user_id]

            if type_filter is not None:
                conditions.append(Reflection.type == type_filter)
            if period_from is not None:
                conditions.append(Reflection.period_end >= period_from)
            if period_to is not None:
                conditions.append(Reflection.period_end <= period_to)

            count_stmt = select(func.count()).select_from(Reflection).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            order = (
                Reflection.period_end.asc() if sort == "oldest" else Reflection.period_end.desc()
            )

            stmt = select(Reflection).where(*conditions).order_by(order).offset(skip).limit(take)
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def get_reflection(
        self, reflection_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Reflection | None:
        async with self._read_session(session) as s:
            stmt = select(Reflection).where(
                Reflection.id == reflection_id,
                Reflection.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_opened_reflection_periods(
        self,
        user_id: str,
        *,
        type_filter: str | None = None,
        limit: int = 60,
        session: AsyncSession | None = None,
    ) -> list[datetime]:
        """`periodEnd` of every reflection the learner has actually opened, newest first.

        Only opened ones, because the reflection streak counts engagement rather than
        existence — the rows themselves are written by a Sunday task, so counting them would
        measure the scheduler.

        Bounded: a streak is a run at the end of the series, so the whole history is never
        needed to find it.
        """
        async with self._read_session(session) as s:
            conditions = [Reflection.user_id == user_id, Reflection.opened_at.is_not(None)]
            if type_filter is not None:
                conditions.append(Reflection.type == type_filter)

            stmt = (
                select(Reflection.period_end)
                .where(*conditions)
                .order_by(Reflection.period_end.desc())
                .limit(limit)
            )
            return [row[0] for row in (await s.execute(stmt)).all()]

    async def update_reflection(
        self,
        reflection_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> Reflection | None:
        """Apply a partial update, or return None if the row is not the caller's."""
        async with self._use_session(session) as s:
            reflection = (
                await s.execute(
                    select(Reflection).where(
                        Reflection.id == reflection_id,
                        Reflection.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if reflection is None:
                return None

            for field, value in self._map_reflection(data).items():
                setattr(reflection, field, value)
            await s.flush()
            await s.refresh(reflection)
            return reflection

    async def delete_reflection(
        self, reflection_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete the learner's reflection. False when it was not theirs or not there."""
        async with self._use_session(session) as s:
            result = await s.execute(
                delete(Reflection).where(
                    Reflection.id == reflection_id,
                    Reflection.user_id == user_id,
                )
            )
            return (result.rowcount or 0) > 0

    async def mark_reflection_opened(
        self,
        reflection_id: str,
        user_id: str,
        *,
        opened_at: datetime,
        session: AsyncSession | None = None,
    ) -> Reflection | None:
        """Record the first time the learner opened this reflection.

        First only — a later re-read leaves `openedAt` alone, because the field answers "did
        they engage with this period", not "when did they last look". Overwriting it would
        make the reflection streak a measure of recent browsing.
        """
        async with self._use_session(session) as s:
            reflection = (
                await s.execute(
                    select(Reflection).where(
                        Reflection.id == reflection_id,
                        Reflection.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if reflection is None:
                return None

            if reflection.opened_at is None:
                reflection.opened_at = opened_at
                await s.flush()
                await s.refresh(reflection)
            return reflection

    # -----------------------------------------------------------------------
    # Field mapping helpers — Reflections
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_reflection(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "type": "type",
            "periodStart": "period_start",
            "periodEnd": "period_end",
            "title": "title",
            "summary": "summary",
            "depth": "depth",
            "metrics": "metrics",
            # Present so the column is writable at all. Without it the allowlist would reject
            # `narrative` and name the mapper, which is the guard working correctly — but a
            # column no writer can reach is a column that will be quietly worked around.
            "narrative": "narrative",
            "recommendations": "recommendations",
            "openedAt": "opened_at",
        }
        return map_fields(data, field_map, entity="_map_reflection")

    # -----------------------------------------------------------------------
    # Reflection notes — learner-authored, distinct from generated reflections
    # -----------------------------------------------------------------------

    async def create_reflection_note(
        self,
        *,
        user_id: str,
        body: str,
        prompt_used: str | None = None,
        session: AsyncSession | None = None,
    ) -> ReflectionNote:
        """Store a note the learner wrote."""
        async with self._use_session(session) as s:
            note = ReflectionNote(user_id=user_id, body=body, prompt_used=prompt_used)
            s.add(note)
            await s.flush()
            await s.refresh(note)
            return note

    async def list_reflection_notes(
        self,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[ReflectionNote], int]:
        """A page of the learner's notes, newest first, with the total.

        Newest first because the box that writes them sits at the top of the page and the
        learner's last thought is the one they are still holding.
        """
        async with self._read_session(session) as s:
            conditions = [ReflectionNote.user_id == user_id]
            total = (
                await s.execute(select(func.count()).select_from(ReflectionNote).where(*conditions))
            ).scalar() or 0
            stmt = (
                select(ReflectionNote)
                .where(*conditions)
                .order_by(ReflectionNote.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            items = list((await s.execute(stmt)).scalars().all())
            return items, total

    async def find_reflection_note(
        self, note_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> ReflectionNote | None:
        """One note, scoped to its owner. Ownership is in the query, not checked after."""
        async with self._read_session(session) as s:
            stmt = select(ReflectionNote).where(
                ReflectionNote.id == note_id,
                ReflectionNote.user_id == user_id,
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def update_reflection_note(
        self, note_id: str, user_id: str, *, body: str, session: AsyncSession | None = None
    ) -> ReflectionNote | None:
        """Edit a note's text. `None` when it was not theirs or not there.

        Only `body` moves. `promptUsed` records what the learner was answering at the time, so
        an edit must not rewrite it.
        """
        async with self._use_session(session) as s:
            note = (
                await s.execute(
                    select(ReflectionNote).where(
                        ReflectionNote.id == note_id,
                        ReflectionNote.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if note is None:
                return None
            note.body = body
            await s.flush()
            await s.refresh(note)
            return note

    async def delete_reflection_note(
        self, note_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        """Delete a note. False when it was not theirs or not there."""
        async with self._use_session(session) as s:
            result = await s.execute(
                delete(ReflectionNote).where(
                    ReflectionNote.id == note_id,
                    ReflectionNote.user_id == user_id,
                )
            )
            return (result.rowcount or 0) > 0

    # -----------------------------------------------------------------------
    # Discovery Recommendations
    # -----------------------------------------------------------------------

    async def create_recommendation(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> DiscoveryRecommendation:
        async with self._use_session(session) as s:
            recommendation = DiscoveryRecommendation(**self._map_recommendation(data))
            s.add(recommendation)
            await s.flush()
            await s.refresh(recommendation)
            return recommendation

    async def list_active_recommendations(
        self, user_id: str, *, limit: int = 5, session: AsyncSession | None = None
    ) -> list[DiscoveryRecommendation]:
        async with self._read_session(session) as s:
            stmt = (
                select(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.user_id == user_id,
                    DiscoveryRecommendation.status == "ACTIVE",
                )
                .order_by(DiscoveryRecommendation.relevance_score.desc())
                .limit(limit)
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def mark_followed(
        self, rec_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.id == rec_id,
                    DiscoveryRecommendation.user_id == user_id,
                )
                .values(status="FOLLOWED", followed_at=datetime.now(UTC))
            )
            await s.execute(stmt)

    async def dismiss_recommendation(
        self, rec_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.id == rec_id,
                    DiscoveryRecommendation.user_id == user_id,
                )
                .values(status="DISMISSED", dismissed_at=datetime.now(UTC))
            )
            await s.execute(stmt)

    async def delete_old_recommendations(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            cutoff = datetime.now(UTC) - timedelta(days=7)
            stmt = delete(DiscoveryRecommendation).where(
                DiscoveryRecommendation.user_id == user_id,
                DiscoveryRecommendation.status == "ACTIVE",
                DiscoveryRecommendation.created_at < cutoff,
            )
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Field mapping helpers — Discovery Recommendations
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_recommendation(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "itemType": "item_type",
            "itemId": "item_id",
            "title": "title",
            "reason": "reason",
            "relevanceScore": "relevance_score",
            "status": "status",
        }
        return map_fields(data, field_map, entity="_map_recommendation")

    # -----------------------------------------------------------------------
    # Activity Feed
    # -----------------------------------------------------------------------

    async def create_feed_entry(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> ActivityFeedEntry:
        async with self._use_session(session) as s:
            entry = ActivityFeedEntry(**self._map_feed_entry(data))
            s.add(entry)
            await s.flush()
            await s.refresh(entry)
            return entry

    async def list_feed_entries(
        self,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[ActivityFeedEntry], int]:
        async with self._read_session(session) as s:
            conditions = [ActivityFeedEntry.user_id == user_id]

            # Count
            count_stmt = select(func.count()).select_from(ActivityFeedEntry).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(ActivityFeedEntry)
                .where(*conditions)
                .order_by(ActivityFeedEntry.occurred_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    # -----------------------------------------------------------------------
    # Field mapping helpers — Activity Feed
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_feed_entry(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "activityType": "activity_type",
            "title": "title",
            "description": "description",
            "context": "context",
            "occurredAt": "occurred_at",
        }
        return map_fields(data, field_map, entity="_map_feed_entry")

    # -----------------------------------------------------------------------
    # Background task helpers
    # -----------------------------------------------------------------------

    async def list_active_profiles(
        self, *, skip: int = 0, take: int = 100, session: AsyncSession | None = None
    ) -> list[LearningProfile]:
        """Return LearningProfiles in paginated batches (for background tasks)."""
        async with self._read_session(session) as s:
            stmt = (
                select(LearningProfile).order_by(LearningProfile.user_id).offset(skip).limit(take)
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def count_active_profiles(self, *, session: AsyncSession | None = None) -> int:
        """Return total count of active learning profiles."""
        async with self._read_session(session) as s:
            stmt = select(func.count()).select_from(LearningProfile)
            return (await s.execute(stmt)).scalar() or 0

    async def list_declining_engagement_profiles(
        self,
        min_declining_days: int = 3,
        *,
        skip: int = 0,
        take: int = 100,
        session: AsyncSession | None = None,
    ) -> list[LearningProfile]:
        """Return profiles with dropout_risk above threshold (paginated).

        A more sophisticated implementation would track daily activity counts,
        but for now we use the cached dropout_risk score computed by the
        behaviour analysis task (> 0.5 indicates declining engagement).
        """
        async with self._read_session(session) as s:
            stmt = (
                select(LearningProfile)
                .where(
                    LearningProfile.dropout_risk.isnot(None),
                    LearningProfile.dropout_risk > 0.5,
                )
                .order_by(LearningProfile.user_id)
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def increment_maturity_days(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        """Increment maturity_days counter for a learner's profile."""
        async with self._use_session(session) as s:
            stmt = (
                update(LearningProfile)
                .where(LearningProfile.user_id == user_id)
                .values(maturity_days=LearningProfile.maturity_days + 1)
            )
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Collections
    # -----------------------------------------------------------------------

    @staticmethod
    def _map_collection(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "description": "description",
            "sourceTag": "source_tag",
            "deletedAt": "deleted_at",
        }
        return map_fields(data, field_map, entity="_map_collection")

    @staticmethod
    def _map_collection_item(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "collectionId": "collection_id",
            "entityType": "entity_type",
            "entityId": "entity_id",
            "position": "position",
        }
        return map_fields(data, field_map, entity="_map_collection_item")

    async def create_collection(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Collection:
        async with self._use_session(session) as s:
            collection = Collection(**self._map_collection(data))
            s.add(collection)
            await s.flush()
            await s.refresh(collection)
            return collection

    async def find_collection(
        self, collection_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Collection | None:
        """Find a non-deleted collection owned by user."""
        async with self._read_session(session) as s:
            stmt = select(Collection).where(
                Collection.id == collection_id,
                Collection.user_id == user_id,
                Collection.deleted_at.is_(None),
            )
            return (await s.execute(stmt)).scalar_one_or_none()

    async def update_collection(
        self, collection_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Collection | None:
        async with self._use_session(session) as s:
            mapped = self._map_collection(data)
            if mapped:
                stmt = update(Collection).where(Collection.id == collection_id).values(**mapped)
                await s.execute(stmt)

        async with self._use_session(None) as s:
            stmt = select(Collection).where(Collection.id == collection_id)
            return (await s.execute(stmt)).scalar_one_or_none()

    async def soft_delete_collection(
        self, collection_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        async with self._use_session(session) as s:
            stmt = (
                update(Collection)
                .where(
                    Collection.id == collection_id,
                    Collection.user_id == user_id,
                    Collection.deleted_at.is_(None),
                )
                .values(deleted_at=datetime.now(UTC))
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    async def list_collections(
        self,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[Collection], int]:
        """List non-deleted collections ordered by updatedAt desc, with item counts."""
        async with self._read_session(session) as s:
            conditions = [
                Collection.user_id == user_id,
                Collection.deleted_at.is_(None),
            ]
            total = (
                await s.execute(select(func.count()).select_from(Collection).where(*conditions))
            ).scalar_one() or 0

            stmt = (
                select(Collection)
                .where(*conditions)
                .order_by(Collection.updated_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def create_collection_item(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> CollectionItem:
        async with self._use_session(session) as s:
            item = CollectionItem(**self._map_collection_item(data))
            s.add(item)
            await s.flush()
            await s.refresh(item)
            return item

    async def delete_collection_item(
        self, item_id: str, collection_id: str, *, session: AsyncSession | None = None
    ) -> bool:
        async with self._use_session(session) as s:
            stmt = delete(CollectionItem).where(
                CollectionItem.id == item_id,
                CollectionItem.collection_id == collection_id,
            )
            result = await s.execute(stmt)
            return result.rowcount > 0

    async def reorder_collection_items(
        self, collection_id: str, item_ids: list[str], *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            for position, item_id in enumerate(item_ids):
                await s.execute(
                    update(CollectionItem)
                    .where(
                        CollectionItem.id == item_id,
                        CollectionItem.collection_id == collection_id,
                    )
                    .values(position=position)
                )

    async def find_cross_type_tags(
        self, user_id: str, *, limit: int = 8, session: AsyncSession | None = None
    ) -> list[tuple[str, int]]:
        """Tags appearing in >=2 entity types, excluding tags already used by a Collection.

        `SavedResource.tags` is a **`json` column**, not a Postgres array. This query used
        `unnest(sr.tags)`, which only accepts arrays, so it raised
        `UndefinedFunctionError: function unnest(json) does not exist` on every call — and since
        `auto_seed_collections` wraps the whole thing in a logging `except`, collection
        auto-seeding never once worked while appearing to run. It is reached from the learning
        dashboard, so the failure was logged on every dashboard load.

        The array operator is a leftover from Prisma, where `tags String[]` mapped to a real
        `text[]`; the SQLAlchemy migration declared the column `JSON` and this raw SQL was never
        revisited. Raw SQL is where that kind of drift hides, because no ORM layer complains and
        the SQLite used in tests has neither function.

        The `CASE` guard is not defensive padding: `json_array_elements_text` raises on any value
        that is not a JSON array, and the column is typed `dict | None` on the ORM, so a
        non-array is representable. Guarding inside the function argument rather than in a
        `WHERE` matters — a `WHERE` clause is not guaranteed to be evaluated before a
        set-returning function in the same query level.
        """
        from sqlalchemy import text as sql_text

        async with self._read_session(session) as s:
            query = sql_text(
                """
                WITH tag_sources AS (
                    SELECT nt.tag AS tag, 'note' AS entity_type
                    FROM "NoteTag" nt
                    JOIN "Note" n ON n.id = nt."noteId"
                    WHERE n."userId" = :user_id AND n."spaceId" IS NULL

                    UNION ALL

                    SELECT elem.tag AS tag, 'saved_resource' AS entity_type
                    FROM "SavedResource" sr
                    CROSS JOIN LATERAL json_array_elements_text(
                        CASE WHEN json_typeof(sr.tags) = 'array'
                             THEN sr.tags
                             ELSE '[]'::json
                        END
                    ) AS elem(tag)
                    WHERE sr."userId" = :user_id AND sr.tags IS NOT NULL
                ),
                cross_tags AS (
                    SELECT tag, COUNT(DISTINCT entity_type) AS type_count
                    FROM tag_sources
                    GROUP BY tag
                    HAVING COUNT(DISTINCT entity_type) >= 2
                )
                SELECT ct.tag, ct.type_count
                FROM cross_tags ct
                WHERE ct.tag NOT IN (
                    SELECT c."sourceTag" FROM "Collection" c
                    WHERE c."userId" = :user_id AND c."sourceTag" IS NOT NULL
                )
                ORDER BY ct.type_count DESC, ct.tag ASC
                LIMIT :limit
            """
            )
            result = await s.execute(query, {"user_id": user_id, "limit": limit})
            return [(row[0], row[1]) for row in result.all()]

    async def bulk_create_collection_items(
        self,
        collection_id: str,
        items: list[dict[str, Any]],
        *,
        session: AsyncSession | None = None,
    ) -> None:
        async with self._use_session(session) as s:
            for item_data in items:
                mapped = self._map_collection_item(item_data)
                mapped["collection_id"] = collection_id
                ci = CollectionItem(**mapped)
                s.add(ci)
            await s.flush()

    async def list_collection_items_with_titles(
        self, collection_id: str, *, session: AsyncSession | None = None
    ) -> list[dict[str, Any]]:
        """LEFT JOIN against artifact tables to resolve titles; exclude deleted artifacts."""
        async with self._read_session(session) as s:
            note_alias = aliased(Note)
            deck_alias = aliased(FlashcardDeck)
            resource_alias = aliased(SavedResource)
            document_alias = aliased(GeneratedDocument)

            stmt = (
                select(
                    CollectionItem.id,
                    CollectionItem.entity_type,
                    CollectionItem.entity_id,
                    CollectionItem.position,
                    CollectionItem.added_at,
                    note_alias.title.label("note_title"),
                    deck_alias.title.label("deck_title"),
                    resource_alias.title.label("resource_title"),
                    document_alias.title.label("document_title"),
                )
                .outerjoin(
                    note_alias,
                    (CollectionItem.entity_id == note_alias.id)
                    & (CollectionItem.entity_type == "note"),
                )
                .outerjoin(
                    deck_alias,
                    (CollectionItem.entity_id == deck_alias.id)
                    & (CollectionItem.entity_type == "deck"),
                )
                .outerjoin(
                    resource_alias,
                    (CollectionItem.entity_id == resource_alias.id)
                    & (CollectionItem.entity_type == "saved_resource"),
                )
                .outerjoin(
                    document_alias,
                    (CollectionItem.entity_id == document_alias.id)
                    & (CollectionItem.entity_type == "document"),
                )
                .where(CollectionItem.collection_id == collection_id)
                .order_by(CollectionItem.position.asc().nullslast(), CollectionItem.added_at.asc())
            )
            rows = (await s.execute(stmt)).all()
            results: list[dict[str, Any]] = []
            for row in rows:
                title = row.note_title or row.deck_title or row.resource_title or row.document_title
                if title is None:
                    # Artifact was deleted — skip
                    continue
                results.append(
                    {
                        "id": row.id,
                        "entity_type": row.entity_type,
                        "entity_id": row.entity_id,
                        "title": title,
                        "position": row.position,
                        "added_at": row.added_at,
                    }
                )
            return results

    async def list_dashboard_collections(
        self, user_id: str, *, take: int = 6, session: AsyncSession | None = None
    ) -> list[dict[str, Any]]:
        """Returns id, title, item_count, entity_types for the dashboard.

        Two queries regardless of how many collections come back. This was ``3 + 2N``: the
        ``Collection`` select, its ``lazy="selectin"`` items load, and then a count query *and* a
        distinct-types query **per collection** — fourteen round trips for six collections, to print
        six numbers and six short lists.

        That was survivable only because collection auto-seeding had never worked, so `N` was
        almost always zero. Fixing the seeding query made this the dashboard's worst N+1 overnight,
        which is the argument for grouping it now rather than later: a latent N+1 behind a broken
        feature becomes a live one the moment the feature starts working.

        Grouped by ``(collectionId, entityType)`` and folded in Python rather than aggregated with
        ``array_agg``, which is Postgres-only — the test suite runs SQLite, and a query that cannot
        execute there is a query no test can cover. Counting per type and summing loses nothing: the
        per-type counts add up to the total by construction.

        ``noload`` on the items relationship is load-bearing, not tidiness. It is ``lazy="selectin"``,
        so selecting the collections would otherwise fetch **every item of every collection** —
        entity ids and types for the whole set — purely to be discarded in favour of the counts
        below.
        """
        async with self._read_session(session) as s:
            stmt = (
                select(Collection)
                .options(noload(Collection.items))
                .where(
                    Collection.user_id == user_id,
                    Collection.deleted_at.is_(None),
                )
                .order_by(Collection.updated_at.desc())
                .limit(take)
            )
            collections = list((await s.execute(stmt)).scalars().all())
            if not collections:
                return []

            counts_by_collection: dict[str, int] = {}
            types_by_collection: dict[str, list[str]] = {}
            grouped = await s.execute(
                select(
                    CollectionItem.collection_id,
                    CollectionItem.entity_type,
                    func.count(CollectionItem.id),
                )
                .where(CollectionItem.collection_id.in_([c.id for c in collections]))
                .group_by(CollectionItem.collection_id, CollectionItem.entity_type)
            )
            for collection_id, entity_type, count in grouped.all():
                counts_by_collection[collection_id] = (
                    counts_by_collection.get(collection_id, 0) + (count or 0)
                )
                if entity_type is not None:
                    types_by_collection.setdefault(collection_id, []).append(entity_type)

            return [
                {
                    "id": collection.id,
                    "title": collection.title,
                    # Absent from the grouping means an empty collection, which is a real state — a
                    # freshly seeded one before its items are written — so it reports 0 rather than
                    # being dropped from the dashboard.
                    "item_count": counts_by_collection.get(collection.id, 0),
                    "entity_types": sorted(types_by_collection.get(collection.id, [])),
                }
                for collection in collections
            ]


# Singleton
personal_learning_repo = PersonalLearningRepository()
