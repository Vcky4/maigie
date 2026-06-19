"""
Blog Autopilot Service.

Handles the full pipeline for AI-generated blog posts:
1. Generate content from a content calendar entry (topic + keywords)
2. Fetch a cover image from Google Drive
3. Upload the image to Bunny CDN
4. Save the blog post to the database
5. Push the .mdoc file to the maigie-public Astro repo

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import get_settings
from src.core.database import db

logger = logging.getLogger(__name__)


# ─── 1. AI Content Generation ────────────────────────────────────────────────


async def generate_blog_content(
    topic: str,
    keywords: list[str],
    category: str,
) -> dict[str, Any]:
    """
    Use AI to generate a full blog post given a topic and keywords.

    Returns: {title, slug, description, content, tags, readTime}
    """
    settings = get_settings()

    prompt = f"""Write a high-quality blog post for Maigie, an AI-powered study platform for students.

TOPIC: {topic}
KEYWORDS: {', '.join(keywords)}
CATEGORY: {category}

REQUIREMENTS:
- Title: Compelling, SEO-friendly, 50-70 characters
- Description: 1-2 sentence meta description for SEO (max 160 chars)
- Content: 1200-1800 words, well-structured with ## headers, practical advice
- Tone: Friendly, authoritative, relatable to students
- Include actionable tips, examples, and how Maigie helps (subtle, not salesy)
- Use Markdoc format (standard markdown with --- for horizontal rules)
- Tags: 5-7 relevant tags

Return JSON with these exact fields:
{{
  "title": "...",
  "slug": "...",
  "description": "...",
  "content": "... (the full blog body in markdown, NO frontmatter)",
  "tags": ["tag1", "tag2", ...],
  "readTime": 7
}}

