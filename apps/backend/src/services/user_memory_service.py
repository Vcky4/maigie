"""
User Memory Service for storing and retrieving user interactions.
Enables personalized experiences by tracking important user behaviors.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from prisma import Json
from src.core.database import db


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
            # 1. Sanitize and Wrap Metadata
            # Prisma's Json type is strict. We must ensure:
            # a) The data is a pure Python dictionary (no Pydantic models)
            # b) All values are JSON primitives (no UUIDs or Datetime objects)
            # c) It is wrapped in the explicit 'Json' type helper to satisfy the Union validator

            prisma_metadata = Json({})

            if metadata:
                try:
                    # Step A: Normalize input to a standard dict
                    if hasattr(metadata, "model_dump"):
                        temp = metadata.model_dump()
                    elif hasattr(metadata, "dict"):
                        temp = metadata.dict()
                    elif isinstance(metadata, str):
                        try:
                            temp = json.loads(metadata)
                        except Exception:  # <--- FIXED: Added specific exception
                            temp = {"raw_content": metadata}
                    elif isinstance(metadata, dict):
                        temp = metadata
                    else:
                        temp = {"value": str(metadata)}

                    # Step B: Deep Sanitize
                    # We round-trip through json.dumps with default=str.
                    # This converts complex types (UUID, Datetime) into strings that DB can store.
                    clean_dict = json.loads(json.dumps(temp, default=str))

                    # Step C: Wrap in Prisma Json type
                    prisma_metadata = Json(clean_dict)

                except Exception as e:
                    print(f"Metadata sanitization warning: {e}")
                    # Fallback to a safe error object rather than crashing
                    prisma_metadata = Json({"error": "Invalid metadata format", "details": str(e)})

            # 2. Build Data Payload
            # We use the 'Unchecked' strategy (providing the scalar userId string).
            # This is efficient and avoids the ambiguity of the 'user' relation object.
            interaction_data = {
                "userId": str(user_id),
                "interactionType": interaction_type,
                "entityType": entity_type,
                "importance": float(importance),
                "metadata": prisma_metadata,
            }

            # 3. Add Optional Entity ID
            if entity_id:
                interaction_data["entityId"] = str(entity_id)

            # 4. Create Record
            interaction = await db.userinteractionmemory.create(data=interaction_data)
            return interaction.id

        except Exception as e:
            # Log error strictly but return empty string to handle failure gracefully.
            # This ensures the user's main action (e.g., viewing a resource) isn't blocked
            # just because the analytics/tracking failed.
            print(f"Error recording interaction: {e}")
            # import traceback
            # traceback.print_exc()
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
            # Get recent important interactions
            recent_interactions = await db.userinteractionmemory.find_many(
                where={"userId": user_id},
                order={"createdAt": "desc"},
                take=limit,
            )

            preferences = {
                "preferredResourceTypes": [],
                "activeCourses": [],
                "recentTopics": [],
                "interactionPatterns": {},
                "learningGoals": [],
            }

            # Analyze interactions to extract preferences
            resource_type_counts = {}
            course_ids = set()
            topic_ids = set()
            interaction_counts = {}

            for interaction in recent_interactions:
                # Count interaction types
                interaction_type = interaction.interactionType
                interaction_counts[interaction_type] = (
                    interaction_counts.get(interaction_type, 0) + 1
                )

                # Extract entity information
                entity_type = interaction.entityType
                entity_id = interaction.entityId

                if entity_type == "resource" and entity_id:
                    # Try to get resource type
                    resource = await db.resource.find_unique(where={"id": entity_id})
                    if resource:
                        resource_type = resource.type
                        resource_type_counts[resource_type] = (
                            resource_type_counts.get(resource_type, 0) + 1
                        )

                elif entity_type == "course" and entity_id:
                    course_ids.add(entity_id)

                elif entity_type == "topic" and entity_id:
                    topic_ids.add(entity_id)

            # Build preferences
            if resource_type_counts:
                # Sort by frequency
                sorted_types = sorted(
                    resource_type_counts.items(), key=lambda x: x[1], reverse=True
                )
                preferences["preferredResourceTypes"] = [rtype for rtype, _ in sorted_types[:5]]

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
            where_clause = {"userId": user_id}

            if interaction_type:
                where_clause["interactionType"] = interaction_type

            if entity_type:
                where_clause["entityType"] = entity_type

            # Calculate date threshold (use timezone-aware datetime)
            threshold_date = datetime.now(UTC) - timedelta(days=days)

            interactions = await db.userinteractionmemory.find_many(
                where=where_clause,
                order={"createdAt": "desc"},
                take=limit,
            )

            # Filter by date in Python (Prisma doesn't support easy complex date filtering in where clause)
            filtered = [i for i in interactions if i.createdAt >= threshold_date]

            return [
                {
                    "id": i.id,
                    "interactionType": i.interactionType,
                    "entityType": i.entityType,
                    "entityId": i.entityId,
                    "metadata": i.metadata,
                    "importance": i.importance,
                    "createdAt": i.createdAt.isoformat(),
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
            # Get user's courses
            courses = await db.course.find_many(
                where={"userId": user_id, "archived": False},
                take=10,
                order={"updatedAt": "desc"},
            )

            # Get user's goals (active goals)
            goals = await db.goal.find_many(
                where={"userId": user_id, "status": "ACTIVE"},
                take=10,
                order={"updatedAt": "desc"},
            )

            # Get user's notes
            recent_notes = await db.note.find_many(
                where={"userId": user_id, "archived": False},
                take=10,
                order={"updatedAt": "desc"},
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
                        "targetDate": g.targetDate.isoformat() if g.targetDate else None,
                        "status": g.status,
                        "progress": g.progress,
                        "courseId": getattr(g, "courseId", None),
                        "topicId": getattr(g, "topicId", None),
                    }
                    for g in goals
                ],
                "recentNotes": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "summary": n.summary,
                        "courseId": n.courseId,
                    }
                    for n in recent_notes
                ],
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


# Global instance
user_memory_service = UserMemoryService()
