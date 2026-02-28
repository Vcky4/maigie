"""
Resource Bank service for managing shared academic resources.

This module handles:
- File upload and storage
- AI moderation of uploaded content
- Text extraction and RAG indexing
- Credit rewards for approved uploads
- Semantic search across the resource bank

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import logging
import os
import warnings
from datetime import datetime, timedelta
from typing import Any

from fastapi import UploadFile
from prisma import Prisma
from prisma.models import User

from ..core.database import db
from ..services.embedding_service import embedding_service
from ..services.storage_service import storage_service
from ..services.text_extraction_service import extract_text_from_file_async

logger = logging.getLogger(__name__)

# Reward tokens for an approved resource upload
UPLOAD_REWARD_TOKENS = 1500

# Valid resource bank types (must match Prisma enum ResourceBankType)
VALID_RESOURCE_BANK_TYPES = {
    "TEXTBOOK",
    "NOTE",
    "ASSIGNMENT",
    "PAST_QUESTION",
    "SLIDES",
    "SUMMARY",
    "LAB_REPORT",
    "OTHER",
}

# Max files per upload
MAX_FILES_PER_UPLOAD = 10

# Max file size (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


async def upload_resource(
    user: User,
    title: str,
    university_name: str,
    files: list[UploadFile],
    description: str | None = None,
    resource_type: str = "OTHER",
    course_name: str | None = None,
    course_code: str | None = None,
    db_client: Prisma | None = None,
) -> dict:
    """
    Upload a resource to the resource bank.

    1. Creates the ResourceBankItem record with PENDING_REVIEW status.
    2. Uploads each file to BunnyCDN and creates ResourceBankFile records.

    Returns:
        Dictionary with the created resource bank item.
    """
    if db_client is None:
        db_client = db

    if resource_type not in VALID_RESOURCE_BANK_TYPES:
        resource_type = "OTHER"

    if len(files) > MAX_FILES_PER_UPLOAD:
        raise ValueError(f"Maximum {MAX_FILES_PER_UPLOAD} files allowed per upload")

    # Create the resource bank item
    item = await db_client.resourcebankitem.create(
        data={
            "uploaderId": user.id,
            "title": title,
            "description": description,
            "type": resource_type,
            "universityName": university_name,
            "courseName": course_name,
            "courseCode": course_code,
            "status": "PENDING_REVIEW",
        }
    )

    uploaded_files = []

    for file in files:
        try:
            # Upload to BunnyCDN
            upload_result = await storage_service.upload_file(file, path=f"resource-bank/{item.id}")

            # Create file record
            file_record = await db_client.resourcebankfile.create(
                data={
                    "resourceBankItemId": item.id,
                    "filename": upload_result["filename"],
                    "url": upload_result["url"],
                    "size": upload_result.get("size"),
                    "mimeType": file.content_type,
                }
            )
            uploaded_files.append(file_record)

        except Exception as e:
            logger.error(f"Failed to upload file {file.filename} for resource {item.id}: {e}")
            # Continue with remaining files

    logger.info(
        f"Resource bank item created: {item.id} by user {user.id} "
        f"({len(uploaded_files)}/{len(files)} files uploaded)"
    )

    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "type": item.type,
        "universityName": item.universityName,
        "courseName": item.courseName,
        "courseCode": item.courseCode,
        "status": str(item.status),
        "fileCount": len(uploaded_files),
        "createdAt": item.createdAt.isoformat(),
    }


async def moderate_resource(item_id: str, db_client: Prisma | None = None) -> dict:
    """
    AI-moderate a resource bank item using Gemini.

    Checks:
    1. Is it academic material?
    2. No inappropriate content?
    3. Files are readable / not corrupt?

    Auto-approves if all checks pass, rejects with reason otherwise.
    If approved, triggers text extraction, indexing, and reward creation.
    """
    if db_client is None:
        db_client = db

    item = await db_client.resourcebankitem.find_unique(
        where={"id": item_id},
        include={"files": True, "uploader": True},
    )

    if not item:
        logger.error(f"Resource bank item {item_id} not found for moderation")
        return {"status": "error", "reason": "Item not found"}

    # Build content summary for AI check
    content_parts = [f"Title: {item.title}"]
    if item.description:
        content_parts.append(f"Description: {item.description}")
    content_parts.append(f"Type: {item.type}")
    content_parts.append(f"University: {item.universityName}")
    if item.courseName:
        content_parts.append(f"Course: {item.courseName}")
    if item.courseCode:
        content_parts.append(f"Course Code: {item.courseCode}")

    # Extract text from uploaded files for content check
    extracted_texts = []
    for file_record in item.files:
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(file_record.url, timeout=30)
                if resp.status_code == 200:
                    text = await extract_text_from_file_async(
                        resp.content,
                        file_record.filename,
                        file_record.mimeType,
                    )
                    if text:
                        extracted_texts.append(text)
                        # Store extracted text for later indexing
                        await db_client.resourcebankfile.update(
                            where={"id": file_record.id},
                            data={"extractedText": text[:50000]},
                        )
        except Exception as e:
            logger.warning(f"Failed to extract text from file {file_record.id}: {e}")

    # Add a sample of extracted text for moderation
    for text in extracted_texts[:3]:
        content_parts.append(f"Extracted content sample: {text[:1000]}")

    content_for_review = "\n".join(content_parts)

    # AI moderation via Gemini
    moderation_result = await _ai_moderate_content(content_for_review)

    if moderation_result["approved"]:
        # Approve the resource
        await db_client.resourcebankitem.update(
            where={"id": item_id},
            data={
                "status": "APPROVED",
                "moderationNotes": moderation_result.get("notes", "Auto-approved by AI moderation"),
            },
        )

        # Index into RAG
        await _index_resource_bank_item(item_id, content_parts, extracted_texts, db_client)

        # Create upload reward
        await create_upload_reward(item.uploaderId, item_id, db_client)

        logger.info(f"Resource bank item {item_id} APPROVED")
        return {"status": "approved", "notes": moderation_result.get("notes")}
    else:
        # Reject the resource
        await db_client.resourcebankitem.update(
            where={"id": item_id},
            data={
                "status": "REJECTED",
                "moderationNotes": moderation_result.get("reason", "Rejected by AI moderation"),
            },
        )
        logger.info(f"Resource bank item {item_id} REJECTED: {moderation_result.get('reason')}")
        return {"status": "rejected", "reason": moderation_result.get("reason")}


async def _ai_moderate_content(content: str) -> dict:
    """
    Use Gemini to moderate content for the resource bank.

    Returns:
        dict with 'approved' (bool), 'notes' (str), and optionally 'reason' (str) if rejected.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        prompt = f"""You are a content moderator for an academic resource bank used by university students.
Review the following uploaded resource and determine if it should be APPROVED or REJECTED.

APPROVE if:
- It is clearly academic material (notes, past questions, textbooks, assignments, slides, summaries, lab reports)
- The content is educational and relevant to university studies
- The file content is readable and not corrupted

REJECT if:
- It contains inappropriate, offensive, or harmful content
- It is spam, advertising, or unrelated to academics
- It appears to be copyrighted material from a commercial publisher (full textbook scans)
- The content is empty, unreadable, or corrupted

Respond with EXACTLY this JSON format (no markdown, no extra text):
{{"approved": true/false, "notes": "brief explanation", "reason": "rejection reason if rejected, empty string otherwise"}}

Resource to review:
{content[:3000]}"""

        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash", contents=prompt
        )

        if response.text:
            import json

            # Try to parse JSON response
            text = response.text.strip()
            # Remove markdown code fence if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            result = json.loads(text)
            return {
                "approved": bool(result.get("approved", False)),
                "notes": result.get("notes", ""),
                "reason": result.get("reason", ""),
            }

    except Exception as e:
        logger.error(f"AI moderation failed: {e}")

    # Default: approve if AI moderation fails (fail open for now)
    return {"approved": True, "notes": "Auto-approved (moderation service unavailable)"}


