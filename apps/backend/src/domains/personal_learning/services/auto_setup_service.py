"""
Auto-Setup Service — proactive content creation.

When a learner provides their purpose and subjects, the system
automatically prepares everything they need to start learning.

"Autonomous learning is a state where the environment handles
the planning, scheduling, searching, and organising.
The learner simply learns."
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def auto_setup_for_learner(*, user_id: str) -> dict[str, Any]:
    """
    Proactively create initial content based on the learner's profile.

    Called after onboarding is complete (purpose + subjects set).
    Creates preparation, extracts topics, generates flashcards, and builds study plan.

    Returns a summary of what was created.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile or not profile.purpose or not profile.subjects:
        logger.info(f"Auto-setup skipped for user {user_id}: incomplete profile")
        return {"status": "skipped", "reason": "incomplete_profile"}

    purpose = profile.purpose
    subjects = profile.subjects or []
    goals = profile.goals_text or ""

    created: dict[str, Any] = {
        "preparations": [],
        "topics": [],
        "flashcards": [],
        "studyPlan": None,
    }

    try:
        # Step 1: Create a preparation for the primary subject
        prep = await _create_preparation(user_id, purpose, subjects, goals)
        if prep:
            created["preparations"].append(prep.id)

            # Step 2: Extract topics via LLM
            topics = await _extract_topics(user_id, prep.id, subjects, goals)
            created["topics"] = [t.id for t in topics]

            # Step 3: Generate initial flashcards from topics
            flashcards = await _generate_initial_flashcards(user_id, subjects)
            created["flashcards"] = [f.id for f in flashcards]

            # Step 4: Generate study plan if there's a deadline context
            plan = await _create_study_plan(user_id, prep, topics, goals)
            if plan:
                created["studyPlan"] = plan.id

        logger.info(
            f"Auto-setup complete for user {user_id}: "
            f"{len(created['topics'])} topics, {len(created['flashcards'])} flashcards"
        )
        return {"status": "completed", "created": created}

    except Exception as e:
        logger.error(f"Auto-setup failed for user {user_id}: {e}")
        return {"status": "partial", "created": created, "error": str(e)}


async def _create_preparation(
    user_id: str, purpose: str, subjects: list[str], goals: str
) -> Any:
    """Create a preparation based on purpose and subjects."""
    from . import exam_prep_service

    # Determine type from purpose
    type_map = {
        "exam_prep": "EXAM",
        "professional_certification": "CERTIFICATION",
        "skill_building": "PROJECT",
        "course_completion": "ASSIGNMENT",
        "general_learning": "PROJECT",
    }
    prep_type = type_map.get(purpose, "PROJECT")

    # Default deadline: 30 days from now (can be adjusted later)
    default_deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    subject_title = subjects[0] if subjects else "My Learning"
    description = goals if goals else f"Preparation for {', '.join(subjects)}"

    try:
        prep = await exam_prep_service.create_preparation(
            user_id=user_id,
            data={
                "subject": subject_title,
                "type": prep_type,
                "targetDate": default_deadline,
                "description": description,
            },
        )
        return prep
    except Exception as e:
        logger.warning(f"Failed to auto-create preparation: {e}")
        return None


async def _extract_topics(
    user_id: str, prep_id: str, subjects: list[str], goals: str
) -> list[Any]:
    """Extract topics using AI from the subject matter."""
    from . import exam_prep_service

    try:
        topics = await exam_prep_service.extract_topics(user_id=user_id, prep_id=prep_id)
        return topics
    except Exception as e:
        logger.warning(f"Failed to auto-extract topics: {e}")
        return []


async def _generate_initial_flashcards(
    user_id: str, subjects: list[str]
) -> list[Any]:
    """Generate starter flashcards for the learner's subjects."""
    from src.domains.intelligence.reasoning.llm import generate_content
    from . import flashcard_service
    import json

    if not subjects:
        return []

    # Generate flashcards for the first subject
    subject = subjects[0]
    prompt = (
        f"Create 5 fundamental flashcards for someone beginning to study {subject}.\n"
        f"These should cover the most basic, essential concepts a beginner needs to know.\n\n"
        f"Return a JSON array of objects with 'front' (question) and 'back' (answer).\n"
        f"Keep answers concise (1-2 sentences).\n"
        f"Return ONLY the JSON array."
    )

    try:
        response = await generate_content(prompt, max_tokens=1500)
        cards_data = json.loads(response)
    except Exception as e:
        logger.warning(f"Failed to generate initial flashcards: {e}")
        return []

    created_cards = []
    for card in cards_data:
        if isinstance(card, dict) and "front" in card and "back" in card:
            try:
                flashcard = await flashcard_service.create_flashcard(
                    user_id=user_id,
                    data={
                        "front": card["front"],
                        "back": card["back"],
                        "sourceType": "auto_setup",
                        "sourceId": subject,
                    },
                )
                created_cards.append(flashcard)
            except Exception as e:
                logger.warning(f"Failed to create flashcard: {e}")

    return created_cards


async def _create_study_plan(
    user_id: str, prep: Any, topics: list[Any], goals: str
) -> Any:
    """Create a study plan distributing topics across available days."""
    from . import study_plan_service

    if not topics:
        return None

    try:
        plan = await study_plan_service.generate_plan(
            user_id=user_id,
            data={
                "title": f"Study Plan: {prep.subject}",
                "goalDescription": goals or f"Master {prep.subject}",
                "deadline": (
                    prep.exam_date.isoformat()
                    if prep.exam_date
                    else (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                ),
                "prepId": prep.id,
            },
        )
        return plan
    except Exception as e:
        logger.warning(f"Failed to auto-create study plan: {e}")
        return None
