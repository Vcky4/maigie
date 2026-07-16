"""
User Memory Service for storing and retrieving user interactions.
Enables personalized experiences by tracking important user behaviors.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..repository import intelligence_repo


class UserMemoryService:
    """Service for managing user interaction memory."""

    def __init__(self):
        """Initialize the user memory service."""
        pass

    async def record_interaction(
        self,
        user_id: str,
        interaction_type: str,
        entity_type: str,
        entity_id: str | None = None,
        metadata: dict[str, Any] | Any | None = None,
        importance: float = 0.5,
    ) -> str:
        """
        Record a user interaction for personalization.

        Args:
            user_id: ID of the user
            interaction_type: Type of interaction (see InteractionType enum)
            entity_type: Type of entity ("resource", "course", "note", "goal", "chat")
            entity_id: Optional ID of the entity
            metadata: Optional additional context (dict or Pydantic model)
            importance: Importance score (0.0 to 1.0) for this interaction

        Returns:
            ID of the created interaction record
        """
        try:
            # Sanitize metadata
            clean_metadata = {}
            if metadata:
                try:
                    if hasattr(metadata, "model_dump"):
                        temp = metadata.model_dump()
                    elif hasattr(metadata, "dict"):
                        temp = metadata.dict()
                    elif isinstance(metadata, str):
                        try:
                            temp = json.loads(metadata)
                        except Exception:
                            temp = {"raw_content": metadata}
                    elif isinstance(metadata, dict):
                        temp = metadata
                    else:
                        temp = {"value": str(metadata)}

                    # Deep sanitize via JSON round-trip
                    clean_metadata = json.loads(json.dumps(temp, default=str))
                except Exception as e:
                    clean_metadata = {"error": "Invalid metadata format", "details": str(e)}

            interaction = await intelligence_repo.create_interaction(
                {
                    "userId": str(user_id),
                    "interactionType": interaction_type,
                    "entityType": entity_type,
                    "entityId": str(entity_id) if entity_id else None,
                    "metadata": clean_metadata,
                    "importance": float(importance),
                }
            )
            return interaction.id

        except Exception as e:
            print(f"Error recording interaction: {e}")
            return ""

    async def get_user_preferences(self, user_id: str, limit: int = 50) -> dict[str, Any]:
        """
        Get user preferences based on interaction history.

        Args:
            user_id: ID of the user
            limit: Maximum number of interactions to analyze

        Returns:
            Dictionary with user preferences and patterns
        """
        try:
            recent_interactions = await intelligence_repo.list_interactions(user_id, take=limit)

            preferences = {
                "preferredResourceTypes": [],
                "activeCourses": [],
                "recentTopics": [],
                "interactionPatterns": {},
                "learningGoals": [],
            }

            resource_type_counts: dict[str, int] = {}
            course_ids: set[str] = set()
            topic_ids: set[str] = set()
            interaction_counts: dict[str, int] = {}

            for interaction in recent_interactions:
                interaction_type = interaction.interaction_type
                interaction_counts[interaction_type] = (
                    interaction_counts.get(interaction_type, 0) + 1
                )

                entity_type = interaction.entity_type
                entity_id = interaction.entity_id

                if entity_type == "course" and entity_id:
                    course_ids.add(entity_id)
                elif entity_type == "topic" and entity_id:
                    topic_ids.add(entity_id)

            preferences["activeCourses"] = list(course_ids)[:10]
            preferences["recentTopics"] = list(topic_ids)[:10]
            preferences["interactionPatterns"] = interaction_counts

            return preferences

        except Exception as e:
            print(f"Error getting user preferences: {e}")
            return {
                "preferredResourceTypes": [],
                "activeCourses": [],
                "recentTopics": [],
                "interactionPatterns": {},
                "learningGoals": [],
            }

    async def get_recent_interactions(
        self,
        user_id: str,
        interaction_type: str | None = None,
        entity_type: str | None = None,
        days: int = 30,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get recent user interactions.

        Args:
            user_id: ID of the user
            interaction_type: Optional filter by interaction type
            entity_type: Optional filter by entity type
            days: Number of days to look back
            limit: Maximum number of results

        Returns:
            List of interaction records
        """
        try:
            threshold_date = datetime.now(UTC) - timedelta(days=days)

            interactions = await intelligence_repo.list_interactions(
                user_id,
                interaction_type=interaction_type,
                entity_type=entity_type,
                take=limit,
            )

            # Filter by date in Python
            filtered = [i for i in interactions if i.created_at >= threshold_date]

            return [
                {
                    "id": i.id,
                    "interactionType": i.interaction_type,
                    "entityType": i.entity_type,
                    "entityId": i.entity_id,
                    "metadata": i.metadata_json,
                    "importance": i.importance,
                    "createdAt": i.created_at.isoformat(),
                }
                for i in filtered
            ]

        except Exception as e:
            print(f"Error getting recent interactions: {e}")
            return []

    async def get_user_context(self, user_id: str) -> dict[str, Any]:
        """
        Get comprehensive user context for personalization.

        Args:
            user_id: ID of the user

        Returns:
            Dictionary with user context including courses, goals, recent activity
        """
        try:
            from src.domains.knowledge.repository import KnowledgeRepository
            from src.domains.progress.repository import progress_repo

            knowledge_repo = KnowledgeRepository()

            # Get user's courses
            courses, _ = await knowledge_repo.list_courses(
                user_id, where={"archived": False}, skip=0, take=10
            )

            # Get user's goals
            goals, _ = await progress_repo.list_goals(
                user_id, where={"status": "ACTIVE"}, skip=0, take=10
            )

            # Get recent interactions
            recent_interactions = await self.get_recent_interactions(
                user_id=user_id, days=7, limit=20
            )

            # Get user preferences
            preferences = await self.get_user_preferences(user_id=user_id)

            return {
                "courses": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "description": c.description,
                        "difficulty": c.difficulty,
                        "progress": c.progress,
                    }
                    for c in courses
                ],
                "goals": [
                    {
                        "id": g.id,
                        "title": g.title,
                        "description": g.description,
                        "targetDate": g.target_date.isoformat() if g.target_date else None,
                        "status": g.status,
                        "progress": g.progress,
                        "courseId": g.course_id,
                        "topicId": g.topic_id,
                    }
                    for g in goals
                ],
                "recentNotes": [],  # Notes not yet in SQLAlchemy
                "recentActivity": recent_interactions,
                "preferences": preferences,
            }

        except Exception as e:
            print(f"Error getting user context: {e}")
            return {
                "courses": [],
                "goals": [],
                "recentNotes": [],
                "recentActivity": [],
                "preferences": {},
            }

    async def get_user_facts(
        self, user_id: str, category: str | None = None, limit: int = 30
    ) -> list[dict[str, Any]]:
        """
        Get stored facts about the user.

        Args:
            user_id: ID of the user
            category: Optional filter by category
            limit: Maximum number of facts to return

        Returns:
            List of fact records
        """
        try:
            facts = await intelligence_repo.list_user_facts(
                user_id, category=category, active_only=True, take=limit
            )

            return [
                {
                    "id": f.id,
                    "category": f.category,
                    "content": f.content,
                    "source": f.source,
                    "confidence": f.confidence,
                    "createdAt": f.created_at.isoformat(),
                }
                for f in facts
            ]
        except Exception as e:
            print(f"Error getting user facts: {e}")
            return []

    async def save_user_fact(
        self,
        user_id: str,
        category: str,
        content: str,
        source: str = "conversation",
        confidence: float = 0.8,
    ) -> str:
        """
        Save a fact about the user.

        Args:
            user_id: ID of the user
            category: Fact category
            content: The fact content
            source: Source of the fact
            confidence: Confidence score

        Returns:
            ID of the created fact
        """
        try:
            fact = await intelligence_repo.create_user_fact(
                {
                    "userId": user_id,
                    "category": category,
                    "content": content,
                    "source": source,
                    "confidence": confidence,
                }
            )
            return fact.id
        except Exception as e:
            print(f"Error saving user fact: {e}")
            return ""

    async def deactivate_fact(self, fact_id: str) -> bool:
        """Deactivate (soft-delete) a user fact."""
        try:
            await intelligence_repo.deactivate_user_fact(fact_id)
            return True
        except Exception as e:
            print(f"Error deactivating fact: {e}")
            return False


# Singleton
user_memory_service = UserMemoryService()