async def _index_resource_bank_item(
    item_id: str,
    content_parts: list[str],
    extracted_texts: list[str],
    db_client: Prisma | None = None,
) -> None:
    """Index a resource bank item into Pinecone + Postgres via the embedding service."""
    if db_client is None:
        db_client = db

    try:
        full_content_parts = list(content_parts)
        for text in extracted_texts:
            full_content_parts.append(text[:2000])

        content = " ".join(full_content_parts)

        item = await db_client.resourcebankitem.find_unique(where={"id": item_id})
        if not item:
            return

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

        logger.info(f"Indexed resource bank item: {item_id}")

    except Exception as e:
        logger.error(f"Error indexing resource bank item {item_id}: {e}")


async def create_upload_reward(
    uploader_id: str, item_id: str, db_client: Prisma | None = None
) -> None:
    """Create a claimable reward for an approved resource upload."""
    if db_client is None:
        db_client = db

    # Check if reward already exists
    existing = await db_client.resourceuploadreward.find_first(
        where={"uploaderId": uploader_id, "resourceBankItemId": item_id}
    )

    if existing:
        logger.info(f"Upload reward already exists for item {item_id}")
        return

    await db_client.resourceuploadreward.create(
        data={
            "uploaderId": uploader_id,
            "resourceBankItemId": item_id,
            "tokens": UPLOAD_REWARD_TOKENS,
            "isClaimed": False,
        }
    )

    logger.info(f"Created upload reward ({UPLOAD_REWARD_TOKENS} tokens) for user {uploader_id}")


