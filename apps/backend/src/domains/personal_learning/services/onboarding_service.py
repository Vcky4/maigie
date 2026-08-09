"""
Onboarding service — captures learner purpose and builds initial learning profile.

Handles purpose-first onboarding: "What brings you to Maigie today?"
Creates and progressively refines the LearningProfile.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from src.domains.identity.repository import IdentityRepository
from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def set_purpose(*, user_id: str, purpose: str) -> Any:
    """
    Set the learner's purpose and create their initial LearningProfile.

    Purpose options: exam_prep, skill_building, course_completion,
    professional_certification, teaching, community, general_learning

    Req 14.1: Store purpose and create initial Learning_Profile
    """
    # Check if profile already exists
    existing = await repo.get_profile_by_user(user_id)
    if existing:
        # Update purpose and state on existing profile
        return await repo.update_profile(
            user_id, {"purpose": purpose, "onboardingState": "purpose_set"}
        )

    # Create new profile with onboarding state
    return await repo.create_profile(
        {
            "userId": user_id,
            "purpose": purpose,
            "onboardingState": "purpose_set",
        }
    )


async def set_exam_details(
    *,
    user_id: str,
    exam_name: str,
    exam_date: date | None = None,
    subjects: list[str] | None = None,
    goals: str | None = None,
) -> Any:
    """
    Set exam preparation details and trigger content generation.

    This endpoint is for EXAM_PREP purpose learners. Sets exam-specific
    context and triggers background content generation job.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        # Create profile if it doesn't exist yet
        profile = await repo.create_profile(
            {"userId": user_id, "purpose": "exam_prep", "onboardingState": "purpose_set"}
        )

    update_data: dict[str, Any] = {
        "examName": exam_name,
        "onboardingState": "details_set",
    }

    if exam_date:
        update_data["examDate"] = exam_date
    if subjects:
        update_data["subjects"] = subjects
    if goals:
        update_data["goalsText"] = goals

    profile = await repo.update_profile(user_id, update_data)

    # Trigger background content generation
    try:
        from . import auto_setup_service

        # Run in background (fire-and-forget for now, should be Celery task)
        import asyncio

        asyncio.create_task(
            _generate_onboarding_content(
                user_id=user_id,
                exam_name=exam_name,
                exam_date=exam_date,
                subjects=subjects or [],
                goals=goals,
            )
        )
    except Exception as e:
        logger.error(f"Failed to start content generation: {e}")

    return profile


async def set_skill_details(
    *,
    user_id: str,
    skill_name: str,
    current_level: str | None = None,
    subjects: list[str] | None = None,
    goals: str | None = None,
) -> Any:
    """
    Set skill building details and trigger content generation.

    This endpoint is for SKILL_BUILDING purpose learners. Sets skill-specific
    context and triggers background content generation job.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        # Create profile if it doesn't exist yet
        profile = await repo.create_profile(
            {"userId": user_id, "purpose": "skill_building", "onboardingState": "purpose_set"}
        )

    update_data: dict[str, Any] = {
        "skillName": skill_name,
        "onboardingState": "details_set",
    }

    if current_level:
        update_data["currentLevel"] = current_level
    if subjects:
        update_data["subjects"] = subjects
    if goals:
        update_data["goalsText"] = goals

    profile = await repo.update_profile(user_id, update_data)

    # Trigger background content generation
    try:
        import asyncio

        asyncio.create_task(
            _generate_onboarding_content(
                user_id=user_id,
                skill_name=skill_name,
                current_level=current_level,
                subjects=subjects or [],
                goals=goals,
            )
        )
    except Exception as e:
        logger.error(f"Failed to start content generation: {e}")

    return profile


async def _generate_onboarding_content(
    user_id: str,
    exam_name: str | None = None,
    exam_date: date | None = None,
    skill_name: str | None = None,
    current_level: str | None = None,
    subjects: list[str] | None = None,
    goals: str | None = None,
) -> None:
    """
    Generate initial content for a new learner.

    This should be a Celery background task, but for now runs as async task.

    Creates:
    - Preparation (exam or general)
    - Topics extracted via AI
    - Initial flashcards
    - Study plan
    """
    try:
        from . import auto_setup_service

        # Call existing auto-setup with context
        await auto_setup_service.auto_setup_for_learner(user_id=user_id)

        # Update state to content_ready
        await repo.update_profile(user_id, {"onboardingState": "content_ready"})

    except Exception as e:
        logger.error(f"Content generation failed for user {user_id}: {e}")
        # Don't update state on failure - keeps user in details_set state


async def get_onboarding_status(*, user_id: str) -> dict[str, Any]:
    """
    Get current onboarding status for progress polling.

    Returns:
    - state: Current onboarding state
    - progress: Dict of completed steps
    - estimatedSecondsRemaining: Time estimate (if generating)
    - firstPreparation: First prep created (if ready)
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        return {
            "state": "not_started",
            "progress": {},
            "estimatedSecondsRemaining": None,
            "firstPreparation": None,
        }

    state = profile.onboarding_state or "not_started"

    # Check if content exists
    preps = await repo.list_exam_preps(user_id)
    first_prep = preps[0] if preps else None

    progress = {
        "preparation": first_prep is not None,
        "topics": False,  # TODO: check topics table
        "flashcards": False,  # TODO: check flashcards
        "studyPlan": False,  # TODO: check study plans
    }

    # Estimate time remaining (rough heuristic)
    estimated_seconds = None
    if state == "details_set":
        estimated_seconds = 30  # Initial estimate
    elif state == "content_ready":
        estimated_seconds = 0

    return {
        "state": state,
        "progress": progress,
        "estimatedSecondsRemaining": estimated_seconds,
        "firstPreparation": (
            {"id": first_prep.id, "subject": first_prep.subject} if first_prep else None
        ),
    }


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
    """Get the learner's current learning profile."""
    return await repo.get_profile_by_user(user_id)


async def complete_onboarding(*, user_id: str) -> None:
    """Complete identity onboarding and record when the learning profile was completed."""
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        raise NotFoundError("Learning profile", user_id)

    await IdentityRepository().set_onboarded(user_id)
    await repo.update_profile(
        user_id,
        {
            "onboardingCompletedAt": datetime.now(UTC),
            "onboardingState": "completed",
        },
    )


async def set_preferred_llm_provider(*, user_id: str, provider: str) -> Any:
    """Persist the provider used by resilient personal-learning LLM calls."""
    profile = await repo.get_profile_by_user(user_id)
    if not profile:
        raise NotFoundError("Learning profile", user_id)

    return await repo.update_profile(user_id, {"preferredLlmProvider": provider})


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
    notes, note_count = await personal_learning_repo.list_notes(user_id, where={}, skip=0, take=1)
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