Return ONLY valid JSON, no markdown fences or other text."""

    response_text = ""

    # Try Gemini first
    try:
        from src.services.llm import new_gemini_client

        client = new_gemini_client(settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
        )
        response_text = response.text or ""
    except Exception as gemini_err:
        logger.warning("Gemini failed for blog generation, trying OpenAI: %s", gemini_err)

        if settings.OPENAI_API_KEY:
            try:
                import openai

                openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                completion = await openai_client.chat.completions.create(
                    model=settings.OPENAI_DEFAULT_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                )
                response_text = completion.choices[0].message.content or ""
            except Exception as openai_err:
                raise RuntimeError(f"Both LLMs failed: Gemini={gemini_err}, OpenAI={openai_err}")
        else:
            raise RuntimeError(f"Gemini failed and no OpenAI key: {gemini_err}")

    # Parse JSON response
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse AI blog response: {e}\nRaw: {response_text[:500]}")

    # Sanitize slug
    slug = result.get("slug", "")
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    result["slug"] = slug

    return result


# ─── 2. Google Drive Image Fetch ─────────────────────────────────────────────


async def fetch_cover_image_from_drive(
    used_image_ids: list[str] | None = None,
) -> tuple[bytes, str, str] | None:
    """
    Fetch an unused image from the configured Google Drive folder.

    Returns: (image_bytes, filename, drive_file_id) or None if no images available.
    """
    settings = get_settings()

    if (
        not settings.BLOG_GOOGLE_DRIVE_FOLDER_ID
        or not settings.BLOG_GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON
    ):
        logger.warning("Google Drive not configured for blog images")
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_info = json.loads(settings.BLOG_GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=credentials)

        # List image files in the folder
        query = (
            f"'{settings.BLOG_GOOGLE_DRIVE_FOLDER_ID}' in parents "
            f"and mimeType contains 'image/' "
            f"and trashed = false"
        )
        results = (
            service.files()
            .list(
                q=query,
                fields="files(id, name, mimeType)",
                pageSize=50,
                orderBy="createdTime desc",
            )
            .execute()
        )

        files = results.get("files", [])
        if not files:
            logger.info("No images found in Drive folder")
            return None

        # Pick first unused image
        used_ids = set(used_image_ids or [])
        target_file = None
        for f in files:
            if f["id"] not in used_ids:
                target_file = f
                break

        if not target_file:
            # All used — pick the first one (recycle)
            target_file = files[0]

        # Download the file
        file_content = service.files().get_media(fileId=target_file["id"]).execute()

        return file_content, target_file["name"], target_file["id"]

    except Exception as e:
        logger.error("Failed to fetch image from Drive: %s", e, exc_info=True)
        return None


# ─── 3. Upload to Bunny CDN ──────────────────────────────────────────────────


async def upload_blog_image_to_bunny(
    image_bytes: bytes,
    slug: str,
    original_filename: str,
) -> str | None:
    """
    Upload blog cover image to Bunny CDN.

    Returns the public CDN URL or None on failure.
    """
    settings = get_settings()

    if not settings.BUNNY_CDN_API_KEY or not settings.BUNNY_STORAGE_ZONE:
        logger.warning("Bunny CDN not configured")
        return None

    # Determine extension from original filename
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "jpeg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = "jpeg"

    filename = f"{slug}.{ext}"
    upload_path = f"blog-image/{filename}"
    base_url = f"https://uk.storage.bunnycdn.com/{settings.BUNNY_STORAGE_ZONE}"
    upload_url = f"{base_url}/{upload_path}"

    headers = {
        "AccessKey": settings.BUNNY_CDN_API_KEY,
        "Content-Type": "application/octet-stream",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(upload_url, headers=headers, content=image_bytes)
            if response.status_code != 201:
                logger.error("Bunny upload failed: %s", response.text)
                return None

        # Build public URL
        raw_base = (settings.BUNNY_PUBLIC_URL_BASE or "").strip().rstrip("/")
        if raw_base:
            return f"{raw_base}/{upload_path}"
        return f"https://{settings.BUNNY_CDN_HOSTNAME}/{upload_path}"

    except Exception as e:
        logger.error("Failed to upload blog image to Bunny: %s", e)
        return None


# ─── 4. Save to Database ─────────────────────────────────────────────────────


async def save_blog_post_to_db(
    slug: str,
    title: str,
    description: str,
    content: str,
    category: str,
    tags: list[str],
    cover_image_url: str | None,
    read_time: int,
    publish: bool = True,
) -> str:
    """
    Create a BlogPost record in the database.

    Returns the BlogPost ID.
    """
    settings = get_settings()

    # Check if slug already exists
    existing = await db.blogpost.find_first(where={"slug": slug})
    if existing:
        # Append a number to make unique
        slug = f"{slug}-{datetime.now(UTC).strftime('%Y%m%d')}"

    post = await db.blogpost.create(
        data={
            "slug": slug,
            "title": title,
            "description": description,
            "content": content,
            "category": category,
            "tags": tags,
            "coverImage": cover_image_url,
            "readTime": read_time,
            "authorName": settings.BLOG_DEFAULT_AUTHOR_NAME,
            "authorRole": settings.BLOG_DEFAULT_AUTHOR_ROLE,
            "publishedAt": datetime.now(UTC),
            "published": publish,
            "featured": False,
        }
    )

    return post.id


# ─── 5. Push to Astro Repo ───────────────────────────────────────────────────


async def push_to_astro_repo(
    slug: str,
    title: str,
    description: str,
    content: str,
    category: str,
    tags: list[str],
    cover_image_url: str | None,
    read_time: int,
) -> bool:
    """
    Push a .mdoc file to the maigie-public GitHub repo via the GitHub API.

    Uses the Contents API to create/update the file directly (no cloning needed).
    Returns True on success.
    """
    settings = get_settings()

    if not settings.BLOG_GITHUB_TOKEN:
        logger.warning("GitHub token not configured — skipping Astro push")
        return False

    # Build frontmatter
    tags_yaml = "\n".join(f"  - {tag}" for tag in tags)
    published_date = datetime.now(UTC).strftime("%Y-%m-%d")

    frontmatter = f"""---