async def claim_upload_reward(user: User, reward_id: str, db_client: Prisma | None = None) -> dict:
    """
    Claim an upload reward. Increases the user's daily credit limit.
    Follows the same pattern as claim_referral_reward.
    """
    if db_client is None:
        db_client = db

    reward = await db_client.resourceuploadreward.find_unique(where={"id": reward_id})
    if not reward:
        raise ValueError("Reward not found")

    if reward.uploaderId != user.id:
        raise ValueError("Reward does not belong to this user")

    if reward.isClaimed:
        raise ValueError("Reward already claimed")

    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Check if already claimed today
    existing_claim = await db_client.resourceuploadrewardclaim.find_first(
        where={
            "userId": user.id,
            "claimDate": {"gte": today_midnight},
            "rewardId": reward_id,
        }
    )

    if existing_claim:
        raise ValueError("This reward has already been claimed today")

    # Mark reward as claimed
    await db_client.resourceuploadreward.update(
        where={"id": reward_id},
        data={
            "isClaimed": True,
            "claimedAt": now,
            "claimDate": today_midnight,
        },
    )

    # Create claim record
    await db_client.resourceuploadrewardclaim.create(
        data={
            "userId": user.id,
            "rewardId": reward_id,
            "tokensClaimed": reward.tokens,
            "claimDate": today_midnight,
            "dailyLimitIncrease": reward.tokens,
        }
    )

    logger.info(f"User {user.id} claimed upload reward {reward_id}: {reward.tokens} tokens")

    return {
        "rewardId": reward_id,
        "tokensClaimed": reward.tokens,
        "claimDate": today_midnight.isoformat(),
        "dailyLimitIncrease": reward.tokens,
    }


async def get_claimable_upload_rewards(user: User, db_client: Prisma | None = None) -> list[dict]:
    """Get all unclaimed upload rewards for a user."""
    if db_client is None:
        db_client = db

    rewards = await db_client.resourceuploadreward.find_many(
        where={"uploaderId": user.id, "isClaimed": False},
        include={"resourceBankItem": True},
        order={"createdAt": "desc"},
    )

    return [
        {
            "id": reward.id,
            "resourceBankItemId": reward.resourceBankItemId,
            "resourceTitle": reward.resourceBankItem.title if reward.resourceBankItem else None,
            "tokens": reward.tokens,
            "isClaimed": reward.isClaimed,
            "claimedAt": reward.claimedAt.isoformat() if reward.claimedAt else None,
            "createdAt": reward.createdAt.isoformat() if reward.createdAt else None,
        }
        for reward in rewards
    ]


