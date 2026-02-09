"""
Embedding Service for generating and storing vector embeddings.
Uses Google Gemini's embedding API.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import json
import os
from typing import Any

import google.generativeai as genai
from fastapi import HTTPException

from src.core.database import db

# Configure API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Use Gemini embedding model (text-embedding-004 is deprecated/removed for v1beta)
# Note: The embed_content function adds the "models/" prefix automatically
# Existing DB embeddings from text-embedding-004 may need re-indexing for best results
EMBEDDING_MODEL = "gemini-embedding-001"


class EmbeddingService:
    """Service for generating and managing embeddings."""

    def __init__(self):
        """Initialize the embedding service."""
        pass

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate an embedding vector for the given text.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector
        """
        try:
            # Use Gemini's embedding model
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=text,
                task_type="retrieval_document",  # Use retrieval_document for documents
            )

            embedding = result["embedding"]
            return embedding

        except Exception as e:
            print(f"Embedding generation error: {e}")
            raise HTTPException(status_code=500, detail="Failed to generate embedding")

    async def generate_query_embedding(self, text: str) -> list[float]:
        """
        Generate an embedding vector for a search query.

        Args:
            text: The query text to embed

        Returns:
            List of floats representing the embedding vector
        """
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=text,
                task_type="retrieval_query",  # Use retrieval_query for queries
            )

            embedding = result["embedding"]
            return embedding

        except Exception as e:
            print(f"Query embedding generation error: {e}")
            raise HTTPException(status_code=500, detail="Failed to generate query embedding")

    async def store_embedding(
        self,
        object_type: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        resource_id: str | None = None,
    ) -> str:
        """
        Generate and store an embedding in the database.

        Args:
            object_type: Type of object ("resource", "note", "course", "topic")
            object_id: ID of the object
            content: Text content to embed
            metadata: Optional metadata about the embedding
            resource_id: Optional resource ID if this is a resource embedding

        Returns:
            ID of the created embedding record
        """
        try:
            # Generate embedding
            embedding_vector = await self.generate_embedding(content)

            # Store in database (wrap vector in Json for Prisma)
            from prisma import Json

            embedding_record = await db.embedding.create(
                data={
                    "objectType": object_type,
                    "objectId": object_id,
                    "vector": Json(embedding_vector),  # Wrap in Json for Prisma
                    "content": content[:1000] if content else None,  # Store truncated content
                    "metadata": Json(metadata) if metadata else None,
                    "resourceId": resource_id,
                }
            )

            return embedding_record.id

        except Exception as e:
            print(f"Error storing embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to store embedding")

    async def find_similar(
        self,
        query_text: str,
        object_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Find similar embeddings using cosine similarity.

        Note: This is a simplified implementation. For production,
        you should use pgvector extension for efficient vector similarity search.

        Args:
            query_text: The query text to search for
            object_type: Optional filter by object type
            limit: Maximum number of results
            threshold: Minimum similarity threshold (0.0 to 1.0)

        Returns:
            List of similar objects with their similarity scores
        """
        try:
            # Generate query embedding
            query_embedding = await self.generate_query_embedding(query_text)

            # Get embeddings (or filtered by object_type) with a reasonable limit
            # For production, this should use pgvector for efficient similarity search
            # For now, we limit to a reasonable number to avoid loading all embeddings
            where_clause = {}
            if object_type:
                where_clause["objectType"] = object_type

            # Limit to 1000 embeddings max for performance
            # In production with pgvector, this would be handled by the database
            max_embeddings_to_check = min(limit * 20, 1000)  # Check up to 20x the limit or 1000 max
            all_embeddings = await db.embedding.find_many(
                where=where_clause,
                take=max_embeddings_to_check,
                order={"createdAt": "desc"},  # Prefer recent embeddings
            )

            # Calculate cosine similarity for each embedding
            similarities = []
            for emb in all_embeddings:
                if not emb.vector:
                    continue

                # Calculate cosine similarity
                similarity = self._cosine_similarity(query_embedding, emb.vector)

                if similarity >= threshold:
                    similarities.append(
                        {
                            "objectType": emb.objectType,
                            "objectId": emb.objectId,
                            "similarity": similarity,
                            "content": emb.content,
                            "metadata": emb.metadata,
                        }
                    )

            # Sort by similarity (descending) and return top results
            similarities.sort(key=lambda x: x["similarity"], reverse=True)
            return similarities[:limit]

        except Exception as e:
            print(f"Error finding similar embeddings: {e}")
            raise HTTPException(status_code=500, detail="Failed to find similar embeddings")

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        import math

        # Ensure vectors are the same length
        if len(vec1) != len(vec2):
            return 0.0

        # Calculate dot product
        dot_product = sum(a * b for a, b in zip(vec1, vec2))

        # Calculate magnitudes
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(a * a for a in vec2))

        # Avoid division by zero
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        # Cosine similarity
        similarity = dot_product / (magnitude1 * magnitude2)

        # Normalize to 0-1 range (cosine similarity is already -1 to 1, but embeddings are typically positive)
        return max(0.0, similarity)

    async def update_embedding(
        self,
        object_type: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        resource_id: str | None = None,
    ) -> str:
        """
        Update an existing embedding or create a new one if it doesn't exist.

        Args:
            object_type: Type of object
            object_id: ID of the object
            content: New text content to embed
            metadata: Optional metadata
            resource_id: Optional resource ID (for linking)

        Returns:
            ID of the embedding record
        """
        try:
            # Check if embedding exists
            existing = await db.embedding.find_first(
                where={"objectType": object_type, "objectId": object_id}
            )

            if existing:
                # Update existing embedding
                embedding_vector = await self.generate_embedding(content)
                from prisma import Json

                data_to_update = {
                    "vector": Json(embedding_vector),  # Wrap in Json for Prisma
                    "content": content[:1000] if content else None,
                    "metadata": Json(metadata) if metadata else None,
                }

                # Update resourceId if provided
                if resource_id:
                    data_to_update["resourceId"] = resource_id

                updated = await db.embedding.update(
                    where={"id": existing.id},
                    data=data_to_update,
                )
                return updated.id
            else:
                # Create new embedding
                return await self.store_embedding(
                    object_type, object_id, content, metadata, resource_id
                )

        except Exception as e:
            print(f"Error updating embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to update embedding")

    async def delete_embedding(self, object_type: str, object_id: str) -> None:
        """
        Delete an embedding for a given object.

        Args:
            object_type: Type of object
            object_id: ID of the object
        """
        try:
            await db.embedding.delete_many(where={"objectType": object_type, "objectId": object_id})
        except Exception as e:
            print(f"Error deleting embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete embedding")


# Global instance
embedding_service = EmbeddingService()
