"""
Personal Learning domain — Data access layer.

Encapsulates Prisma queries for Notes, ExamPrep, and GeneratedDocuments.
"""

import logging
from typing import Any

from src.shared.database import db

logger = logging.getLogger(__name__)


class PersonalLearningRepository:
    """Data access for notes, exam prep, and documents."""

    # -----------------------------------------------------------------------
    # Notes
    # -----------------------------------------------------------------------

    async def find_note(self, note_id: str, user_id: str):
        return await db.note.find_first(
            where={"id": note_id, "userId": user_id},
            include={"tags": True, "attachments": True},
        )

    async def list_notes(
        self,
        user_id: str,
        *,
        where: dict[str, Any],
        skip: int = 0,
        take: int = 20,
    ) -> tuple[list, int]:
        where["userId"] = user_id
        total = await db.note.count(where=where)
        items = await db.note.find_many(
            where=where,
            skip=skip,
            take=take,
            order={"updatedAt": "desc"},
            include={"tags": True, "attachments": True},
        )
        return items, total

    async def create_note(self, data: dict[str, Any]):
        return await db.note.create(
            data=data,
            include={"tags": True, "attachments": True},
        )

    async def update_note(self, note_id: str, data: dict[str, Any]):
        return await db.note.update(
            where={"id": note_id},
            data=data,
            include={"tags": True, "attachments": True},
        )

    async def delete_note(self, note_id: str):
        return await db.note.delete(where={"id": note_id})

    # -----------------------------------------------------------------------
    # Note Attachments
    # -----------------------------------------------------------------------

    async def create_attachment(self, data: dict[str, Any]):
        return await db.noteattachment.create(data=data)

    async def delete_attachment(self, attachment_id: str):
        return await db.noteattachment.delete(where={"id": attachment_id})

    async def find_attachment(self, attachment_id: str, note_id: str):
        return await db.noteattachment.find_first(
            where={"id": attachment_id, "noteId": note_id}
        )

    # -----------------------------------------------------------------------
    # Note Tags
    # -----------------------------------------------------------------------

    async def delete_note_tags(self, note_id: str):
        await db.notetag.delete_many(where={"noteId": note_id})

    async def create_note_tags(self, note_id: str, tags: list[str]):
        for tag in tags:
            await db.notetag.create(data={"noteId": note_id, "tag": tag})

    # -----------------------------------------------------------------------
    # Exam Prep
    # -----------------------------------------------------------------------

    async def find_exam_prep(self, prep_id: str, user_id: str):
        return await db.examprep.find_first(
            where={"id": prep_id, "userId": user_id},
            include={"materials": True, "topics": True},
        )

    async def list_exam_preps(self, user_id: str):
        return await db.examprep.find_many(
            where={"userId": user_id},
            order={"createdAt": "desc"},
            include={"materials": True, "topics": True},
        )

    async def create_exam_prep(self, data: dict[str, Any]):
        return await db.examprep.create(data=data)

    async def update_exam_prep(self, prep_id: str, data: dict[str, Any]):
        return await db.examprep.update(where={"id": prep_id}, data=data)

    async def delete_exam_prep(self, prep_id: str):
        return await db.examprep.delete(where={"id": prep_id})

    # -----------------------------------------------------------------------
    # Generated Documents
    # -----------------------------------------------------------------------

    async def find_document(self, doc_id: str, user_id: str):
        return await db.generateddocument.find_first(
            where={"id": doc_id, "userId": user_id}
        )

    async def find_document_by_share_id(self, share_id: str):
        return await db.generateddocument.find_unique(where={"shareId": share_id})

    async def list_documents(
        self, user_id: str, *, skip: int = 0, take: int = 20
    ) -> tuple[list, int]:
        total = await db.generateddocument.count(where={"userId": user_id})
        items = await db.generateddocument.find_many(
            where={"userId": user_id},
            skip=skip,
            take=take,
            order={"createdAt": "desc"},
        )
        return items, total

    async def create_document(self, data: dict[str, Any]):
        return await db.generateddocument.create(data=data)

    async def update_document(self, doc_id: str, data: dict[str, Any]):
        return await db.generateddocument.update(where={"id": doc_id}, data=data)


# Singleton
personal_learning_repo = PersonalLearningRepository()