async def get_upload_daily_limit_increase(user: User, db_client: Prisma | None = None) -> int:
    """Get total daily limit increase from upload rewards claimed today."""
    if db_client is None:
        db_client = db

    now = datetime.utcnow()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    claims = await db_client.resourceuploadrewardclaim.find_many(
        where={
            "userId": user.id,
            "claimDate": {"gte": today_midnight},
        }
    )

    return sum(claim.dailyLimitIncrease for claim in claims)


async def search_resource_bank(
    query: str,
    university_name: str | None = None,
    limit: int = 10,
    threshold: float = 0.6,
    db_client: Prisma | None = None,
) -> list[dict]:
    """
    Semantic search across the resource bank via Pinecone.

    Args:
        query: Search query text
        university_name: Filter by university
        limit: Max results
        threshold: Minimum similarity threshold
    """
    try:
        # Build Pinecone metadata filter for university scoping
        metadata_filter: dict[str, Any] | None = None
        if university_name:
            metadata_filter = {"universityName": university_name}

        results = await embedding_service.find_similar(
            query_text=query,
            object_type="resource_bank_item",
            limit=limit,
            threshold=threshold,
            metadata_filter=metadata_filter,
        )

        # Map to expected format
        return [
            {
                "resourceBankItemId": r["objectId"],
                "similarity": r["similarity"],
                "content": r.get("content", ""),
                "metadata": r.get("metadata", {}),
            }
            for r in results
        ]

    except Exception as e:
        logger.error(f"Error searching resource bank: {e}")
        return []


