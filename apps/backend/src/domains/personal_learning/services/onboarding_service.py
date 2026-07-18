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

    After subjects are set, automatically create initial content:
    - Preparation (exam prep, certification, etc.)
    - Topics extracted via AI
    - Initial flashcards
    - Study plan

    The learner provides intent — the system prepares everything.

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

    # Trigger auto-setup: create preparation, topics, flashcards, study plan
    # This runs inline so the learner sees "setting up" in the next Home request
    # and the content is ready by the time they check again
    try:
        from . import auto_setup_service

        await auto_setup_service.auto_setup_for_learner(user_id=user_id)
    except Exception as e:
        logger.error(f"Auto-setup failed after subjects set: {e}")
        # Don't fail the subjects endpoint — auto-setup is best-effort

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
    Check if the learner is still in the onboarding phase.

    Onboarding ends (returns False) when EITHER:
    - Time-based: maturity_days > 7 (fallback for inactive users)
    - Activity-based: the learner has a purpose set AND has created
      at least one piece of content (note, flashcard, or preparation)

    This ensures active learners get real value immediately while
    inactive users still see gentle onboarding prompts.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return True  # No profile = definitely onboarding

    # Time-based exit (fallback)
    if (profile.maturity_days or 0) > 7:
        return False

    # Activity-based exit: purpose set + at least one content item created
    if not profile.purpose:
        return True  # Haven't even set purpose yet

    # Check if user has created any content (notes, flashcards, or preparations)
    from ..repository import personal_learning_repo

    # Quick check: any flashcards?
    stats = await personal_learning_repo.get_flashcard_stats(user_id)
    if stats.get("total", 0) > 0:
        return False

    # Any notes?
    notes, note_count = await personal_learning_repo.list_notes(
        user_id, where={}, skip=0, take=1
    )
    if note_count > 0:
        return False

    # Any preparations?
    preps = await personal_learning_repo.list_exam_preps(user_id)
    if preps:
        return False

    # Any study plans?
    plans = await personal_learning_repo.list_active_plans(user_id)
    if plans:
        return False

    return True  # Still onboarding — no content created yet


async def update_quiet_hours(*, user_id: str, start: str | None, end: str | None) -> Any:
    """Update quiet hours preferences."""
    return await repo.update_profile(
        user_id,
        {
            "quietHoursStart": start,
            "quietHoursEnd": end,
        },
    )
