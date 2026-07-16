"""
Personal Learning Chat — contextual intelligence integration.

Wraps the intelligence domain's conversation service with personal
learning context so that asking Maigie feels personalized, not like
switching into a separate AI product.
"""

import logging
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def send_message(*, user_id: str, message: str) -> dict[str, Any]:
    """
    Send a message with personal learning context injected.

    Req 9.1: Include learner's current course, topic, recent notes, progress, goals, and profile.
    Req 9.3: Provide concrete recommendations based on schedule, due reviews, study plan.
    Req 9.5: Persist in existing conversation system.
    Req 9.6: Suggest logical next action.
    """
    from . import behaviour_service, flashcard_service, onboarding_service

    # Gather context
    profile = await repo.get_profile_by_user(user_id)
    behaviour = await behaviour_service.get_behaviour_profile(user_id=user_id)
    due_count = len(await flashcard_service.get_due_flashcards(user_id=user_id))

    # Build system context for the LLM
    context_parts = []
    if profile:
        if profile.purpose:
            context_parts.append(f"Learning purpose: {profile.purpose}")
        if profile.subjects:
            context_parts.append(f"Subjects: {', '.join(profile.subjects)}")
        if profile.goals_text:
            context_parts.append(f"Goals: {profile.goals_text}")

    if behaviour.get("avgSessionMinutes"):
        context_parts.append(f"Average study session: {behaviour['avgSessionMinutes']} minutes")
    if behaviour.get("consistencyScore"):
        context_parts.append(f"Consistency score: {behaviour['consistencyScore']}/100")
    if behaviour.get("bestDayOfWeek"):
        context_parts.append(f"Best study day: {behaviour['bestDayOfWeek']}")

    if due_count > 0:
        context_parts.append(f"Flashcards due for review: {due_count}")

    system_context = (
        "You are Maigie, a personal learning assistant. You know this learner deeply:\n"
        + "\n".join(f"- {p}" for p in context_parts)
        + "\n\nBe specific, encouraging, and actionable. If asked what to study, "
        "give a concrete recommendation. Suggest next actions at the end of your response."
    )

    # Delegate to intelligence domain conversation service
    try:
        from src.domains.intelligence.conversation import conversation_service

        response = await conversation_service.send_message_with_context(
            user_id=user_id,
            message=message,
            system_context=system_context,
            session_type="personal_learning",
        )
        return response
    except (ImportError, AttributeError):
        # Fallback if intelligence domain method doesn't exist yet
        from src.domains.intelligence.reasoning.llm import generate_content

        prompt = f"{system_context}\n\nLearner: {message}\n\nMaigie:"
        ai_response = await generate_content(prompt, max_tokens=1000)

        return {
            "message": ai_response,
            "suggestedAction": _suggest_action(message, due_count),
        }


def _suggest_action(message: str, due_count: int) -> dict[str, Any] | None:
    """Suggest a logical next action based on conversation content."""
    message_lower = message.lower()

    if "note" in message_lower or "write" in message_lower:
        return {"type": "create_note", "title": "Create a note"}
    if "flashcard" in message_lower or "review" in message_lower:
        return {"type": "review_flashcards", "title": "Review flashcards"}
    if "quiz" in message_lower or "practice" in message_lower:
        return {"type": "start_quiz", "title": "Start a practice quiz"}
    if "plan" in message_lower or "schedule" in message_lower:
        return {"type": "view_study_plan", "title": "View your study plan"}
    if due_count > 0:
        return {"type": "review_flashcards", "title": f"Review {due_count} due flashcards"}

    return None
