"""
Moderation service — content reports, Circle hiding, and image moderation.

Handles report submission, admin review, Circle hide/restore, profile image
moderation, and platform-wide ban seat release.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from prisma import Prisma

from src.core.database import db as default_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

REPORT_RATE_LIMITED = "REPORT_RATE_LIMITED"
CIRCLE_HIDDEN_FOR_VIOLATION = "CIRCLE_HIDDEN_FOR_VIOLATION"


class ModerationError(Exception):
    """Structured error raised by moderation operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Report submission
# ---------------------------------------------------------------------------

# Rate limit: max reports per user per hour
_MAX_REPORTS_PER_HOUR = 10


async def submit_report(
    *,
    reporter_user_id: str,
    target_type: str,
    target_id: str,
    reason_code: str,
    description: str | None = None,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Submit a moderation report.

    Rate-limited to _MAX_REPORTS_PER_HOUR per user.
    """
    client = db_client or default_db

    # Rate limit check
    from datetime import timedelta

    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    recent_count = await client.report.count(
        where={
            "reporterId": reporter_user_id,
            "createdAt": {"gte": one_hour_ago},
        }
    )
    if recent_count >= _MAX_REPORTS_PER_HOUR:
        raise ModerationError(
            code=REPORT_RATE_LIMITED,
            message="You have submitted too many reports recently. Please try again later.",
            status_code=429,
        )

    # Validate target type
    valid_types = ("CIRCLE", "MEMBER", "MESSAGE", "RESOURCE", "PROFILE_IMAGE")
    if target_type not in valid_types:
        raise ModerationError(
            code="INVALID_TARGET_TYPE",
            message=f"target_type must be one of: {', '.join(valid_types)}",
            status_code=400,
        )

    # Create report
    report = await client.report.create(
        data={
            "reporterId": reporter_user_id,
            "targetType": target_type,
            "targetId": target_id,
            "reasonCode": reason_code,
            "description": description,
            "status": "PENDING",
        }
    )

    logger.info(
        "Report submitted: id=%s type=%s target=%s by=%s",
        report.id,
        target_type,
        target_id,
        reporter_user_id,
    )

    return {
        "reportId": report.id,
        "status": "PENDING",
        "targetType": target_type,
        "targetId": target_id,
    }


# ---------------------------------------------------------------------------
# Admin report management
# ---------------------------------------------------------------------------


async def list_pending_reports(
    *,
    page: int = 1,
    size: int = 20,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """List pending reports for admin review."""
    client = db_client or default_db

    total = await client.report.count(where={"status": "PENDING"})
    reports = await client.report.find_many(
        where={"status": "PENDING"},
        order={"createdAt": "asc"},
        skip=(page - 1) * size,
        take=size,
    )

    return {
        "items": [
            {
                "id": r.id,
                "reporterId": r.reporterId,
                "targetType": r.targetType,
                "targetId": r.targetId,
                "reasonCode": r.reasonCode,
                "description": getattr(r, "description", None),
                "status": r.status,
                "createdAt": r.createdAt.isoformat(),
            }
            for r in reports
        ],
        "total": total,
        "page": page,
        "size": size,
    }


async def decide_report(
    *,
    report_id: str,
    decision: str,
    admin_user_id: str,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Decide on a report (UPHELD or DISMISSED).

    On UPHELD:
        - CIRCLE target: hide via hiddenByModeration=true
        - PROFILE_IMAGE target: clear User.profileImageUrl
        - MESSAGE target: soft-delete (mark as deleted)
    """
    client = db_client or default_db

    if decision not in ("UPHELD", "DISMISSED"):
        raise ModerationError(
            code="INVALID_DECISION",
            message="Decision must be UPHELD or DISMISSED.",
            status_code=400,
        )

    report = await client.report.find_unique(where={"id": report_id})
    if report is None:
        raise ModerationError(
            code="REPORT_NOT_FOUND",
            message="Report not found.",
            status_code=404,
        )

    # Update report status
    await client.report.update(
        where={"id": report_id},
        data={
            "status": decision,
            "decidedById": admin_user_id,
            "decidedAt": datetime.now(UTC),
        },
    )

    actions_taken: list[str] = []

    if decision == "UPHELD":
        if report.targetType == "CIRCLE":
            await hide_circle(report.targetId, db_client=client)
            actions_taken.append("circle_hidden")

        elif report.targetType == "PROFILE_IMAGE":
            # Clear the user's profile image
            await client.user.update(
                where={"id": report.targetId},
                data={"profileImageUrl": None, "profileImageStatus": "REJECTED"},
            )
            actions_taken.append("profile_image_cleared")

        elif report.targetType == "MESSAGE":
            # Soft-delete the message
            try:
                await client.chatmessage.update(
                    where={"id": report.targetId},
                    data={"isDeleted": True},
                )
                actions_taken.append("message_soft_deleted")
            except Exception:
                logger.warning("Failed to soft-delete message %s", report.targetId)

    logger.info(
        "Report decided: id=%s decision=%s by=%s actions=%s",
        report_id,
        decision,
        admin_user_id,
        actions_taken,
    )

    return {
        "reportId": report_id,
        "decision": decision,
        "actionsTaken": actions_taken,
    }


# ---------------------------------------------------------------------------
# Circle hide / restore
# ---------------------------------------------------------------------------


async def hide_circle(
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> None:
    """Hide a Circle from the repository and block joins."""
    client = db_client or default_db
    await client.circle.update(
        where={"id": circle_id},
        data={"hiddenByModeration": True},
    )
    logger.info("Circle hidden: %s", circle_id)


async def restore_circle(
    circle_id: str,
    *,
    db_client: Prisma | None = None,
) -> None:
    """Restore a hidden Circle (successful appeal)."""
    client = db_client or default_db
    await client.circle.update(
        where={"id": circle_id},
        data={"hiddenByModeration": False},
    )
    logger.info("Circle restored: %s", circle_id)


# ---------------------------------------------------------------------------
# Image moderation
# ---------------------------------------------------------------------------


async def submit_image_for_moderation(image_url: str) -> bool:
    """Submit an image URL for automated content moderation.

    Returns True if the image passes moderation, False if rejected.

    Currently a placeholder that auto-approves. Will be wired to a
    third-party moderation API (e.g. AWS Rekognition, Google Vision
    SafeSearch) in a future iteration.
    """
    # TODO: Wire to actual moderation API
    logger.info("Image moderation check (auto-approve): %s", image_url[:80])
    return True


# ---------------------------------------------------------------------------
# Platform-wide ban
# ---------------------------------------------------------------------------


async def ban_user_platform_wide(
    target_user_id: str,
    *,
    db_client: Prisma | None = None,
) -> dict[str, Any]:
    """Ban a user platform-wide.

    Removes the user from every Circle they belong to and releases any
    held PLUS_SEATs via seat_service.release_seat_on_member_remove.
    """
    client = db_client or default_db

    from src.services.seat_service import release_seat_on_member_remove

    # Find all Circle memberships
    memberships = await client.circlemember.find_many(where={"userId": target_user_id})

    circles_removed_from: list[str] = []
    seats_released: int = 0

    for membership in memberships:
        released = await release_seat_on_member_remove(
            membership.circleId, target_user_id, db_client=client
        )
        if released:
            seats_released += 1

        await client.circlemember.delete(where={"id": membership.id})
        circles_removed_from.append(membership.circleId)

    # Mark user as suspended
    await client.user.update(
        where={"id": target_user_id},
        data={"suspended": True},
    )

    logger.info(
        "Platform ban: user=%s circles_removed=%d seats_released=%d",
        target_user_id,
        len(circles_removed_from),
        seats_released,
    )

    return {
        "userId": target_user_id,
        "circlesRemovedFrom": circles_removed_from,
        "seatsReleased": seats_released,
    }
