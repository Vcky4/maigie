"""
Indexing Service for creating embeddings when content is created or updated.
Can be called as a background task.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from typing import Any

from src.core.database import db
from src.services.embedding_service import embedding_service


class IndexingService:
    """Service for indexing content with embeddings."""

    def __init__(self):
        """Initialize the indexing service."""
        pass

    async def index_resource(self, resource_id: str) -> None:
        """
        Index a resource by creating an embedding.

        Args:
            resource_id: ID of the resource to index
        """
        try:
            resource = await db.resource.find_unique(where={"id": resource_id})

            if not resource:
                print(f"Resource {resource_id} not found for indexing")
                return

            # Build content string for embedding
            content_parts = [resource.title]
            if resource.description:
                content_parts.append(resource.description)

            # Add metadata if available
            if resource.metadata:
                metadata_str = str(resource.metadata)
                content_parts.append(metadata_str[:500])  # Limit metadata length

            content = " ".join(content_parts)

            # Create or update embedding
            await embedding_service.update_embedding(
                object_type="resource",
                object_id=resource_id,
                content=content,
                metadata={
                    "title": resource.title,
                    "type": resource.type,
                    "url": resource.url,
                },
                resource_id=resource_id,
            )

            print(f"✅ Indexed resource: {resource_id}")

        except Exception as e:
            print(f"Error indexing resource {resource_id}: {e}")
            # Don't raise - indexing failures shouldn't break the main flow

    async def index_note(self, note_id: str) -> None:
        """
        Index a note by creating an embedding.

        Args:
            note_id: ID of the note to index
        """
        try:
            note = await db.note.find_unique(where={"id": note_id})

            if not note:
                print(f"Note {note_id} not found for indexing")
                return

            # Build content string for embedding
            content_parts = [note.title]
            if note.content:
                content_parts.append(note.content)
            if note.summary:
                content_parts.append(note.summary)

            content = " ".join(content_parts)

            # Create or update embedding
            await embedding_service.update_embedding(
                object_type="note",
                object_id=note_id,
                content=content,
                metadata={
                    "title": note.title,
                    "courseId": note.courseId,
                    "topicId": note.topicId,
                },
            )

            print(f"✅ Indexed note: {note_id}")

        except Exception as e:
            print(f"Error indexing note {note_id}: {e}")

    async def index_course(self, course_id: str) -> None:
        """
        Index a course by creating an embedding.

        Args:
            course_id: ID of the course to index
        """
        try:
            course = await db.course.find_unique(
                where={"id": course_id}, include={"modules": {"include": {"topics": True}}}
            )

            if not course:
                print(f"Course {course_id} not found for indexing")
                return

            # Build content string for embedding
            content_parts = [course.title]
            if course.description:
                content_parts.append(course.description)

            # Include module and topic titles
            for module in course.modules:
                content_parts.append(module.title)
                if module.description:
                    content_parts.append(module.description)
                for topic in module.topics:
                    content_parts.append(topic.title)
                    if topic.content:
                        content_parts.append(topic.content[:500])  # Limit topic content

            content = " ".join(content_parts)

            # Create or update embedding
            await embedding_service.update_embedding(
                object_type="course",
                object_id=course_id,
                content=content,
                metadata={
                    "title": course.title,
                    "difficulty": course.difficulty,
                    "userId": course.userId,
                },
            )

            print(f"✅ Indexed course: {course_id}")

        except Exception as e:
            print(f"Error indexing course {course_id}: {e}")

    async def index_topic(self, topic_id: str) -> None:
        """
        Index a topic by creating an embedding.

        Args:
            topic_id: ID of the topic to index
        """
        try:
            topic = await db.topic.find_unique(where={"id": topic_id})

            if not topic:
                print(f"Topic {topic_id} not found for indexing")
                return

            # Build content string for embedding
            content_parts = [topic.title]
            if topic.content:
                content_parts.append(topic.content)

            content = " ".join(content_parts)

            # Create or update embedding
            await embedding_service.update_embedding(
                object_type="topic",
                object_id=topic_id,
                content=content,
                metadata={
                    "title": topic.title,
                    "moduleId": topic.moduleId,
                },
            )

            print(f"✅ Indexed topic: {topic_id}")

        except Exception as e:
            print(f"Error indexing topic {topic_id}: {e}")

    async def index_resource_bank_item(self, item_id: str) -> None:
        """
        Index a resource bank item by creating an embedding from its
        metadata and extracted file text.

        Args:
            item_id: ID of the ResourceBankItem to index
        """
        try:
            item = await db.resourcebankitem.find_unique(
                where={"id": item_id},
                include={"files": True},
            )

            if not item:
                print(f"ResourceBankItem {item_id} not found for indexing")
                return

            # Build content string
            content_parts = [item.title]
            if item.description:
                content_parts.append(item.description)
            content_parts.append(f"University: {item.universityName}")
            if item.courseName:
                content_parts.append(f"Course: {item.courseName}")
            if item.courseCode:
                content_parts.append(f"Course Code: {item.courseCode}")
            content_parts.append(f"Type: {item.type}")

            # Include extracted text from files
            if item.files:
                for f in item.files:
                    if f.extractedText:
                        content_parts.append(f.extractedText[:2000])

            content = " ".join(content_parts)

            metadata: dict[str, Any] = {
                "title": item.title,
                "type": str(item.type),
                "universityName": item.universityName,
            }
            if item.courseName:
                metadata["courseName"] = item.courseName
            if item.courseCode:
                metadata["courseCode"] = item.courseCode

            await embedding_service.update_embedding(
                object_type="resource_bank_item",
                object_id=item_id,
                content=content[:5000],
                metadata=metadata,
                resource_bank_item_id=item_id,
            )

            print(f"✅ Indexed resource bank item: {item_id}")

        except Exception as e:
            print(f"Error indexing resource bank item {item_id}: {e}")

    async def reindex_all_user_content(self, user_id: str) -> dict[str, int]:
        """
        Reindex all content for a user (useful for migration or updates).

        Args:
            user_id: ID of the user

        Returns:
            Dictionary with counts of indexed items
        """
        counts = {"resources": 0, "notes": 0, "courses": 0, "topics": 0}

        try:
            # Index all resources
            resources = await db.resource.find_many(where={"userId": user_id})
            for resource in resources:
                await self.index_resource(resource.id)
                counts["resources"] += 1

            # Index all notes
            notes = await db.note.find_many(where={"userId": user_id})
            for note in notes:
                await self.index_note(note.id)
                counts["notes"] += 1

            # Index all courses
            courses = await db.course.find_many(where={"userId": user_id})
            for course in courses:
                await self.index_course(course.id)
                counts["courses"] += 1

            # Index all topics (through courses)
            for course in courses:
                modules = await db.module.find_many(
                    where={"courseId": course.id}, include={"topics": True}
                )
                for module in modules:
                    for topic in module.topics:
                        await self.index_topic(topic.id)
                        counts["topics"] += 1

            print(f"✅ Reindexed all content for user {user_id}: {counts}")

        except Exception as e:
            print(f"Error reindexing user content: {e}")

        return counts


# Global instance
indexing_service = IndexingService()
