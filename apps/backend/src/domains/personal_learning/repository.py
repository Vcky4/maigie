"""
Personal Learning domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Notes, NoteTag, NoteAttachment,
ExamPrep, and GeneratedDocument.
"""

import logging
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
    """Data access for notes, exam prep, and documents."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Notes
    # -----------------------------------------------------------------------

    async def find_note(self, note_id: str, user_id: str) -> Note | None:
        async with await self._session() as session:
            stmt = (
                select(Note)
                .options(
                    selectinload(Note.tags),
                    selectinload(Note.attachments),
                )
                .where(Note.id == note_id, Note.user_id == user_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_notes(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
    ) -> tuple[list[Note], int]:
        async with await self._session() as session:
            conditions = [Note.user_id == user_id]
            conditions.extend(self._build_note_conditions(where))

            # Count
            count_stmt = select(func.count()).select_from(Note).where(*conditions)
            total = (await session.execute(count_stmt)).scalar() or 0

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
            result = await session.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def create_note(self, data: dict[str, Any]) -> Note:
        async with await self._session() as session:
            note = Note(**self._map_note(data))

            # Handle nested tags
            tags_data = data.get("tags")
            if tags_data and isinstance(tags_data, dict):
                create_list = tags_data.get("create", [])
                for tag_item in create_list:
                    note.tags.append(NoteTag(tag=tag_item["tag"]))

            session.add(note)
            await session.commit()
            await session.refresh(note)
            return note

    async def update_note(self, note_id: str, data: dict[str, Any]) -> Note | None:
        async with await self._session() as session:
            mapped = self._map_note(data)
            if mapped:
                stmt = update(Note).where(Note.id == note_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        return await self.find_note(note_id, data.get("userId", ""))

    async def delete_note(self, note_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(Note).where(Note.id == note_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Note Attachments
    # -----------------------------------------------------------------------

    async def create_attachment(self, data: dict[str, Any]) -> NoteAttachment:
        async with await self._session() as session:
            attachment = NoteAttachment(**self._map_attachment(data))
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)
            return attachment

    async def delete_attachment(self, attachment_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(NoteAttachment).where(NoteAttachment.id == attachment_id)
            await session.execute(stmt)
            await session.commit()

    async def find_attachment(self, attachment_id: str, note_id: str) -> NoteAttachment | None:
        async with await self._session() as session:
            stmt = select(NoteAttachment).where(
                NoteAttachment.id == attachment_id,
                NoteAttachment.note_id == note_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Note Tags
    # -----------------------------------------------------------------------

    async def delete_note_tags(self, note_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(NoteTag).where(NoteTag.note_id == note_id)
            await session.execute(stmt)
            await session.commit()

    async def create_note_tags(self, note_id: str, tags: list[str]) -> None:
        async with await self._session() as session:
            for tag in tags:
                session.add(NoteTag(note_id=note_id, tag=tag))
            await session.commit()

    # -----------------------------------------------------------------------
    # Exam Prep
    # -----------------------------------------------------------------------

    async def find_exam_prep(self, prep_id: str, user_id: str) -> ExamPrep | None:
        async with await self._session() as session:
            stmt = select(ExamPrep).where(
                ExamPrep.id == prep_id,
                ExamPrep.user_id == user_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_exam_preps(self, user_id: str) -> list[ExamPrep]:
        async with await self._session() as session:
            stmt = (
                select(ExamPrep)
                .where(ExamPrep.user_id == user_id)
                .order_by(ExamPrep.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_exam_prep(self, data: dict[str, Any]) -> ExamPrep:
        async with await self._session() as session:
            prep = ExamPrep(**self._map_exam_prep(data))
            session.add(prep)
            await session.commit()
            await session.refresh(prep)
            return prep

    async def update_exam_prep(self, prep_id: str, data: dict[str, Any]) -> ExamPrep | None:
        async with await self._session() as session:
            mapped = self._map_exam_prep(data)
            if mapped:
                stmt = update(ExamPrep).where(ExamPrep.id == prep_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        # Re-fetch to return updated object
        # Use a broad user_id match since we don't always have it here
        async with await self._session() as session:
            stmt = select(ExamPrep).where(ExamPrep.id == prep_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_exam_prep(self, prep_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(ExamPrep).where(ExamPrep.id == prep_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Generated Documents
    # -----------------------------------------------------------------------

    async def find_document(self, doc_id: str, user_id: str) -> GeneratedDocument | None:
        async with await self._session() as session:
            stmt = select(GeneratedDocument).where(
                GeneratedDocument.id == doc_id,
                GeneratedDocument.user_id == user_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_document_by_share_id(self, share_id: str) -> GeneratedDocument | None:
        async with await self._session() as session:
            stmt = select(GeneratedDocument).where(GeneratedDocument.share_id == share_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_documents(
        self, user_id: str, *, skip: int = 0, take: int = 20
    ) -> tuple[list[GeneratedDocument], int]:
        async with await self._session() as session:
            count_stmt = (
                select(func.count())
                .select_from(GeneratedDocument)
                .where(GeneratedDocument.user_id == user_id)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = (
                select(GeneratedDocument)
                .where(GeneratedDocument.user_id == user_id)
                .order_by(GeneratedDocument.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def create_document(self, data: dict[str, Any]) -> GeneratedDocument:
        async with await self._session() as session:
            doc = GeneratedDocument(**self._map_document(data))
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            return doc

    async def update_document(self, doc_id: str, data: dict[str, Any]) -> GeneratedDocument | None:
        async with await self._session() as session:
            mapped = self._map_document(data)
            if mapped:
                stmt = update(GeneratedDocument).where(GeneratedDocument.id == doc_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        async with await self._session() as session:
            stmt = select(GeneratedDocument).where(GeneratedDocument.id == doc_id)
            result = await session.execute(stmt)
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
            conditions.append(Note.space_id == where["spaceId"])
        if "title" in where and isinstance(where["title"], dict):
            contains = where["title"].get("contains", "")
            if contains:
                conditions.append(Note.title.ilike(f"%{contains}%"))
        return conditions


    # -----------------------------------------------------------------------
    # Flashcards
    # -----------------------------------------------------------------------

    async def create_flashcard(self, data: dict[str, Any]) -> Flashcard:
        async with await self._session() as session:
            flashcard = Flashcard(**self._map_flashcard(data))
            session.add(flashcard)
            await session.commit()
            await session.refresh(flashcard)
            return flashcard

    async def get_flashcard(self, card_id: str, user_id: str) -> Flashcard | None:
        async with await self._session() as session:
            stmt = select(Flashcard).where(
                Flashcard.id == card_id,
                Flashcard.user_id == user_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_flashcard(self, card_id: str, data: dict[str, Any]) -> Flashcard | None:
        async with await self._session() as session:
            mapped = self._map_flashcard(data)
            if mapped:
                stmt = update(Flashcard).where(Flashcard.id == card_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        # Re-fetch to return updated object
        async with await self._session() as session:
            stmt = select(Flashcard).where(Flashcard.id == card_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_due_flashcards(self, user_id: str) -> list[Flashcard]:
        async with await self._session() as session:
            now = datetime.now(timezone.utc)
            stmt = (
                select(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.next_review_at <= now,
                )
                .order_by(Flashcard.next_review_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_flashcard_stats(self, user_id: str) -> dict[str, Any]:
        async with await self._session() as session:
            now = datetime.now(timezone.utc)

            # Total count
            total_stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(Flashcard.user_id == user_id)
            )
            total = (await session.execute(total_stmt)).scalar() or 0

            # Due today count
            due_stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.next_review_at <= now,
                )
            )
            due_today = (await session.execute(due_stmt)).scalar() or 0

            # Mastered count (interval > 21 days)
            mastered_stmt = (
                select(func.count())
                .select_from(Flashcard)
                .where(
                    Flashcard.user_id == user_id,
                    Flashcard.interval_days > 21,
                )
            )
            mastered_count = (await session.execute(mastered_stmt)).scalar() or 0

            # Average ease factor
            avg_ease_stmt = (
                select(func.avg(Flashcard.ease_factor))
                .where(Flashcard.user_id == user_id)
            )
            avg_ease_factor = (await session.execute(avg_ease_stmt)).scalar() or 2.5

            return {
                "total": total,
                "due_today": due_today,
                "mastered_count": mastered_count,
                "avg_ease_factor": round(float(avg_ease_factor), 2),
            }

    # -----------------------------------------------------------------------
    # Flashcard Decks
    # -----------------------------------------------------------------------

    async def create_deck(self, data: dict[str, Any]) -> FlashcardDeck:
        async with await self._session() as session:
            deck = FlashcardDeck(**self._map_deck(data))
            session.add(deck)
            await session.commit()
            await session.refresh(deck)
            return deck

    async def list_decks(self, user_id: str) -> list[FlashcardDeck]:
        async with await self._session() as session:
            stmt = (
                select(FlashcardDeck)
                .where(FlashcardDeck.user_id == user_id)
                .order_by(FlashcardDeck.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_deck_flashcards(self, deck_id: str, user_id: str) -> list[Flashcard]:
        async with await self._session() as session:
            stmt = (
                select(Flashcard)
                .where(
                    Flashcard.deck_id == deck_id,
                    Flashcard.user_id == user_id,
                )
                .order_by(Flashcard.created_at.desc())
            )
            result = await session.execute(stmt)
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

    async def create_resource(self, data: dict[str, Any]) -> SavedResource:
        async with await self._session() as session:
            resource = SavedResource(**self._map_resource(data))
            session.add(resource)
            await session.commit()
            await session.refresh(resource)
            return resource

    async def list_resources(
        self,
        user_id: str,
        *,
        source_type: str | None = None,
        search: str | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> tuple[list[SavedResource], int]:
        async with await self._session() as session:
            conditions = [SavedResource.user_id == user_id]

            if source_type is not None:
                conditions.append(SavedResource.source_type == source_type)
            if search:
                conditions.append(SavedResource.title.ilike(f"%{search}%"))

            # Count
            count_stmt = (
                select(func.count()).select_from(SavedResource).where(*conditions)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(SavedResource)
                .where(*conditions)
                .order_by(SavedResource.created_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def delete_resource(self, resource_id: str, user_id: str) -> bool:
        async with await self._session() as session:
            stmt = delete(SavedResource).where(
                SavedResource.id == resource_id,
                SavedResource.user_id == user_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def update_resource_tags(
        self, resource_id: str, user_id: str, tags: list[str]
    ) -> SavedResource | None:
        async with await self._session() as session:
            stmt = (
                update(SavedResource)
                .where(
                    SavedResource.id == resource_id,
                    SavedResource.user_id == user_id,
                )
                .values(tags=tags)
            )
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount == 0:
                return None

        # Re-fetch updated resource
        async with await self._session() as session:
            stmt = select(SavedResource).where(SavedResource.id == resource_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_last_accessed(self, resource_id: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(SavedResource)
                .where(SavedResource.id == resource_id)
                .values(last_accessed_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

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

    async def create_profile(self, data: dict[str, Any]) -> LearningProfile:
        async with await self._session() as session:
            profile = LearningProfile(**self._map_profile(data))
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            return profile

    async def get_profile_by_user(self, user_id: str) -> LearningProfile | None:
        async with await self._session() as session:
            stmt = select(LearningProfile).where(LearningProfile.user_id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_profile(self, user_id: str, data: dict[str, Any]) -> LearningProfile | None:
        async with await self._session() as session:
            mapped = self._map_profile(data)
            if mapped:
                stmt = (
                    update(LearningProfile)
                    .where(LearningProfile.user_id == user_id)
                    .values(**mapped)
                )
                await session.execute(stmt)
                await session.commit()

        return await self.get_profile_by_user(user_id)

    async def update_profile_behaviour(self, user_id: str, data: dict[str, Any]) -> None:
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

        async with await self._session() as session:
            stmt = (
                update(LearningProfile)
                .where(LearningProfile.user_id == user_id)
                .values(**mapped)
            )
            await session.execute(stmt)
            await session.commit()

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
        }
        return {field_map[k]: v for k, v in data.items() if k in field_map}

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    async def create_notification(self, data: dict[str, Any]) -> Notification:
        async with await self._session() as session:
            notification = Notification(**self._map_notification(data))
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return notification

    async def list_unread(self, user_id: str) -> list[Notification]:
        async with await self._session() as session:
            stmt = (
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.status.notin_(["READ", "DISMISSED"]),
                )
                .order_by(Notification.priority.asc(), Notification.scheduled_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def mark_read(self, notification_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
                .values(status="READ", read_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def mark_dismissed(self, notification_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
                .values(status="DISMISSED", dismissed_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def count_today_delivered(self, user_id: str) -> int:
        async with await self._session() as session:
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
            result = (await session.execute(stmt)).scalar() or 0
            return result

    async def list_pending_for_delivery(self) -> list[Notification]:
        async with await self._session() as session:
            now = datetime.now(timezone.utc)
            stmt = (
                select(Notification)
                .where(
                    Notification.status == "PENDING",
                    Notification.scheduled_at <= now,
                )
                .order_by(Notification.scheduled_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_status(
        self, notification_id: str, status: str, delivered_at: datetime | None = None
    ) -> None:
        async with await self._session() as session:
            values: dict[str, Any] = {"status": status}
            if delivered_at is not None:
                values["delivered_at"] = delivered_at
            stmt = (
                update(Notification)
                .where(Notification.id == notification_id)
                .values(**values)
            )
            await session.execute(stmt)
            await session.commit()

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

    async def create_prep_topic(self, data: dict[str, Any]) -> PrepTopic:
        async with await self._session() as session:
            topic = PrepTopic(**self._map_prep_topic(data))
            session.add(topic)
            await session.commit()
            await session.refresh(topic)
            return topic

    async def list_prep_topics(self, prep_id: str) -> list[PrepTopic]:
        async with await self._session() as session:
            stmt = (
                select(PrepTopic)
                .where(PrepTopic.prep_id == prep_id)
                .order_by(PrepTopic.order_index.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_topic_mastery(self, topic_id: str, mastery_score: float, status: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(PrepTopic)
                .where(PrepTopic.id == topic_id)
                .values(mastery_score=mastery_score, status=status)
            )
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Prep Materials
    # -----------------------------------------------------------------------

    async def create_prep_material(self, data: dict[str, Any]) -> PrepMaterial:
        async with await self._session() as session:
            material = PrepMaterial(**self._map_prep_material(data))
            session.add(material)
            await session.commit()
            await session.refresh(material)
            return material

    async def list_prep_materials(self, prep_id: str) -> list[PrepMaterial]:
        async with await self._session() as session:
            stmt = (
                select(PrepMaterial)
                .where(PrepMaterial.prep_id == prep_id)
                .order_by(PrepMaterial.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Quiz Sessions, Questions & Answers
    # -----------------------------------------------------------------------

    async def create_quiz_session(self, data: dict[str, Any]) -> QuizSession:
        async with await self._session() as session:
            quiz = QuizSession(**self._map_quiz_session(data))
            session.add(quiz)
            await session.commit()
            await session.refresh(quiz)
            return quiz

    async def get_quiz_session(self, quiz_id: str, user_id: str) -> QuizSession | None:
        async with await self._session() as session:
            stmt = (
                select(QuizSession)
                .options(selectinload(QuizSession.answers))
                .where(
                    QuizSession.id == quiz_id,
                    QuizSession.user_id == user_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_quiz_session(self, quiz_id: str, data: dict[str, Any]) -> QuizSession | None:
        async with await self._session() as session:
            mapped = self._map_quiz_session(data)
            if mapped:
                stmt = update(QuizSession).where(QuizSession.id == quiz_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        # Re-fetch to return updated object
        async with await self._session() as session:
            stmt = select(QuizSession).where(QuizSession.id == quiz_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_quiz_question(self, data: dict[str, Any]) -> QuizQuestion:
        async with await self._session() as session:
            question = QuizQuestion(**self._map_quiz_question(data))
            session.add(question)
            await session.commit()
            await session.refresh(question)
            return question

    async def create_quiz_answer(self, data: dict[str, Any]) -> QuizAnswer:
        async with await self._session() as session:
            answer = QuizAnswer(**self._map_quiz_answer(data))
            session.add(answer)
            await session.commit()
            await session.refresh(answer)
            return answer

    async def list_quiz_answers(self, quiz_id: str) -> list[QuizAnswer]:
        async with await self._session() as session:
            stmt = (
                select(QuizAnswer)
                .where(QuizAnswer.quiz_session_id == quiz_id)
            )
            result = await session.execute(stmt)
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

    async def create_study_plan(self, data: dict[str, Any]) -> StudyPlan:
        async with await self._session() as session:
            plan = StudyPlan(**self._map_study_plan(data))
            session.add(plan)
            await session.commit()
            await session.refresh(plan)
            return plan

    async def get_study_plan(self, plan_id: str, user_id: str) -> StudyPlan | None:
        async with await self._session() as session:
            stmt = (
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(
                    StudyPlan.id == plan_id,
                    StudyPlan.user_id == user_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_active_plans(self, user_id: str) -> list[StudyPlan]:
        async with await self._session() as session:
            stmt = (
                select(StudyPlan)
                .options(selectinload(StudyPlan.items))
                .where(
                    StudyPlan.user_id == user_id,
                    StudyPlan.status == "ACTIVE",
                )
                .order_by(StudyPlan.deadline.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_plan_status(self, plan_id: str, status: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(StudyPlan)
                .where(StudyPlan.id == plan_id)
                .values(status=status)
            )
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Study Plan Items
    # -----------------------------------------------------------------------

    async def create_plan_item(self, data: dict[str, Any]) -> StudyPlanItem:
        async with await self._session() as session:
            item = StudyPlanItem(**self._map_plan_item(data))
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def update_plan_item(self, item_id: str, data: dict[str, Any]) -> StudyPlanItem | None:
        async with await self._session() as session:
            mapped = self._map_plan_item(data)
            if mapped:
                stmt = update(StudyPlanItem).where(StudyPlanItem.id == item_id).values(**mapped)
                await session.execute(stmt)
                await session.commit()

        # Re-fetch to return updated object
        async with await self._session() as session:
            stmt = select(StudyPlanItem).where(StudyPlanItem.id == item_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_plan_items(self, plan_id: str) -> list[StudyPlanItem]:
        async with await self._session() as session:
            stmt = (
                select(StudyPlanItem)
                .where(StudyPlanItem.plan_id == plan_id)
                .order_by(StudyPlanItem.scheduled_date.asc())
            )
            result = await session.execute(stmt)
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

    async def create_reflection(self, data: dict[str, Any]) -> Reflection:
        async with await self._session() as session:
            reflection = Reflection(**self._map_reflection(data))
            session.add(reflection)
            await session.commit()
            await session.refresh(reflection)
            return reflection

    async def list_reflections(
        self,
        user_id: str,
        *,
        type_filter: str | None = None,
        skip: int = 0,
        take: int = 20,
    ) -> tuple[list[Reflection], int]:
        async with await self._session() as session:
            conditions = [Reflection.user_id == user_id]

            if type_filter is not None:
                conditions.append(Reflection.type == type_filter)

            # Count
            count_stmt = (
                select(func.count()).select_from(Reflection).where(*conditions)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(Reflection)
                .where(*conditions)
                .order_by(Reflection.period_end.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
            items = list(result.scalars().all())
            return items, total

    async def get_reflection(self, reflection_id: str, user_id: str) -> Reflection | None:
        async with await self._session() as session:
            stmt = select(Reflection).where(
                Reflection.id == reflection_id,
                Reflection.user_id == user_id,
            )
            result = await session.execute(stmt)
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

    async def create_recommendation(self, data: dict[str, Any]) -> DiscoveryRecommendation:
        async with await self._session() as session:
            recommendation = DiscoveryRecommendation(**self._map_recommendation(data))
            session.add(recommendation)
            await session.commit()
            await session.refresh(recommendation)
            return recommendation

    async def list_active_recommendations(
        self, user_id: str, *, limit: int = 5
    ) -> list[DiscoveryRecommendation]:
        async with await self._session() as session:
            stmt = (
                select(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.user_id == user_id,
                    DiscoveryRecommendation.status == "ACTIVE",
                )
                .order_by(DiscoveryRecommendation.relevance_score.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def mark_followed(self, rec_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.id == rec_id,
                    DiscoveryRecommendation.user_id == user_id,
                )
                .values(status="FOLLOWED", followed_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def dismiss_recommendation(self, rec_id: str, user_id: str) -> None:
        async with await self._session() as session:
            stmt = (
                update(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.id == rec_id,
                    DiscoveryRecommendation.user_id == user_id,
                )
                .values(status="DISMISSED", dismissed_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def delete_old_recommendations(self, user_id: str) -> None:
        async with await self._session() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            stmt = (
                delete(DiscoveryRecommendation)
                .where(
                    DiscoveryRecommendation.user_id == user_id,
                    DiscoveryRecommendation.status == "ACTIVE",
                    DiscoveryRecommendation.created_at < cutoff,
                )
            )
            await session.execute(stmt)
            await session.commit()

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

    async def create_feed_entry(self, data: dict[str, Any]) -> ActivityFeedEntry:
        async with await self._session() as session:
            entry = ActivityFeedEntry(**self._map_feed_entry(data))
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    async def list_feed_entries(
        self,
        user_id: str,
        *,
        skip: int = 0,
        take: int = 20,
    ) -> tuple[list[ActivityFeedEntry], int]:
        async with await self._session() as session:
            conditions = [ActivityFeedEntry.user_id == user_id]

            # Count
            count_stmt = (
                select(func.count()).select_from(ActivityFeedEntry).where(*conditions)
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # Items
            stmt = (
                select(ActivityFeedEntry)
                .where(*conditions)
                .order_by(ActivityFeedEntry.occurred_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
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

    async def list_active_profiles(self) -> list[LearningProfile]:
        """Return all LearningProfiles (active learners)."""
        async with await self._session() as session:
            stmt = select(LearningProfile)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_declining_engagement_profiles(
        self, min_declining_days: int = 3
    ) -> list[LearningProfile]:
        """Return profiles with dropout_risk above threshold (proxy for declining engagement).

        A more sophisticated implementation would track daily activity counts,
        but for now we use the cached dropout_risk score computed by the
        behaviour analysis task (> 0.5 indicates declining engagement).
        """
        async with await self._session() as session:
            stmt = select(LearningProfile).where(
                LearningProfile.dropout_risk.isnot(None),
                LearningProfile.dropout_risk > 0.5,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def increment_maturity_days(self, user_id: str) -> None:
        """Increment maturity_days counter for a learner's profile."""
        async with await self._session() as session:
            stmt = (
                update(LearningProfile)
                .where(LearningProfile.user_id == user_id)
                .values(maturity_days=LearningProfile.maturity_days + 1)
            )
            await session.execute(stmt)
            await session.commit()


# Singleton
personal_learning_repo = PersonalLearningRepository()
