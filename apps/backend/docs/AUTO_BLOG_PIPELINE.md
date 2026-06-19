# Auto Blog Pipeline — Design Doc

## Overview

Automated blog publishing pipeline that:
1. Takes a content calendar (managed in admin dashboard)
2. Uses AI to generate blog posts when scheduled
3. Fetches cover images from a Google Drive folder
4. Uploads images to Bunny CDN
5. Creates `.mdoc` file and pushes to `maigie-public` repo (triggers auto-deploy)
6. Supports toggle between auto-publish and draft-for-review

## Architecture

```
Admin Dashboard (Content Calendar)
  ↓ schedules entries with topic, target date, keywords
  
Celery Beat Task (daily check)
  ↓ finds calendar entries due today
  
blog_autopilot_service.py
  ├── 1. generate_blog_content(topic, keywords) → title, body, meta
  │     Uses Gemini/OpenAI to write ~1500 word blog post
  ├── 2. fetch_cover_image_from_drive(folder_id) → image bytes
  │     Uses Google Drive API (service account) to pick an unused image
  ├── 3. upload_to_bunny(image_bytes, filename) → CDN URL
  │     Uses existing StorageService pattern
  ├── 4. save_to_db(post_data) → BlogPost record
  │     Creates record with published=autoPost setting
  └── 5. push_to_astro_repo(slug, frontmatter, content) → commit
        Clones/pulls maigie-public, creates .mdoc, pushes to main
```

## Data Model

### ContentCalendarEntry (new Prisma model)
```prisma
model ContentCalendarEntry {
  id            String   @id @default(uuid())
  topic         String          // "How to use spaced repetition effectively"
  keywords      String[]        // ["spaced repetition", "study techniques"]
  category      String          // "Learning Strategies"
  scheduledDate DateTime        // When to publish
  status        String   @default("scheduled") // scheduled, generating, published, failed
  blogPostId    String?         // Reference to created BlogPost
  driveImageId  String?         // Google Drive file ID used (to avoid reuse)
  notes         String?         // Optional admin notes
  autoPublish   Boolean  @default(true) // Auto-publish or create as draft
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt

  @@index([status, scheduledDate])
}
```

## Config (settings.py additions)
```python
# --- Auto Blog Pipeline ---
BLOG_GOOGLE_DRIVE_FOLDER_ID: str = ""        # Folder containing cover images
BLOG_GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: str = ""  # Service account JSON
BLOG_AUTOPILOT_ENABLED: bool = True          # Master switch
BLOG_GITHUB_TOKEN: str = ""                   # PAT for pushing to maigie-public
BLOG_GITHUB_REPO: str = "Maigie-Ltd/maigie-public"
BLOG_DEFAULT_AUTHOR_NAME: str = "Maigie Team"
BLOG_DEFAULT_AUTHOR_ROLE: str = "Learning Science"
```

## Key Behaviors

- **Content Calendar**: Admin uploads entries (topic + date + keywords). Can batch-create.
- **Auto-post toggle**: Per-entry and global. When off, creates BlogPost with `published=false` and skips git push.
- **Image selection**: Picks first unused image from Drive folder (tracks used IDs in ContentCalendarEntry.driveImageId).
- **Idempotent**: If entry already has blogPostId, skip it.
- **Error handling**: On failure, sets status to "failed" with error details for admin visibility.

## File Output Format (maigie-public)

```markdown
---
title: 'Generated Title Here'
description: SEO description here
publishedAt: '2026-06-20'
category: Learning Strategies
tags:
  - tag1
  - tag2
coverImage: 'https://cdn.maigie.com/blog-image/generated-slug.jpeg'
readTime: 7
featured: false
authorName: Maigie Team
authorRole: Learning Science
---

Blog content in Markdoc format...
```

Saved to: `src/content/posts/{slug}.mdoc`