async def browse_resource_bank(
    university_name: str | None = None,
    course_code: str | None = None,
    resource_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "createdAt",
    sort_order: str = "desc",
    db_client: Prisma | None = None,
) -> dict:
    """Browse/search approved resource bank items with filters and pagination."""
    if db_client is None:
        db_client = db

    where_clause: dict[str, Any] = {"status": "APPROVED"}

    if university_name:
        where_clause["universityName"] = {"contains": university_name, "mode": "insensitive"}

    if course_code:
        where_clause["courseCode"] = {"contains": course_code, "mode": "insensitive"}

    if resource_type and resource_type in VALID_RESOURCE_BANK_TYPES:
        where_clause["type"] = resource_type

    if search:
        where_clause["OR"] = [
            {"title": {"contains": search, "mode": "insensitive"}},
            {"description": {"contains": search, "mode": "insensitive"}},
            {"courseName": {"contains": search, "mode": "insensitive"}},
            {"courseCode": {"contains": search, "mode": "insensitive"}},
        ]

    skip = (page - 1) * page_size

    total = await db_client.resourcebankitem.count(where=where_clause)

    items = await db_client.resourcebankitem.find_many(
        where=where_clause,
        order={sort_by: sort_order},
        skip=skip,
        take=page_size,
        include={"uploader": True, "files": True},
    )

    has_more = (skip + page_size) < total

    return {
        "items": [
            {
                "id": item.id,
                "uploaderName": item.uploader.name if item.uploader else None,
                "title": item.title,
                "description": item.description,
                "type": str(item.type),
                "universityName": item.universityName,
                "courseName": item.courseName,
                "courseCode": item.courseCode,
                "downloadCount": item.downloadCount,
                "viewCount": item.viewCount,
                "fileCount": len(item.files) if item.files else 0,
                "createdAt": item.createdAt.isoformat(),
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "hasMore": has_more,
    }


async def get_resource_bank_detail(item_id: str, db_client: Prisma | None = None) -> dict | None:
    """Get full details of a resource bank item including files."""
    if db_client is None:
        db_client = db

    item = await db_client.resourcebankitem.find_unique(
        where={"id": item_id},
        include={"uploader": True, "files": True},
    )

    if not item:
        return None

    # Increment view count
    await db_client.resourcebankitem.update(
        where={"id": item_id},
        data={"viewCount": {"increment": 1}},
    )

    return {
        "id": item.id,
        "uploaderId": item.uploaderId,
        "uploaderName": item.uploader.name if item.uploader else None,
        "title": item.title,
        "description": item.description,
        "type": str(item.type),
        "universityName": item.universityName,
        "courseName": item.courseName,
        "courseCode": item.courseCode,
        "status": str(item.status),
        "downloadCount": item.downloadCount,
        "viewCount": item.viewCount + 1,
        "fileCount": len(item.files) if item.files else 0,
        "files": [
            {
                "id": f.id,
                "filename": f.filename,
                "url": f.url,
                "size": f.size,
                "mimeType": f.mimeType,
                "createdAt": f.createdAt.isoformat(),
            }
            for f in (item.files or [])
        ],
        "createdAt": item.createdAt.isoformat(),
        "updatedAt": item.updatedAt.isoformat(),
    }


async def download_file(item_id: str, file_id: str, db_client: Prisma | None = None) -> dict | None:
    """Get file download URL and increment download count."""
    if db_client is None:
        db_client = db

    file_record = await db_client.resourcebankfile.find_first(
        where={"id": file_id, "resourceBankItemId": item_id}
    )

    if not file_record:
        return None

    # Increment download count on the parent item
    await db_client.resourcebankitem.update(
        where={"id": item_id},
        data={"downloadCount": {"increment": 1}},
    )

    return {
        "filename": file_record.filename,
        "url": file_record.url,
        "size": file_record.size,
        "mimeType": file_record.mimeType,
    }


async def report_resource(
    reporter_id: str,
    item_id: str,
    reason: str,
    description: str | None = None,
    db_client: Prisma | None = None,
) -> dict:
    """Report a resource bank item."""
    if db_client is None:
        db_client = db

    # Check if already reported by this user
    existing = await db_client.resourcebankreport.find_first(
        where={"resourceBankItemId": item_id, "reporterId": reporter_id}
    )

    if existing:
        raise ValueError("You have already reported this resource")

    report = await db_client.resourcebankreport.create(
        data={
            "resourceBankItemId": item_id,
            "reporterId": reporter_id,
            "reason": reason,
            "description": description,
            "status": "REPORT_PENDING",
        }
    )

    # Increment report count
    updated_item = await db_client.resourcebankitem.update(
        where={"id": item_id},
        data={"reportCount": {"increment": 1}},
    )

    # Auto-flag if report count reaches threshold
    if updated_item.reportCount >= 3:
        await db_client.resourcebankitem.update(
            where={"id": item_id},
            data={"status": "FLAGGED"},
        )
        logger.warning(
            f"Resource bank item {item_id} auto-flagged due to {updated_item.reportCount} reports"
        )

    return {
        "id": report.id,
        "resourceBankItemId": item_id,
        "reason": reason,
        "status": str(report.status),
        "createdAt": report.createdAt.isoformat(),
    }


async def get_my_uploads(user: User, db_client: Prisma | None = None) -> list[dict]:
    """Get all resource bank items uploaded by the user."""
    if db_client is None:
        db_client = db

    items = await db_client.resourcebankitem.find_many(
        where={"uploaderId": user.id},
        order={"createdAt": "desc"},
        include={"files": True},
    )

    return [
        {
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "type": str(item.type),
            "universityName": item.universityName,
            "courseName": item.courseName,
            "courseCode": item.courseCode,
            "status": str(item.status),
            "moderationNotes": item.moderationNotes,
            "downloadCount": item.downloadCount,
            "viewCount": item.viewCount,
            "fileCount": len(item.files) if item.files else 0,
            "createdAt": item.createdAt.isoformat(),
        }
        for item in items
    ]
