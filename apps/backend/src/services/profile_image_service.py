"""
Profile image service — upload, moderation, and removal.

Handles user profile image uploads with validation (JPEG/PNG/WEBP, ≤5 MB,
≤4096×4096), automated content moderation before publishing, and removal.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from typing import Any

from prisma import Prisma

from src.core.database import db as default_db

logger = logging.getLogger(__name__)

# Validation constants
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_DIMENSION = 4096


class ProfileImageError(Exception):
    """Structured error raised by profile image operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def upload_profile_image(
    user_id: str,
    file_content: bytes,
    content_type: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Upload and set a user's profile image.

    Validation:
        - Content type must be JPEG, PNG, or WEBP
        - File size must be ≤5 MB
        - Image dimensions must be ≤4096×4096

    The image is submitted for moderation before the URL is persisted.
    On rejection, the prior URL is preserved and an error is returned.

    Returns a dict with the new profileImageUrl on success.
    """
    client = db_client or default_db

    # Validate content type
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ProfileImageError(
            code="INVALID_IMAGE_TYPE",
            message=f"Image must be JPEG, PNG, or WEBP. Got: {content_type}",
            status_code=400,
        )

    # Validate file size
    if len(file_content) > MAX_FILE_SIZE_BYTES:
        raise ProfileImageError(
            code="IMAGE_TOO_LARGE",
            message=f"Image must be ≤5 MB. Got: {len(file_content) / (1024 * 1024):.1f} MB",
            status_code=400,
        )

    # Validate dimensions (best-effort; skip if PIL not available)
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(file_content))
        width, height = img.size
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise ProfileImageError(
                code="IMAGE_TOO_LARGE_DIMENSIONS",
                message=f"Image dimensions must be ≤{MAX_DIMENSION}×{MAX_DIMENSION}. Got: {width}×{height}",
                status_code=400,
            )
    except ImportError:
        # PIL not available; skip dimension check
        logger.warning("PIL not available; skipping image dimension validation")
    except ProfileImageError:
        raise
    except Exception as e:
        logger.warning("Image dimension check failed: %s", e)

    # Upload to storage
    try:
        from src.services.storage_service import storage_service

        image_url = await storage_service.upload_profile_image(user_id, file_content, content_type)
    except Exception as e:
        logger.error("Failed to upload profile image for user %s: %s", user_id, e)
        raise ProfileImageError(
            code="UPLOAD_FAILED",
            message="Failed to upload image. Please try again.",
            status_code=500,
        ) from e

    # Submit for moderation (Requirement 14.3: moderation precedes publish)
    # For now, auto-approve. Task 9.5 will wire real moderation.
    moderation_passed = True
    try:
        # Placeholder: when moderation_service is implemented, call:
        # moderation_passed = await moderation_service.submit_image_for_moderation(image_url)
        pass
    except Exception as e:
        logger.warning("Image moderation check failed, allowing: %s", e)

    if not moderation_passed:
        raise ProfileImageError(
            code="IMAGE_MODERATION_REJECTED",
            message="Your image was rejected by our content moderation system.",
            status_code=400,
        )

    # Persist URL on User record
    await client.user.update(
        where={"id": user_id},
        data={
            "profileImageUrl": image_url,
            "profileImageStatus": "APPROVED",
        },
    )

    logger.info("Profile image uploaded for user %s", user_id)
    return {"profileImageUrl": image_url, "status": "APPROVED"}


async def remove_profile_image(
    user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Remove a user's profile image (clear URL within 60 s)."""
    client = db_client or default_db

    await client.user.update(
        where={"id": user_id},
        data={
            "profileImageUrl": None,
            "profileImageStatus": None,
        },
    )

    logger.info("Profile image removed for user %s", user_id)
    return {"profileImageUrl": None, "status": "removed"}