title: '{title.replace("'", "''")}'
description: {description}
publishedAt: '{published_date}'
category: {category}
tags:
{tags_yaml}
coverImage: '{cover_image_url or ''}'
readTime: {read_time}
featured: false
authorName: {settings.BLOG_DEFAULT_AUTHOR_NAME}
authorRole: {settings.BLOG_DEFAULT_AUTHOR_ROLE}
---"""

    file_content = f"{frontmatter}\n\n{content}\n"
    file_path = f"src/content/posts/{slug}.mdoc"

    # GitHub Contents API
    url = f"https://api.github.com/repos/{settings.BLOG_GITHUB_REPO}/contents/{file_path}"
    headers = {
        "Authorization": f"token {settings.BLOG_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Check if file already exists (need sha for update)
    sha = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            check_resp = await client.get(url, headers=headers)
            if check_resp.status_code == 200:
                sha = check_resp.json().get("sha")
    except Exception:
        pass

    # Create or update file
    payload: dict[str, Any] = {
        "message": f"blog: add '{title}' [auto-generated]",
        "content": base64.b64encode(file_content.encode()).decode(),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info("Pushed blog post to Astro repo: %s", file_path)
                return True
            else:
                logger.error("GitHub push failed: %s %s", resp.status_code, resp.text[:300])
                return False
    except Exception as e:
        logger.error("Failed to push to GitHub: %s", e)
        return False


# ─── Full Pipeline ────────────────────────────────────────────────────────────


async def process_calendar_entry(entry_id: str) -> dict[str, Any]:
    """
    Process a single content calendar entry through the full pipeline.

    Returns a result dict with status and details.
    """
    entry = await db.contentcalendarentry.find_unique(where={"id": entry_id})
    if not entry:
        return {"status": "error", "message": "Entry not found"}

    if entry.status == "published":
        return {"status": "skipped", "message": "Already published"}

    # Mark as generating
    await db.contentcalendarentry.update(
        where={"id": entry_id},
        data={"status": "generating"},
    )

    try:
        # 1. Generate content
        logger.info("Generating blog content for: %s", entry.topic)
        blog_data = await generate_blog_content(
            topic=entry.topic,
            keywords=entry.keywords or [],
            category=entry.category or "Study Tips",
        )

        slug = blog_data["slug"]
        title = blog_data["title"]
        description = blog_data["description"]
        content = blog_data["content"]
        tags = blog_data.get("tags", entry.keywords or [])
        read_time = blog_data.get("readTime", 7)

        # 2. Use cover image from the calendar entry (uploaded by admin)
        cover_image_url = entry.coverImageUrl

        # 4. Save to DB
        should_publish = entry.autoPublish
        blog_post_id = await save_blog_post_to_db(
            slug=slug,
            title=title,
            description=description,
            content=content,
            category=entry.category or "Study Tips",
            tags=tags,
            cover_image_url=cover_image_url,
            read_time=read_time,
            publish=should_publish,
        )

        # 5. Push to Astro repo (only if auto-publish)
        pushed = False
        if should_publish:
            pushed = await push_to_astro_repo(
                slug=slug,
                title=title,
                description=description,
                content=content,
                category=entry.category or "Study Tips",
                tags=tags,
                cover_image_url=cover_image_url,
                read_time=read_time,
            )

        # Update calendar entry
        await db.contentcalendarentry.update(
            where={"id": entry_id},
            data={
                "status": "published" if should_publish else "draft",
                "blogPostId": blog_post_id,
            },
        )

        return {
            "status": "success",
            "blogPostId": blog_post_id,
            "slug": slug,
            "title": title,
            "pushed": pushed,
            "coverImage": cover_image_url,
        }

    except Exception as e:
        logger.error("Blog autopilot failed for entry %s: %s", entry_id, e, exc_info=True)
        await db.contentcalendarentry.update(
            where={"id": entry_id},
            data={"status": "failed", "errorMessage": str(e)[:500]},
        )
        return {"status": "error", "message": str(e)}


async def run_blog_autopilot() -> dict[str, Any]:
    """
    Check for scheduled calendar entries due today and process them.

    Called by the Celery beat task.
    """
    settings = get_settings()
    if not settings.BLOG_AUTOPILOT_ENABLED:
        return {"skipped": True, "reason": "Blog autopilot disabled"}

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start.replace(hour=23, minute=59, second=59)

    # Find entries scheduled for today that haven't been processed
    due_entries = await db.contentcalendarentry.find_many(
        where={
            "status": "scheduled",
            "scheduledDate": {"gte": today_start, "lte": today_end},
        },
        order={"scheduledDate": "asc"},
    )

    if not due_entries:
        return {"processed": 0, "message": "No entries due today"}

    results = []
    for entry in due_entries:
        result = await process_calendar_entry(entry.id)
        results.append({"entryId": entry.id, "topic": entry.topic, **result})

    return {"processed": len(results), "results": results}
