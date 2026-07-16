"""
Onboarding service — captures learner purpose and builds initial learning profile.

Handles purpose-first onboarding: "What brings you to Maigie today?"
Creates and progressively refines the LearningProfile.
"""

import logging
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def set_purpose(*, user_id: str, purpose: str) -> Any:
    """
    Set the learner's purpose and create their initial LearningProfile.

    Purpose options: exam_prep, skill_building, course_completion,
    professional_certification, general_learning

    Req 14.1: Store purpose and create initial Learning_Profile
    """
    # Check if profile already exists
    existing = await repo.get_profile_by_user(user_id)
    if existing:
        # Update purpose on existing profile
        return await repo.update_profile(user_id, {"purpose": purpose})

    # Create new profile
    return await repo.create_profile(
        {
            "userId": user_id,
            "purpose": purpose,
        }
    )


async def set_subjects(
    *, user_id: str, subjects: list[str] | None = None, goals: str | None = None
) -> Any:
    """
    Set initial subjects and/or goals for the learner.

    Req 14.2: When subjects provided, create study plan seeds.
              When goals provided without subjects, create topic interests.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        # Create profile if it doesn't exist yet
        profile = await repo.create_profile({"userId": user_id})

    update_data: dict[str, Any] = {}
    if subjects:
        update_data["subjects"] = subjects
    if goals:
        update_data["goalsText"] = goals

    if update_data:
        profile = await repo.update_profile(user_id, update_data)

    # Req 14.3: Generate initial recommendations based on stated purpose and goals
    # This will be handled by the discovery service's background task
    # For now, we store the data that the recommendation engine will use

    return profile


async def get_profile(*, user_id: str) -> Any:
    """
    Get the learner's current learning profile.
    Returns None if no profile exists yet.
    """
    return await repo.get_profile_by_user(user_id)


async def update_maturity(*, user_id: str) -> None:
    """
    Increment the profile's maturity_days counter.
    Called by the daily background task.

    Req 14.4: Progressive refinement — maturity increases over time.
    """
    profile = await repo.get_profile_by_user(user_id)
    if profile:
        new_maturity = (profile.maturity_days or 0) + 1
        await repo.update_profile(user_id, {"maturityDays": new_maturity})


async def is_onboarding(*, user_id: str) -> bool:
    """
    Check if the learner is still in the onboarding phase (first 7 days).

    Req 14.5: Onboarding phase = maturity_days <= 7
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return True  # No profile = definitely onboarding
    return (profile.maturity_days or 0) <= 7


async def update_quiet_hours(*, user_id: str, start: str | None, end: str | None) -> Any:
    """Update quiet hours preferences."""
    return await repo.update_profile(
        user_id,
        {
            "quietHoursStart": start,
            "quietHoursEnd": end,
        },
    )
