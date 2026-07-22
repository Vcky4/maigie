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
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from .db_models import (
    ActivityFeedEntry,
    DiscoveryRecommendation,
    ExamPrep,
    Flashcard,
    FlashcardDeck,
    GeneratedDocument,
    LearningProfile,
    Note,
    NoteAttachment,
    NoteHistory,
    NoteTag,
    Notification,
    PrepMaterial,
    PrepTopic,
    QuizAnswer,
    QuizQuestion,
    QuizSession,
    Reflection,
    SavedResource,
    StudyPlan,
    StudyPlanItem,
)

logger = logging.getLogger(__name__)


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

    # -----------------------------------------------------------------------
    # Notes
    # -----------------------------------------------------------------------

    async def find_note(
        self, note_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Note | None:
        async with self._use_session(session) as s:
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

    async def count_user_notes(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> int:
        """Count total notes for a user (excluding archived)."""
        async with self._use_session(session) as s:
            stmt = (
                select(func.count())
                .select_from(Note)
                .where(Note.user_id == user_id)
                .where(Note.archived.is_(False))
            )
            result = await s.execute(stmt)
            return result.scalar_one() or 0

    async def list_notes(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[Note], int]:
        async with self._use_session(session) as s:
            conditions = [Note.user_id == user_id]
            conditions.extend(self._build_note_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Note).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(Note)
                .options(
                    selectinload(Note.tags),
                    selectinload(Note.attachments),
                )
                .where(*conditions)
                .order_by(Note.updated_at.desc())
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

    async def delete_note(
        self, note_id: str, *, session: AsyncSession | None = None
    ) -> None:
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
        async with self._use_session(session) as s:
            stmt = select(NoteAttachment).where(
                NoteAttachment.id == attachment_id,
                NoteAttachment.note_id == note_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Note Tags
    # -----------------------------------------------------------------------

    async def delete_note_tags(
        self, note_id: str, *, session: AsyncSession | None = None
    ) -> None:
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
        async with self._use_session(session) as s:
            stmt = select(ExamPrep).where(
                ExamPrep.id == prep_id,
                ExamPrep.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_exam_preps(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> list[ExamPrep]:
        async with self._use_session(session) as s:
            stmt = (
                select(ExamPrep)
                .where(ExamPrep.user_id == user_id)
                .order_by(ExamPrep.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

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

    async def delete_exam_prep(
        self, prep_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = delete(ExamPrep).where(ExamPrep.id == prep_id)
            await s.execute(stmt)

    # -----------------------------------------------------------------------
    # Generated Documents
    # -----------------------------------------------------------------------

    async def find_document(
        self, doc_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> GeneratedDocument | None:
        async with self._use_session(session) as s:
            stmt = select(GeneratedDocument).where(
                GeneratedDocument.id == doc_id,
                GeneratedDocument.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def find_document_by_share_id(
        self, share_id: str, *, session: AsyncSession | None = None
    ) -> GeneratedDocument | None:
        async with self._use_session(session) as s:
            stmt = select(GeneratedDocument).where(GeneratedDocument.share_id == share_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_documents(
        self, user_id: str, *, skip: int = 0, take: int = 20, session: AsyncSession | None = None
    ) -> tuple[list[GeneratedDocument], int]:
        async with self._use_session(session) as s:
            count_stmt = (
                select(func.count())
                .select_from(GeneratedDocument)
                .where(GeneratedDocument.user_id == user_id)
            )
            total = (await s.execute(count_stmt)).scalar() or 0

            stmt = (
                select(GeneratedDocument)
                .where(GeneratedDocument.user_id == user_id)
                .order_by(GeneratedDocument.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def count_documents_since(
        self, user_id: str, since: datetime, *, session: AsyncSession | None = None
    ) -> int:
        """Count documents generated by user since a given datetime."""
        async with self._use_session(session) as s:
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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_attachment(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "noteId": "note_id",
            "filename": "filename",
            "url": "url",
            "size": "size",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_exam_prep(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "subject": "subject",
            "examDate": "exam_date",
            "description": "description",
            "status": "status",
            "spaceId": "space_id",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_document(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "format": "format",
            "style": "style",
            "filename": "filename",
            "fileUrl": "file_url",
            "previewUrl": "preview_url",
            "size": "size",
            "contentType": "content_type",
            "isPublic": "is_public",
            "shareId": "share_id",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
                conditions.append(Note.title.ilike(f"%{contains}%"))
        # OR search: match title OR content (case-insensitive)
        if "OR" in where:
            from sqlalchemy import or_

            or_conditions = []
            for clause in where["OR"]:
                if "title" in clause and isinstance(clause["title"], dict):
                    text = clause["title"].get("contains", "")
                    if text:
                        or_conditions.append(Note.title.ilike(f"%{text}%"))
                if "content" in clause and isinstance(clause["content"], dict):
                    text = clause["content"].get("contains", "")
                    if text:
                        or_conditions.append(Note.content.ilike(f"%{text}%"))
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
        async with self._use_session(session) as s:
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
        self, user_id: str, *, limit: int | None = None, session: AsyncSession | None = None
    ) -> list[Flashcard]:
        async with self._use_session(session) as s:
            now = datetime.now(timezone.utc)
            stmt = (
                select(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.next_review_at <= now,
                )
                .order_by(Flashcard.next_review_at.asc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def get_flashcard_stats(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> dict[str, Any]:
        async with self._use_session(session) as s:
            now = datetime.now(timezone.utc)

            # Total count
            total_stmt = (
                select(func.count()).select_from(Flashcard).where(Flashcard.user_id == user_id)
            )
            total = (await s.execute(total_stmt)).scalar() or 0

            # Due today count
            due_stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.next_review_at <= now,
                )
            )
            due_today = (await s.execute(due_stmt)).scalar() or 0

            # Mastered count (interval > 21 days)
            mastered_stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.interval_days > 21,
                )
            )
            mastered_count = (await s.execute(mastered_stmt)).scalar() or 0

            # Average ease factor
            avg_ease_stmt = select(func.avg(Flashcard.ease_factor)).where(
                Flashcard.user_id == user_id
            )
            avg_ease_factor = (await s.execute(avg_ease_stmt)).scalar() or 2.5

            return {
                "total": total,
                "due_today": due_today,
                "mastered_count": mastered_count,
                "avg_ease_factor": round(float(avg_ease_factor), 2),
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
        async with self._use_session(session) as s:
            stmt = (
                select(FlashcardDeck)
                .where(FlashcardDeck.user_id == user_id)
                .order_by(FlashcardDeck.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_deck_flashcards(
        self, deck_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> list[Flashcard]:
        async with self._use_session(session) as s:
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
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_deck(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "userId": "user_id",
            "title": "title",
            "description": "description",
            "courseId": "course_id",
            "topicId": "topic_id",
            "prepId": "prep_id",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
            conditions = [SavedResource.user_id == user_id]

            if source_type is not None:
                conditions.append(SavedResource.source_type == source_type)
            if search:
                conditions.append(SavedResource.title.ilike(f"%{search}%"))

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
        self, resource_id: str, user_id: str, tags: list[str], *, session: AsyncSession | None = None
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
        self, resource_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(SavedResource)
                .where(SavedResource.id == resource_id)
                .values(last_accessed_at=datetime.now(timezone.utc))
            )
            await s.execute(stmt)

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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
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
        mapped = {behaviour_fields[k]: v for k, v in data.items() if k in behaviour_fields}
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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
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
                .values(status="READ", read_at=datetime.now(timezone.utc))
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
                .values(status="DISMISSED", dismissed_at=datetime.now(timezone.utc))
            )
            await s.execute(stmt)

    async def count_today_delivered(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> int:
        async with self._use_session(session) as s:
            now = datetime.now(timezone.utc)
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
        async with self._use_session(session) as s:
            now = datetime.now(timezone.utc)
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
        self, notification_id: str, status: str, delivered_at: datetime | None = None,
        *, session: AsyncSession | None = None
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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
            stmt = (
                select(PrepTopic)
                .where(PrepTopic.prep_id == prep_id)
                .order_by(PrepTopic.order_index.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def update_topic_mastery(
        self, topic_id: str, mastery_score: float, status: str,
        *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            stmt = (
                update(PrepTopic)
                .where(PrepTopic.id == topic_id)
                .values(mastery_score=mastery_score, status=status)
            )
            await s.execute(stmt)

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
        async with self._use_session(session) as s:
            stmt = (
                select(PrepMaterial)
                .where(PrepMaterial.prep_id == prep_id)
                .order_by(PrepMaterial.created_at.desc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

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
        async with self._use_session(session) as s:
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

    async def create_quiz_question(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> QuizQuestion:
        async with self._use_session(session) as s:
            question = QuizQuestion(**self._map_quiz_question(data))
            s.add(question)
            await s.flush()
            await s.refresh(question)
            return question

    async def create_quiz_answer(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> QuizAnswer:
        async with self._use_session(session) as s:
            answer = QuizAnswer(**self._map_quiz_answer(data))
            s.add(answer)
            await s.flush()
            await s.refresh(answer)
            return answer

    async def list_quiz_answers(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> list[QuizAnswer]:
        async with self._use_session(session) as s:
            stmt = select(QuizAnswer).where(QuizAnswer.quiz_session_id == quiz_id)
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_quiz_questions(
        self, quiz_id: str, *, session: AsyncSession | None = None
    ) -> list[QuizQuestion]:
        """Return all questions for a quiz session ordered by orderIndex."""
        async with self._use_session(session) as s:
            stmt = (
                select(QuizQuestion)
                .where(QuizQuestion.quiz_session_id == quiz_id)
                .order_by(QuizQuestion.order_index.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def list_prep_quizzes(
        self,
        prep_id: str,
        user_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> list[QuizSession]:
        """Return all quiz sessions for a preparation, newest first."""
        async with self._use_session(session) as s:
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
            "estimatedMinutes": "estimated_minutes",
            "orderIndex": "order_index",
            "masteryScore": "mastery_score",
            "status": "status",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
            "completedAt": "completed_at",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_quiz_question(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "quizSessionId": "quiz_session_id",
            "prepTopicId": "prep_topic_id",
            "questionText": "question_text",
            "questionType": "question_type",
            "options": "options",
            "correctAnswer": "correct_answer",
            "explanation": "explanation",
            "orderIndex": "order_index",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_quiz_answer(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "quizSessionId": "quiz_session_id",
            "questionId": "question_id",
            "userAnswer": "user_answer",
            "isCorrect": "is_correct",
            "timeTakenSeconds": "time_taken_seconds",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
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
        async with self._use_session(session) as s:
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
        self, item_id: str, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> StudyPlanItem | None:
        async with self._use_session(session) as s:
            mapped = self._map_plan_item(data)
            if mapped:
                stmt = update(StudyPlanItem).where(StudyPlanItem.id == item_id).values(**mapped)
                await s.execute(stmt)

        # Re-fetch to return updated object
        async with self._use_session(None) as s:
            stmt = select(StudyPlanItem).where(StudyPlanItem.id == item_id)
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

    async def list_plan_items(
        self, plan_id: str, *, session: AsyncSession | None = None
    ) -> list[StudyPlanItem]:
        async with self._use_session(session) as s:
            stmt = (
                select(StudyPlanItem)
                .where(StudyPlanItem.plan_id == plan_id)
                .order_by(StudyPlanItem.scheduled_date.asc())
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

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
            "totalItems": "total_items",
            "completedItems": "completed_items",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    @staticmethod
    def _map_plan_item(data: dict[str, Any]) -> dict[str, Any]:
        field_map = {
            "planId": "plan_id",
            "title": "title",
            "description": "description",
            "scheduledDate": "scheduled_date",
            "estimatedMinutes": "estimated_minutes",
            "itemType": "item_type",
            "topicId": "topic_id",
            "prepTopicId": "prep_topic_id",
            "status": "status",
            "completedAt": "completed_at",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    # -----------------------------------------------------------------------
    # Reflections
    # -----------------------------------------------------------------------

    async def create_reflection(
        self, data: dict[str, Any], *, session: AsyncSession | None = None
    ) -> Reflection:
        async with self._use_session(session) as s:
            reflection = Reflection(**self._map_reflection(data))
            s.add(reflection)
            await s.flush()
            await s.refresh(reflection)
            return reflection

    async def list_reflections(
        self,
        user_id: str,
        *,
        type_filter: str | None = None,
        skip: int = 0,
        take: int = 20,
        session: AsyncSession | None = None,
    ) -> tuple[list[Reflection], int]:
        async with self._use_session(session) as s:
            conditions = [Reflection.user_id == user_id]

            if type_filter is not None:
                conditions.append(Reflection.type == type_filter)

            # Count
            count_stmt = select(func.count()).select_from(Reflection).where(*conditions)
            total = (await s.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(Reflection)
                .where(*conditions)
                .order_by(Reflection.period_end.desc())
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def get_reflection(
        self, reflection_id: str, user_id: str, *, session: AsyncSession | None = None
    ) -> Reflection | None:
        async with self._use_session(session) as s:
            stmt = select(Reflection).where(
                Reflection.id == reflection_id,
                Reflection.user_id == user_id,
            )
            result = await s.execute(stmt)
            return result.scalar_one_or_none()

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
            "summary": "summary",
            "activitiesLayer": "activities_layer",
            "progressLayer": "progress_layer",
            "achievementsLayer": "achievements_layer",
            "recommendations": "recommendations",
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
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
                .values(status="FOLLOWED", followed_at=datetime.now(timezone.utc))
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
                .values(status="DISMISSED", dismissed_at=datetime.now(timezone.utc))
            )
            await s.execute(stmt)

    async def delete_old_recommendations(
        self, user_id: str, *, session: AsyncSession | None = None
    ) -> None:
        async with self._use_session(session) as s:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

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
        async with self._use_session(session) as s:
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
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    # -----------------------------------------------------------------------
    # Background task helpers
    # -----------------------------------------------------------------------

    async def list_active_profiles(
        self, *, skip: int = 0, take: int = 100, session: AsyncSession | None = None
    ) -> list[LearningProfile]:
        """Return LearningProfiles in paginated batches (for background tasks)."""
        async with self._use_session(session) as s:
            stmt = (
                select(LearningProfile)
                .order_by(LearningProfile.user_id)
                .offset(skip)
                .limit(take)
            )
            result = await s.execute(stmt)
            return list(result.scalars().all())

    async def count_active_profiles(
        self, *, session: AsyncSession | None = None
    ) -> int:
        """Return total count of active learning profiles."""
        async with self._use_session(session) as s:
            stmt = select(func.count()).select_from(LearningProfile)
            return (await s.execute(stmt)).scalar() or 0

    async def list_declining_engagement_profiles(
        self, min_declining_days: int = 3, *, skip: int = 0, take: int = 100,
        session: AsyncSession | None = None
    ) -> list[LearningProfile]:
        """Return profiles with dropout_risk above threshold (paginated).

        A more sophisticated implementation would track daily activity counts,
        but for now we use the cached dropout_risk score computed by the
        behaviour analysis task (> 0.5 indicates declining engagement).
        """
        async with self._use_session(session) as s:
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


# Singleton
personal_learning_repo = PersonalLearningRepository()
