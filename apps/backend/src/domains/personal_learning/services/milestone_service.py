"""
Milestone Service — Detects learning milestones and integrates with referrals.

Milestones are achievements tied to learning behaviour. When achieved,
they create shareable moments and referral opportunities.

Book principle: "Success creates advocacy. Learners who achieve their goals
become trusted advocates."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from src.shared.database.session import get_session_factory

logger = logging.getLogger(__name__)


# `REFERRAL_REWARD_CREDITS = 10_000` and `MAX_MONTHLY_REFERRALS = 10` are gone with
# `process_referral_completion`. See that function's absence below.


# ===========================================================================
# Milestone Definitions
# ===========================================================================

MILESTONES: list[dict[str, Any]] = [
    {
        "id": "first_prep_complete",
        "description": "Finished an exam preparation from start to end.",
        "title": "First Preparation Complete",
        "condition_type": "prep_completed",
        "threshold": 1,
        "share_text": "I just completed my first exam preparation on Maigie! 🎓",
        "icon": "🎓",
    },
    {
        "id": "7_day_streak",
        "description": "Studied on seven consecutive days.",
        "title": "7-Day Study Streak",
        "condition_type": "streak",
        "threshold": 7,
        "share_text": "7 days of consistent learning! 🔥 Building strong study habits with Maigie.",
        "icon": "🔥",
    },
    {
        "id": "quiz_90_plus",
        "description": "Scored 90% or higher on a quiz.",
        "title": "Quiz Master",
        "condition_type": "quiz_score",
        "threshold": 90,
        "share_text": "Scored 90%+ on my quiz! 💪 Maigie's helping me master my subjects.",
        "icon": "💪",
    },
    {
        "id": "plan_complete",
        "description": "Worked through every item of a study plan.",
        "title": "Study Plan Complete",
        "condition_type": "plan_completion",
        "threshold": 100,
        "share_text": "Completed my entire study plan! 📚 Structured learning really works.",
        "icon": "📚",
    },
    {
        "id": "50_flashcards_reviewed",
        "description": "Reviewed fifty flashcards.",
        "title": "Flashcard Warrior",
        "condition_type": "flashcard_reviews",
        "threshold": 50,
        "share_text": "50 flashcards reviewed! 🧠 Spaced repetition is building my memory.",
        "icon": "🧠",
    },
    {
        "id": "first_document",
        "description": "Generated a study document from your own material.",
        "title": "First Document Generated",
        "condition_type": "document_generated",
        "threshold": 1,
        "share_text": "Just generated my first AI-powered document with Maigie! ✍️",
        "icon": "✍️",
    },
]


# ===========================================================================
# Data Classes
# ===========================================================================


@dataclass
class Milestone:
    """An achieved milestone."""

    milestone_id: str
    title: str
    achieved_at: datetime
    share_text: str
    referral_link: str | None = None
    share_card_url: str | None = None
    icon: str = "🏆"


@dataclass
class ShareCard:
    """A shareable achievement card."""

    milestone_id: str
    title: str
    image_url: str
    share_text: str
    referral_link: str


# The `ReferralReward` dataclass went with `process_referral_completion`; it existed only as that
# function's return type.


# ===========================================================================
# Service Functions
# ===========================================================================


async def check_milestones(user_id: str, action_context: dict[str, Any]) -> list[Milestone]:
    """
    Check if any milestones were just achieved based on the action context.

    Args:
        user_id: The user who performed the action
        action_context: Context about the action (e.g., quiz_score, streak_count, etc.)

    Returns:
        List of newly achieved milestones (empty if none).
    """
    newly_achieved: list[Milestone] = []
    now = datetime.now(UTC)

    # Get already-achieved milestones for this user
    existing = await _get_existing_milestones(user_id)

    for milestone_def in MILESTONES:
        milestone_id = milestone_def["id"]

        # Skip if already achieved
        if milestone_id in existing:
            continue

        # Evaluate condition
        if _evaluate_milestone_condition(milestone_def, action_context):
            # Record the milestone
            referral_link = await _get_or_create_referral_link(user_id)

            await _record_milestone(user_id, milestone_id, now, referral_link)

            newly_achieved.append(
                Milestone(
                    milestone_id=milestone_id,
                    title=milestone_def["title"],
                    achieved_at=now,
                    share_text=milestone_def["share_text"],
                    referral_link=referral_link,
                    icon=milestone_def.get("icon", "🏆"),
                )
            )

            logger.info(f"Milestone achieved: user={user_id} milestone={milestone_id}")

    return newly_achieved


async def get_achieved_milestones(user_id: str) -> list[Milestone]:
    """Get all achieved milestones for a user."""
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        from src.domains.personal_learning.db_models import LearningMilestone

        stmt = (
            select(LearningMilestone)
            .where(LearningMilestone.user_id == user_id)
            .order_by(LearningMilestone.achieved_at.desc())
        )
        result = await session.execute(stmt)
        records = result.scalars().all()

    milestones = []
    for record in records:
        # Find the milestone definition
        milestone_def = next((m for m in MILESTONES if m["id"] == record.milestone_id), None)
        milestones.append(
            Milestone(
                milestone_id=record.milestone_id,
                title=milestone_def["title"] if milestone_def else record.milestone_id,
                achieved_at=record.achieved_at,
                share_text=milestone_def["share_text"] if milestone_def else "",
                referral_link=record.referral_link,
                share_card_url=record.share_card_url,
                icon=milestone_def.get("icon", "🏆") if milestone_def else "🏆",
            )
        )

    return milestones


async def generate_share_card(user_id: str, milestone_id: str) -> ShareCard:
    """
    Generate a shareable achievement card.

    For now, returns a structured response that the frontend can use
    to render a card. In future, could generate an actual image.
    """
    milestone_def = next((m for m in MILESTONES if m["id"] == milestone_id), None)
    if not milestone_def:
        raise ValueError(f"Unknown milestone: {milestone_id}")

    referral_link = await _get_or_create_referral_link(user_id)

    # Update the milestone record with share card URL
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        from src.domains.personal_learning.db_models import LearningMilestone

        stmt = (
            select(LearningMilestone)
            .where(LearningMilestone.user_id == user_id)
            .where(LearningMilestone.milestone_id == milestone_id)
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.shared_at = datetime.now(UTC)
            await session.commit()

    # In a real implementation, this would generate an image and upload to storage.
    # For now, return the structured data for frontend rendering.
    image_url = f"/api/v1/learning/milestones/{milestone_id}/card-image"

    return ShareCard(
        milestone_id=milestone_id,
        title=milestone_def["title"],
        image_url=image_url,
        share_text=milestone_def["share_text"],
        referral_link=referral_link,
    )


# `process_referral_completion` is deleted, and it is the one place in the codebase that had the
# right *trigger* for a referral reward: seven days of genuine activity by the referred learner,
# rather than a signup. Decision O keeps that trigger and changes everything else about it.
#
# What it did was award 10 000 credits into `purchasedCreditsBalance`, which Phase 3 dropped. It was
# also unreachable — no caller — and its two guards were hollow: `_get_monthly_referral_count`
# returned a hard-coded 0, so `MAX_MONTHLY_REFERRALS = 10` could never bind, and the "already
# rewarded for this referral" case its own docstring promised was never checked at all. A referred
# learner could therefore have been paid for repeatedly.
#
# The replacement is a points ledger: points are granted once per referred learner who studies on
# seven distinct days, they expire per grant at 60 days, and they redeem into passes and nothing
# else. Idempotency becomes a property of the ledger row rather than a check somebody has to
# remember to write, and there is no monthly cap — the user's call, on the grounds that the activity
# requirement is the real cap.


# ===========================================================================
# Internal Helpers
# ===========================================================================


def _evaluate_milestone_condition(milestone_def: dict, action_context: dict) -> bool:
    """Evaluate if a milestone condition is met."""
    condition_type = milestone_def["condition_type"]
    threshold = milestone_def["threshold"]

    if condition_type == "prep_completed":
        return action_context.get("preps_completed", 0) >= threshold

    elif condition_type == "streak":
        return action_context.get("current_streak", 0) >= threshold

    elif condition_type == "quiz_score":
        score = action_context.get("quiz_score")
        return score is not None and score >= threshold

    elif condition_type == "plan_completion":
        rate = action_context.get("plan_completion_percentage")
        return rate is not None and rate >= threshold

    elif condition_type == "flashcard_reviews":
        return action_context.get("total_flashcard_reviews", 0) >= threshold

    elif condition_type == "document_generated":
        return action_context.get("documents_generated", 0) >= threshold

    return False


async def _get_existing_milestones(user_id: str) -> set[str]:
    """Get set of milestone IDs already achieved by this user."""
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        from src.domains.personal_learning.db_models import LearningMilestone

        stmt = select(LearningMilestone.milestone_id).where(LearningMilestone.user_id == user_id)
        result = await session.execute(stmt)
        return {row[0] for row in result.all()}


async def _record_milestone(
    user_id: str, milestone_id: str, achieved_at: datetime, referral_link: str | None
) -> None:
    """Record a newly achieved milestone (idempotent via unique index)."""
    factory = get_session_factory()
    async with factory() as session:
        from src.domains.personal_learning.db_models import LearningMilestone

        milestone = LearningMilestone(
            id=__import__("uuid").uuid4().hex[:25],
            user_id=user_id,
            milestone_id=milestone_id,
            achieved_at=achieved_at,
            referral_link=referral_link,
        )
        session.add(milestone)
        try:
            await session.commit()
        except Exception:
            # Unique constraint violation — milestone already recorded (idempotent)
            await session.rollback()


async def _get_or_create_referral_link(user_id: str) -> str:
    """Return the share link that carries the learner's referral code.

    This used to try `referral_service.get_referral_link(user_id)` first, inside an
    `except (ImportError, AttributeError, Exception)` that caught everything and passed. That
    function has never existed, so the branch raised `AttributeError` on every call and the fallback
    ran every time — a fallback with a 100% hit rate, wearing a preference for a better path that was
    never taken. The catch-all is what hid it.

    The link now resolves the learner's real referral code, because the fallback it replaces did not:
    `user_id[:8]` is a slice of a primary key, and `track_referral_signup` looks up
    `User.referralCode`, so every share card ever rendered carried a code that could not be redeemed.
    """
    from src.domains.billing.services.referral_rewards_service import (
        get_or_create_referral_code,
    )

    code = await get_or_create_referral_code(user_id)
    return f"https://app.maigie.com/join?ref={code}"


# `_get_monthly_referral_count` returned a hard-coded 0 and was the sole input to the monthly
# referral cap, which is how a cap came to exist in name only. It went with its caller.
