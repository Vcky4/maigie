"""
Intelligence domain — Data access layer (SQLAlchemy).

Queries for chat sessions, messages, AI actions, memory (facts, insights,
interaction memory, conversation summaries), agent tasks, uploads, and LLM cost records.
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
    AIActionLog,
    AIAgentTask,
    ChatMessage,
    ChatSession,
    ConversationSummary,
    LearningInsight,
    LlmCostRecord,
    UserFact,
    UserInteractionMemory,
    UserUpload,
)

logger = logging.getLogger(__name__)


class IntelligenceRepository:
    """Data access for Intelligence domain entities."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    # -----------------------------------------------------------------------
    # Chat Sessions
    # -----------------------------------------------------------------------

    async def find_chat_session(self, session_id: str) -> ChatSession | None:
        async with await self._session() as session:
            stmt = select(ChatSession).where(ChatSession.id == session_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_chat_sessions(
        self, user_id: str, *, take: int = 20, skip: int = 0, active_only: bool = True
    ) -> list[ChatSession]:
        async with await self._session() as session:
            conditions = [ChatSession.user_id == user_id]
            if active_only:
                conditions.append(ChatSession.is_active == True)  # noqa: E712
            stmt = (
                select(ChatSession)
                .where(*conditions)
                .order_by(ChatSession.updated_at.desc())
                .offset(skip)
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_chat_session(self, data: dict[str, Any]) -> ChatSession:
        async with await self._session() as session:
            chat_session = ChatSession(**self._map_chat_session(data))
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            return chat_session

    async def update_chat_session(self, session_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_chat_session(data)
            stmt = update(ChatSession).where(ChatSession.id == session_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def delete_chat_session(self, session_id: str) -> None:
        async with await self._session() as session:
            stmt = delete(ChatSession).where(ChatSession.id == session_id)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Chat Messages
    # -----------------------------------------------------------------------

    async def find_messages(
        self,
        session_id: str,
        *,
        take: int = 50,
        order_asc: bool = True,
        review_item_id: str | None = None,
    ) -> list[ChatMessage]:
        async with await self._session() as session:
            conditions = [ChatMessage.session_id == session_id]
            if review_item_id is not None:
                conditions.append(ChatMessage.review_item_id == review_item_id)
            else:
                conditions.append(ChatMessage.review_item_id.is_(None))
            stmt = select(ChatMessage).where(*conditions)
            if order_asc:
                stmt = stmt.order_by(ChatMessage.created_at.asc())
            else:
                stmt = stmt.order_by(ChatMessage.created_at.desc())
            stmt = stmt.limit(take)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_upload(self, upload_id: str, user_id: str) -> Any:
        """One attachment, **as its owner's**.

        Owner-scoped in the `where`, not checked after the read: an upload id in a `DELETE` path is a
        client-supplied id, and this is the only thing standing between it and someone else's file.
        """
        from .db_models import UserUpload

        async with await self._session() as session:
            stmt = select(UserUpload).where(
                UserUpload.id == upload_id, UserUpload.user_id == user_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_upload(self, upload_id: str, user_id: str) -> bool:
        """Remove an attachment row. Owner-scoped for the same reason as the read."""
        from .db_models import UserUpload

        async with await self._session() as session:
            stmt = select(UserUpload).where(
                UserUpload.id == upload_id, UserUpload.user_id == user_id
            )
            upload = (await session.execute(stmt)).scalar_one_or_none()
            if upload is None:
                return False
            await session.delete(upload)
            await session.commit()
            return True

    async def create_message(self, data: dict[str, Any]) -> ChatMessage:
        async with await self._session() as session:
            msg = ChatMessage(**self._map_message(data))
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            return msg

    async def find_message(self, message_id: str) -> ChatMessage | None:
        async with await self._session() as session:
            stmt = select(ChatMessage).where(ChatMessage.id == message_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def count_user_messages(self, user_id: str, *, since: datetime | None = None) -> int:
        async with await self._session() as session:
            conditions = [ChatMessage.user_id == user_id]
            if since:
                conditions.append(ChatMessage.created_at >= since)
            stmt = select(func.count()).select_from(ChatMessage).where(*conditions)
            return (await session.execute(stmt)).scalar() or 0

    # -----------------------------------------------------------------------
    # AI Action Logs
    # -----------------------------------------------------------------------

    async def create_action_log(self, data: dict[str, Any]) -> AIActionLog:
        async with await self._session() as session:
            log = AIActionLog(**self._map_action_log(data))
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def find_action_logs(
        self, *, session_id: str | None = None, message_id: str | None = None
    ) -> list[AIActionLog]:
        async with await self._session() as session:
            if message_id:
                stmt = select(AIActionLog).where(AIActionLog.message_id == message_id)
            elif session_id:
                stmt = (
                    select(AIActionLog)
                    .join(ChatMessage, AIActionLog.message_id == ChatMessage.id)
                    .where(ChatMessage.session_id == session_id)
                )
            else:
                return []
            stmt = stmt.limit(50)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Conversation Summaries
    # -----------------------------------------------------------------------

    async def find_summary_by_session(
        self, session_id: str, user_id: str
    ) -> ConversationSummary | None:
        async with await self._session() as session:
            stmt = select(ConversationSummary).where(
                ConversationSummary.session_id == session_id,
                ConversationSummary.user_id == user_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_summaries(self, user_id: str, *, take: int = 5) -> list[ConversationSummary]:
        async with await self._session() as session:
            stmt = (
                select(ConversationSummary)
                .where(ConversationSummary.user_id == user_id)
                .order_by(ConversationSummary.created_at.desc())
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_summary(self, data: dict[str, Any]) -> ConversationSummary:
        async with await self._session() as session:
            summary = ConversationSummary(**self._map_summary(data))
            session.add(summary)
            await session.commit()
            await session.refresh(summary)
            return summary

    # -----------------------------------------------------------------------
    # User Facts
    # -----------------------------------------------------------------------

    async def list_user_facts(
        self, user_id: str, *, category: str | None = None, active_only: bool = True, take: int = 30
    ) -> list[UserFact]:
        async with await self._session() as session:
            conditions = [UserFact.user_id == user_id]
            if active_only:
                conditions.append(UserFact.is_active == True)  # noqa: E712
            if category:
                conditions.append(UserFact.category == category)
            stmt = (
                select(UserFact).where(*conditions).order_by(UserFact.updated_at.desc()).limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_user_fact(
        self, user_id: str, category: str, content_like: str | None = None
    ) -> UserFact | None:
        async with await self._session() as session:
            conditions = [
                UserFact.user_id == user_id,
                UserFact.category == category,
                UserFact.is_active.is_(True),
            ]
            stmt = select(UserFact).where(*conditions).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_user_fact(self, data: dict[str, Any]) -> UserFact:
        async with await self._session() as session:
            fact = UserFact(**self._map_user_fact(data))
            session.add(fact)
            await session.commit()
            await session.refresh(fact)
            return fact

    async def update_user_fact(self, fact_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_user_fact(data)
            stmt = update(UserFact).where(UserFact.id == fact_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def deactivate_user_fact(self, fact_id: str) -> None:
        async with await self._session() as session:
            stmt = update(UserFact).where(UserFact.id == fact_id).values(is_active=False)
            await session.execute(stmt)
            await session.commit()

    # -----------------------------------------------------------------------
    # Learning Insights
    # -----------------------------------------------------------------------

    async def list_insights(
        self,
        user_id: str,
        *,
        active_only: bool = True,
        min_confidence: float | None = None,
        take: int = 10,
    ) -> list[LearningInsight]:
        async with await self._session() as session:
            conditions = [LearningInsight.user_id == user_id]
            if active_only:
                conditions.append(LearningInsight.is_active == True)  # noqa: E712
            if min_confidence is not None:
                conditions.append(LearningInsight.confidence >= min_confidence)
            stmt = (
                select(LearningInsight)
                .where(*conditions)
                .order_by(LearningInsight.updated_at.desc())
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_insight(self, user_id: str, insight_type: str) -> LearningInsight | None:
        async with await self._session() as session:
            stmt = select(LearningInsight).where(
                LearningInsight.user_id == user_id,
                LearningInsight.insight_type == insight_type,
                LearningInsight.is_active == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def upsert_insight(self, user_id: str, insight_type: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            stmt = select(LearningInsight).where(
                LearningInsight.user_id == user_id,
                LearningInsight.insight_type == insight_type,
                LearningInsight.is_active == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                mapped = self._map_insight(data)
                for key, value in mapped.items():
                    setattr(existing, key, value)
                await session.commit()
            else:
                insight = LearningInsight(
                    user_id=user_id,
                    insight_type=insight_type,
                    **self._map_insight(data),
                )
                session.add(insight)
                await session.commit()

    # -----------------------------------------------------------------------
    # User Interaction Memory
    # -----------------------------------------------------------------------

    async def create_interaction(self, data: dict[str, Any]) -> UserInteractionMemory:
        async with await self._session() as session:
            interaction = UserInteractionMemory(**self._map_interaction(data))
            session.add(interaction)
            await session.commit()
            await session.refresh(interaction)
            return interaction

    async def list_interactions(
        self,
        user_id: str,
        *,
        interaction_type: str | None = None,
        entity_type: str | None = None,
        take: int = 100,
    ) -> list[UserInteractionMemory]:
        async with await self._session() as session:
            conditions = [UserInteractionMemory.user_id == user_id]
            if interaction_type:
                conditions.append(UserInteractionMemory.interaction_type == interaction_type)
            if entity_type:
                conditions.append(UserInteractionMemory.entity_type == entity_type)
            stmt = (
                select(UserInteractionMemory)
                .where(*conditions)
                .order_by(UserInteractionMemory.created_at.desc())
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # AI Agent Tasks (Nudges)
    # -----------------------------------------------------------------------

    async def list_pending_tasks(
        self, user_id: str, *, before: datetime | None = None, take: int = 5
    ) -> list[AIAgentTask]:
        async with await self._session() as session:
            conditions = [AIAgentTask.user_id == user_id, AIAgentTask.status == "pending"]
            if before:
                conditions.append(AIAgentTask.scheduled_at <= before)
            stmt = (
                select(AIAgentTask)
                .where(*conditions)
                .order_by(AIAgentTask.priority.desc())
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_task(self, task_id: str, data: dict[str, Any]) -> None:
        async with await self._session() as session:
            mapped = self._map_agent_task(data)
            stmt = update(AIAgentTask).where(AIAgentTask.id == task_id).values(**mapped)
            await session.execute(stmt)
            await session.commit()

    async def create_agent_task(self, data: dict[str, Any]) -> AIAgentTask:
        async with await self._session() as session:
            task = AIAgentTask(**self._map_agent_task(data))
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    # -----------------------------------------------------------------------
    # LLM Cost Records
    # -----------------------------------------------------------------------

    async def create_cost_record(self, data: dict[str, Any]) -> LlmCostRecord:
        async with await self._session() as session:
            record = LlmCostRecord(**self._map_cost_record(data))
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    # -----------------------------------------------------------------------
    # User Uploads
    # -----------------------------------------------------------------------

    async def create_upload(self, data: dict[str, Any]) -> UserUpload:
        async with await self._session() as session:
            upload = UserUpload(**self._map_upload(data))
            session.add(upload)
            await session.commit()
            await session.refresh(upload)
            return upload

    async def list_uploads(self, user_id: str, *, take: int = 20) -> list[UserUpload]:
        async with await self._session() as session:
            stmt = (
                select(UserUpload)
                .where(UserUpload.user_id == user_id)
                .order_by(UserUpload.created_at.desc())
                .limit(take)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Field mapping helpers
    # -----------------------------------------------------------------------

    _CHAT_SESSION_MAP = {
        "userId": "user_id",
        "title": "title",
        "isActive": "is_active",
        "isSpaceRoom": "is_space_room",
        "sessionType": "session_type",
        "spaceId": "space_id",
        "courseId": "course_id",
        "topicId": "topic_id",
        "examPrepId": "exam_prep_id",
        "noteId": "note_id",
    }

    _MESSAGE_MAP = {
        "sessionId": "session_id",
        "userId": "user_id",
        "reviewItemId": "review_item_id",
        "replyToMessageId": "reply_to_message_id",
        "role": "role",
        "content": "content",
        "suggestionText": "suggestion_text",
        "audioUrl": "audio_url",
        "imageUrl": "image_url",
        "imageUrls": "image_urls",
        "duration": "duration",
        "componentData": "component_data",
        "tokenCount": "token_count",
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "modelName": "model_name",
        "costUsd": "cost_usd",
        "revenueUsd": "revenue_usd",
        # Provenance, added with migration 049_chat_msg_grounding. Listed here because `map_fields`
        # raises on a key it does not know — a column added to the model but not to this map is a
        # write that fails loudly rather than one that silently disappears. See
        # `tests/test_field_mapping_completeness.py` for why that guarantee exists.
        "citations": "citations",
        "truncated": "truncated",
        "askMode": "ask_mode",
    }

    _ACTION_LOG_MAP = {
        "messageId": "message_id",
        "actionType": "action_type",
        "actionData": "action_data",
        "status": "status",
        "error": "error",
    }

    _SUMMARY_MAP = {
        "userId": "user_id",
        "sessionId": "session_id",
        "summary": "summary",
        "keyTopics": "key_topics",
        "actionsTaken": "actions_taken",
        "emotionalTone": "emotional_tone",
    }

    _USER_FACT_MAP = {
        "userId": "user_id",
        "category": "category",
        "content": "content",
        "source": "source",
        "confidence": "confidence",
        "isActive": "is_active",
    }

    _INSIGHT_MAP = {
        "content": "content",
        "confidence": "confidence",
        "dataPoints": "data_points",
        "metadata": "metadata_json",
        "isActive": "is_active",
    }

    _INTERACTION_MAP = {
        "userId": "user_id",
        "interactionType": "interaction_type",
        "entityType": "entity_type",
        "entityId": "entity_id",
        "metadata": "metadata_json",
        "importance": "importance",
    }

    _AGENT_TASK_MAP = {
        "userId": "user_id",
        "taskType": "task_type",
        "status": "status",
        "priority": "priority",
        "title": "title",
        "message": "message",
        "actionData": "action_data",
        "scheduledAt": "scheduled_at",
        "sentAt": "sent_at",
        "dismissedAt": "dismissed_at",
    }

    _COST_RECORD_MAP = {
        "userId": "user_id",
        "userTier": "user_tier",
        "provider": "provider",
        "model": "model",
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "costUsd": "cost_usd",
    }

    _UPLOAD_MAP = {
        "userId": "user_id",
        "url": "url",
        "filename": "filename",
        "mimeType": "mime_type",
        "size": "size",
        "extractedText": "extracted_text",
        "embeddingId": "embedding_id",
        "chatMessageId": "chat_message_id",
    }

    def _map_chat_session(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._CHAT_SESSION_MAP, entity="_map_chat_session")

    def _map_message(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._MESSAGE_MAP, entity="_map_message")

    def _map_action_log(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._ACTION_LOG_MAP, entity="_map_action_log")

    def _map_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._SUMMARY_MAP, entity="_map_summary")

    def _map_user_fact(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._USER_FACT_MAP, entity="_map_user_fact")

    def _map_insight(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._INSIGHT_MAP, entity="_map_insight")

    def _map_interaction(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._INTERACTION_MAP, entity="_map_interaction")

    def _map_agent_task(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._AGENT_TASK_MAP, entity="_map_agent_task")

    def _map_cost_record(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._COST_RECORD_MAP, entity="_map_cost_record")

    def _map_upload(self, data: dict[str, Any]) -> dict[str, Any]:
        return map_fields(data, self._UPLOAD_MAP, entity="_map_upload")


# Singleton
intelligence_repo = IntelligenceRepository()
