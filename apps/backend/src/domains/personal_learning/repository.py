"""
Personal Learning domain — Data access layer (SQLAlchemy).

Encapsulates all queries for Notes, NoteTag, NoteAttachment,
ExamPrep, and GeneratedDocument.
"""

import logging
from typing import Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_session_factory

from .db_models import (
    ExamPrep,
    GeneratedDocument,
    Note,
    NoteAttachment,
    NoteHistory,
    NoteTag,
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


# Singleton
personal_learning_repo = PersonalLearningRepository()
