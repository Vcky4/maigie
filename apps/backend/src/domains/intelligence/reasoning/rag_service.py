"""Retrieval-Augmented Generation — not implemented.

There is no vector store behind this. Three half-started attempts existed and none
worked:

- a Pinecone dependency with an empty integration package and no API key, now
  removed;
- an `Embedding` table in the knowledge domain whose `vector` column is JSON,
  which cannot be searched by similarity in SQL, and which nothing ever wrote to;
- this service, which returned empty results under one set of method names while
  the only caller used a different name.

That last point mattered: `websocket_handler` calls `retrieve_relevant_context`,
which did not exist here, so every substantive chat message raised
`AttributeError` inside a `try` and printed "RAG context retrieval failed". The
feature was not merely absent, it was reporting a failure on a method that was
never written.

So this class now exposes the name the codebase actually calls and returns empty
results **deliberately and quietly**. Retrieval that finds nothing is the honest
description of a system with nothing to retrieve from.

When RAG is built, the intended home is `pgvector` in the existing Postgres rather
than a second datastore: embeddings and their source rows can then be written in
one transaction, so the index cannot claim a note exists after it has been
deleted.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RagService:
    """Placeholder with the real call signature. Returns nothing, by design."""

    #: Flipped when a retrieval backend exists. Callers may check this instead of
    #: inferring capability from an empty result.
    available: bool = False

    async def retrieve_relevant_context(
        self, *, query: str, user_id: str, limit: int = 3, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Retrieve context for a query.

        Always empty until a vector store exists. Logged at debug rather than
        warning: an unimplemented feature is not a runtime fault, and the previous
        warning trained everyone to ignore the log.
        """
        logger.debug("RAG retrieval skipped: no vector store configured")
        return []

    async def search(
        self, query: str, user_id: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Alias kept for older call sites."""
        return await self.retrieve_relevant_context(query=query, user_id=user_id, **kwargs)

    async def get_context(self, query: str, user_id: str, **kwargs: Any) -> str:
        """Flattened context string. Empty until retrieval exists."""
        return ""


rag_service = RagService()
