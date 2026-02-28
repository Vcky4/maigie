"""
Knowledge Base Service.

Indexes user-uploaded images into a personal knowledge base for RAG context.
Extracts text from images via Gemini Vision OCR, stores UserUpload records,
and creates embeddings in Pinecone for semantic search.

Each user has a tier-based upload limit. When the limit is reached, the oldest
upload is automatically removed to make room for new ones (FIFO eviction).
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx

from src.core.database import db

logger = logging.getLogger(__name__)

# Tier-based upload limits
UPLOAD_LIMITS = {
    "FREE": 20,
    "PREMIUM_MONTHLY": 100,
    "PREMIUM_YEARLY": 100,
    "STUDY_CIRCLE_MONTHLY": 100,
    "STUDY_CIRCLE_YEARLY": 100,
    "SQUAD_MONTHLY": 100,
    "SQUAD_YEARLY": 100,
}

DEFAULT_LIMIT = 20


def _get_upload_limit(tier: str | None) -> int:
    """Return the upload limit for a given tier."""
    return UPLOAD_LIMITS.get(tier or "FREE", DEFAULT_LIMIT)


async def get_upload_stats(user_id: str) -> dict:
    """Return upload usage stats: {used, limit}."""
    user = await db.user.find_unique(where={"id": user_id})
    tier = getattr(user, "tier", "FREE") if user else "FREE"
    limit = _get_upload_limit(tier)
    used = await db.userupload.count(where={"userId": user_id})
    return {"used": used, "limit": limit}


async def get_user_uploads(user_id: str) -> list[dict]:
    """List all knowledge base uploads for a user."""
    uploads = await db.userupload.find_many(
        where={"userId": user_id},
        order={"createdAt": "desc"},
    )
    return [
        {
            "id": u.id,
            "url": u.url,
            "filename": u.filename,
            "mimeType": getattr(u, "mimeType", None),
            "size": getattr(u, "size", None),
            "hasText": bool(getattr(u, "extractedText", None)),
            "createdAt": (
                u.createdAt.isoformat() if hasattr(u.createdAt, "isoformat") else str(u.createdAt)
            ),
        }
        for u in uploads
    ]


async def delete_user_upload(user_id: str, upload_id: str) -> bool:
    """
    Delete a specific upload: remove from DB, Pinecone, and CDN.
    Returns True if deleted, False if not found.
    """
    upload = await db.userupload.find_first(where={"id": upload_id, "userId": user_id})
    if not upload:
        return False

    # Delete embedding from Pinecone
    if upload.embeddingId:
        try:
            from src.services.embedding_service import embedding_service

            await embedding_service.delete_embedding("user_upload", upload.id)
        except Exception as e:
            logger.warning("Failed to delete embedding for upload %s: %s", upload_id, e)

    # Delete file from CDN
    try:
        from src.services.storage_service import storage_service

        await storage_service.delete_file(upload.url)
    except Exception as e:
        logger.warning("Failed to delete CDN file for upload %s: %s", upload_id, e)

    # Delete DB record
    await db.userupload.delete(where={"id": upload_id})
    logger.info("Deleted knowledge base upload %s for user %s", upload_id, user_id)
    return True


async def _evict_oldest_if_needed(user_id: str, tier: str | None) -> None:
    """If user is at or over the upload limit, evict the oldest upload."""
    limit = _get_upload_limit(tier)
    count = await db.userupload.count(where={"userId": user_id})

    while count >= limit:
        oldest = await db.userupload.find_first(
            where={"userId": user_id},
            order={"createdAt": "asc"},
        )
        if not oldest:
            break
        await delete_user_upload(user_id, oldest.id)
        count -= 1
        logger.info(
            "Evicted oldest upload %s for user %s (was at %d/%d)",
            oldest.id,
            user_id,
            count + 1,
            limit,
        )


async def index_user_uploads(
    user_id: str,
    image_urls: list[str],
    chat_message_id: str | None = None,
) -> list[str]:
    """
    Index uploaded images into the user's knowledge base.

    For each image URL:
    1. Download the image bytes
    2. Extract text via Gemini Vision OCR
    3. Create a UserUpload record
    4. Generate and store an embedding in Pinecone

    Returns a list of created UserUpload IDs.
    """
    if not image_urls:
        return []

    # Get user tier for limit checks
    user = await db.user.find_unique(where={"id": user_id})
    tier = getattr(user, "tier", "FREE") if user else "FREE"

    upload_ids = []

    for url in image_urls:
        try:
            # Evict oldest if at limit
            await _evict_oldest_if_needed(user_id, tier)

            # 1. Download image bytes
            filename = Path(url.split("/")[-1].split("?")[0]).name or "upload.jpg"
            mime_type = _guess_mime_type(filename)
            file_content = await _download_file(url)
            if not file_content:
                logger.warning("Failed to download image from %s", url)
                continue

            file_size = len(file_content)

            # 2. Extract text via OCR
            extracted_text = None
            try:
                from src.services.text_extraction_service import (
                    extract_text_from_file_async,
                )

                extracted_text = await extract_text_from_file_async(
                    file_content, filename, mime_type
                )
            except Exception as e:
                logger.warning("Text extraction failed for %s: %s", url, e)

            # 3. Create UserUpload record
            upload = await db.userupload.create(
                data={
                    "userId": user_id,
                    "url": url,
                    "filename": filename,
                    "mimeType": mime_type,
                    "size": file_size,
                    "extractedText": extracted_text,
                    "chatMessageId": chat_message_id,
                }
            )

            # 4. Index embedding if we got text
            if extracted_text and extracted_text.strip():
                try:
                    from src.services.embedding_service import embedding_service

                    embedding_id = await embedding_service.update_embedding(
                        object_type="user_upload",
                        object_id=upload.id,
                        content=extracted_text[:8000],  # Limit for embedding
                        metadata={
                            "userId": user_id,
                            "filename": filename,
                            "uploadId": upload.id,
                        },
                    )
                    # Update the upload with the embedding ID
                    if embedding_id:
                        await db.userupload.update(
                            where={"id": upload.id},
                            data={"embeddingId": str(embedding_id)},
                        )
                except Exception as e:
                    logger.warning("Embedding indexing failed for upload %s: %s", upload.id, e)

            upload_ids.append(upload.id)
            logger.info(
                "Indexed upload %s (%s) for user %s — text: %s chars",
                upload.id,
                filename,
                user_id,
                len(extracted_text) if extracted_text else 0,
            )

        except Exception as e:
            logger.error("Failed to index upload from %s: %s", url, e, exc_info=True)

    return upload_ids


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename extension."""
    ext = Path(filename).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


async def _download_file(url: str) -> bytes | None:
    """Download file bytes from a URL."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.content
            logger.warning("Download failed for %s: HTTP %d", url, response.status_code)
            return None
    except Exception as e:
        logger.warning("Download failed for %s: %s", url, e)
        return None
