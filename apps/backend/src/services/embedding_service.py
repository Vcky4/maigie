"""
Embedding Service for generating and storing vector embeddings.
Uses Google Gemini for embedding generation and Pinecone for vector search.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import os
from typing import Any

from fastapi import HTTPException
from google import genai
from google.genai import types

from src.core.database import db

logger = logging.getLogger(__name__)

# Configure Gemini API client
_client = None


def get_genai_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _client


EMBEDDING_MODEL = "text-embedding-004"

# Gemini embedding-001 produces 3072-dimensional vectors
EMBEDDING_DIMENSION = 3072


def _get_pinecone_index():
    """Lazily initialise and return the Pinecone Index object."""
    from pinecone import Pinecone

    api_key = os.getenv("PINECONE_API_KEY", "")
    index_name = os.getenv("PINECONE_INDEX_NAME", "maigie")

    if not api_key:
        logger.warning("PINECONE_API_KEY not set – vector search will be unavailable")
        return None

    pc = Pinecone(api_key=api_key)

    # Create index if it doesn't exist
    existing_indexes = [idx.name for idx in pc.list_indexes()]
    if index_name not in existing_indexes:
        from pinecone import ServerlessSpec

        cloud = os.getenv("PINECONE_CLOUD", "aws")
        region = os.getenv("PINECONE_REGION", "us-east-1")
        pc.create_index(
            name=index_name,
            dimension=EMBEDDING_DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(cloud=cloud, region=region),
        )
        logger.info(f"Created Pinecone index '{index_name}' ({EMBEDDING_DIMENSION}d, cosine)")

    return pc.Index(index_name)


# Module-level lazy singleton
_pinecone_index = None


def _index():
    """Return cached Pinecone Index, creating it on first call."""
    global _pinecone_index
    if _pinecone_index is None:
        _pinecone_index = _get_pinecone_index()
    return _pinecone_index


def _make_vector_id(object_type: str, object_id: str) -> str:
    """Deterministic Pinecone vector ID."""
    return f"{object_type}:{object_id}"


class EmbeddingService:
    """Service for generating and managing embeddings via Gemini + Pinecone."""

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate a document embedding vector via Gemini."""
        try:
            client = get_genai_client()
            result = await client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
            )
            return result.embeddings[0].values
        except Exception as e:
            logger.error(f"Embedding generation error: {e}")
            raise HTTPException(status_code=500, detail="Failed to generate embedding")

    async def generate_query_embedding(self, text: str) -> list[float]:
        """Generate a query embedding vector via Gemini."""
        try:
            client = get_genai_client()
            result = await client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
            )
            return result.embeddings[0].values
        except Exception as e:
            logger.error(f"Query embedding generation error: {e}")
            raise HTTPException(status_code=500, detail="Failed to generate query embedding")

    # ------------------------------------------------------------------
    # Storage (Pinecone + Postgres audit)
    # ------------------------------------------------------------------

    async def store_embedding(
        self,
        object_type: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        resource_id: str | None = None,
        resource_bank_item_id: str | None = None,
    ) -> str:
        """
        Generate an embedding, upsert it into Pinecone, and write an audit
        row to Postgres.  Returns the Postgres Embedding record ID.
        """
        try:
            embedding_vector = await self.generate_embedding(content)

            # ---- Pinecone upsert ----
            pc_meta = {
                "objectType": object_type,
                "objectId": object_id,
                "content": (content[:500] if content else ""),
            }
            if metadata:
                # Pinecone metadata values must be str/int/float/bool/list[str]
                for k, v in metadata.items():
                    if v is not None and isinstance(v, (str, int, float, bool)):
                        pc_meta[k] = v

            idx = _index()
            if idx is not None:
                idx.upsert(
                    vectors=[
                        {
                            "id": _make_vector_id(object_type, object_id),
                            "values": embedding_vector,
                            "metadata": pc_meta,
                        }
                    ]
                )

            # ---- Postgres audit row ----
            from prisma import Json

            create_data: dict[str, Any] = {
                "objectType": object_type,
                "objectId": object_id,
                "vector": Json(embedding_vector),
                "content": content[:1000] if content else None,
                "metadata": Json(metadata) if metadata else None,
            }
            if resource_id:
                create_data["resourceId"] = resource_id
            if resource_bank_item_id:
                create_data["resourceBankItemId"] = resource_bank_item_id

            record = await db.embedding.create(data=create_data)
            return record.id

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error storing embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to store embedding")

    async def update_embedding(
        self,
        object_type: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        resource_id: str | None = None,
        resource_bank_item_id: str | None = None,
    ) -> str:
        """Upsert an embedding (create or update)."""
        try:
            embedding_vector = await self.generate_embedding(content)

            # ---- Pinecone upsert (idempotent) ----
            pc_meta = {
                "objectType": object_type,
                "objectId": object_id,
                "content": (content[:500] if content else ""),
            }
            if metadata:
                for k, v in metadata.items():
                    if v is not None and isinstance(v, (str, int, float, bool)):
                        pc_meta[k] = v

            idx = _index()
            if idx is not None:
                idx.upsert(
                    vectors=[
                        {
                            "id": _make_vector_id(object_type, object_id),
                            "values": embedding_vector,
                            "metadata": pc_meta,
                        }
                    ]
                )

            # ---- Postgres upsert ----
            from prisma import Json

            existing = await db.embedding.find_first(
                where={"objectType": object_type, "objectId": object_id}
            )

            update_data: dict[str, Any] = {
                "vector": Json(embedding_vector),
                "content": content[:1000] if content else None,
                "metadata": Json(metadata) if metadata else None,
            }
            if resource_id:
                update_data["resourceId"] = resource_id
            if resource_bank_item_id:
                update_data["resourceBankItemId"] = resource_bank_item_id

            if existing:
                updated = await db.embedding.update(where={"id": existing.id}, data=update_data)
                return updated.id
            else:
                create_data = {
                    "objectType": object_type,
                    "objectId": object_id,
                    **update_data,
                }
                record = await db.embedding.create(data=create_data)
                return record.id

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to update embedding")

    # ------------------------------------------------------------------
    # Search (Pinecone)
    # ------------------------------------------------------------------

    async def find_similar(
        self,
        query_text: str,
        object_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find similar embeddings using Pinecone vector search.

        Args:
            query_text: The query text to search for
            object_type: Optional filter by object type
            limit: Maximum number of results
            threshold: Minimum similarity threshold (0.0 to 1.0)
            metadata_filter: Optional additional Pinecone metadata filter dict

        Returns:
            List of similar objects with their similarity scores
        """
        idx = _index()
        if idx is None:
            logger.warning("Pinecone not configured – returning empty results")
            return []

        try:
            query_embedding = await self.generate_query_embedding(query_text)

            # Build Pinecone filter
            pc_filter: dict[str, Any] = {}
            if object_type:
                pc_filter["objectType"] = {"$eq": object_type}
            if metadata_filter:
                for k, v in metadata_filter.items():
                    pc_filter[k] = {"$eq": v}

            results = idx.query(
                vector=query_embedding,
                top_k=limit,
                include_metadata=True,
                filter=pc_filter if pc_filter else None,
            )

            similarities = []
            for match in results.get("matches", []):
                score = match.get("score", 0)
                if score < threshold:
                    continue

                meta = match.get("metadata", {})
                similarities.append(
                    {
                        "objectType": meta.get("objectType", ""),
                        "objectId": meta.get("objectId", ""),
                        "similarity": score,
                        "content": meta.get("content", ""),
                        "metadata": meta,
                    }
                )

            return similarities

        except Exception as e:
            logger.error(f"Error finding similar embeddings via Pinecone: {e}")
            return []

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete_embedding(self, object_type: str, object_id: str) -> None:
        """Delete an embedding from both Pinecone and Postgres."""
        try:
            idx = _index()
            if idx is not None:
                idx.delete(ids=[_make_vector_id(object_type, object_id)])

            await db.embedding.delete_many(where={"objectType": object_type, "objectId": object_id})
        except Exception as e:
            logger.error(f"Error deleting embedding: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete embedding")


# Global instance
embedding_service = EmbeddingService()
