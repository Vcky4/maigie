"""
Guidance Engine — LLM-driven autonomous learning orchestration.

The system doesn't suggest features. It prepares what the learner needs
and presents it. The learner simply learns.

"Autonomous learning is a state where the environment is so intelligent,
so responsive, so well-designed that learning happens without effort.
Not without work. But without the overhead."
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


async def compute_guidance(*, user_id: str) -> dict[str, Any]:
    """
    Compute personalized guidance for the learner.

    This is the brain of the autonomous learning experience.
    It looks at the learner's state and returns what's been prepared for them.

    Returns a guidance object that the frontend renders directly.
    """
    # Gather full learner state
    state = await _gather_learner_state(user_id)

    # Hard-priority: due flashcards always come first
    if state["due_flashcard_count"] > 0:
        return _build_review_guidance(state)

    # Hard-priority: today's study plan items
    if state["todays_plan_items"]:
        return _build_plan_guidance(state)

    # Use LLM to determine the most valuable guidance
    return await _compute_intelligent_guidance(user_id, state)


async def _gather_learner_state(user_id: str) -> dict[str, Any]:
    """Gather everything we know about the learner.

    Runs independent queries concurrently via asyncio.gather() for low latency.
    """
    import asyncio

    # Fire all independent queries concurrently
    (
        profile,
        flashcard_stats,
        (_, note_count),
        preps,
        plans,
        due_flashcards,
    ) = await asyncio.gather(
        repo.get_profile_by_user(user_id),
        repo.get_flashcard_stats(user_id),
        repo.list_notes(user_id, where={}, skip=0, take=1),
        repo.list_exam_preps(user_id),
        repo.list_active_plans(user_id),
        repo.list_due_flashcards(user_id),
    )

    # Today's plan items (in-memory filtering, no extra query)
    todays_items = []
    if plans:
        today = datetime.now(UTC).date()
        for plan in plans:
            if plan.items:
                for item in plan.items:
                    if (
                        item.status == "PENDING"
                        and hasattr(item, "scheduled_date")
                        and item.scheduled_date
                        and item.scheduled_date.date() == today
                    ):
                        todays_items.append(item)

    return {
        "profile": profile,
        "has_purpose": bool(profile and profile.purpose),
        "has_subjects": bool(profile and profile.subjects),
        "purpose": getattr(profile, "purpose", None),
        "subjects": getattr(profile, "subjects", None),
        "goals": getattr(profile, "goals_text", None),
        "maturity_days": getattr(profile, "maturity_days", 0) or 0,
        "note_count": note_count,
        "flashcard_total": flashcard_stats.get("total", 0),
        "due_flashcard_count": len(due_flashcards),
        "due_flashcards": due_flashcards[:5],
        "prep_count": len(preps),
        "active_preps": preps,
        "plan_count": len(plans),
        "active_plans": plans,
        "todays_plan_items": todays_items,
    }


def _build_review_guidance(state: dict) -> dict[str, Any]:
    """Due flashcards exist — this is always highest priority."""
    count = state["due_flashcard_count"]
    minutes = max(1, count * 0.5)  # ~30 seconds per card

    return {
        "message": f"You have {count} flashcard{'s' if count != 1 else ''} ready for review. Quick recall keeps knowledge fresh.",
        "todaysFocus": {
            "title": f"Review {count} flashcard{'s' if count != 1 else ''}",
            "reason": "Spaced repetition — reviewing now prevents forgetting.",
            "estimatedMinutes": round(minutes),
            "type": "review_flashcards",
            "actionData": {"action": "start_review"},
        },
        "readyForYou": [],
        "stage": "active",
    }


def _build_plan_guidance(state: dict) -> dict[str, Any]:
    """Study plan items exist for today."""
    items = state["todays_plan_items"]
    first_item = items[0]
    remaining = len(items) - 1

    message = f"Today's focus: {first_item.title}."
    if remaining > 0:
        message += f" Plus {remaining} more item{'s' if remaining != 1 else ''} after that."

    return {
        "message": message,
        "todaysFocus": {
            "title": first_item.title,
            "reason": "Scheduled in your study plan — consistency builds mastery.",
            "estimatedMinutes": getattr(first_item, "estimated_minutes", 30) or 30,
            "type": "complete_plan_item",
            "actionData": {
                "planId": first_item.plan_id,
                "itemId": first_item.id,
            },
        },
        "readyForYou": [
            {
                "type": "plan_item",
                "title": item.title,
                "estimatedMinutes": getattr(item, "estimated_minutes", 30) or 30,
                "actionData": {"planId": item.plan_id, "itemId": item.id},
            }
            for item in items[1:4]  # Show next 3
        ],
        "stage": "active",
    }


async def _compute_intelligent_guidance(user_id: str, state: dict) -> dict[str, Any]:
    """
    Use LLM to determine the most valuable thing to present.
    Falls back to deterministic logic if LLM is unavailable.
    """
    # First: try deterministic fast-path for common states
    deterministic = _deterministic_guidance(state)
    if deterministic:
        return deterministic

    # LLM-driven guidance for complex states
    try:
        return await _llm_guidance(user_id, state)
    except Exception as e:
        logger.warning(f"LLM guidance failed, using fallback: {e}")
        return await _fallback_guidance(state)


def _deterministic_guidance(state: dict) -> dict[str, Any] | None:
    """
    Fast deterministic guidance for clear-cut states.
    Returns None if the state is ambiguous and needs LLM.
    """
    # No purpose set — the only thing we need from the learner
    if not state["has_purpose"]:
        return {
            "message": "Welcome to Maigie. Tell me what brings you here, and I'll prepare everything you need.",
            "todaysFocus": {
                "title": "What are you learning?",
                "reason": "Once I know your goal, I can prepare your study materials, schedule, and practice exercises.",
                "estimatedMinutes": 1,
                "type": "set_purpose",
                "actionData": {"action": "set_purpose"},
            },
            "readyForYou": [],
            "stage": "fresh",
        }

    # Purpose set but no subjects — we need to know what to prepare
    if not state["has_subjects"]:
        return {
            "message": f"Great — you're here for {_format_purpose(state['purpose'])}. What subjects should I prepare for you?",
            "todaysFocus": {
                "title": "What subjects are you studying?",
                "reason": "I'll start building your study materials and schedule as soon as I know.",
                "estimatedMinutes": 1,
                "type": "set_subjects",
                "actionData": {"action": "set_subjects"},
            },
            "readyForYou": [],
            "stage": "purpose_set",
        }

    # Has purpose + subjects but no content yet — check if auto-setup already ran
    if state["note_count"] == 0 and state["flashcard_total"] == 0 and state["prep_count"] == 0:
        # Auto-setup should have run when subjects were set
        # If we still have no content, trigger it again
        return {
            "message": f"I'm preparing your study materials for {', '.join(state['subjects'] or ['your learning'])}. This takes a moment.",
            "todaysFocus": {
                "title": "Setting up your learning environment",
                "reason": "Creating your topics, flashcards, and study plan now.",
                "estimatedMinutes": 0,
                "type": "auto_setup",
                "actionData": {"action": "auto_create_preparation"},
            },
            "readyForYou": [],
            "stage": "setting_up",
        }

    # Has content but no plan — this is where LLM guidance shines
    return None  # Let _llm_guidance handle complex states


async def _llm_guidance(user_id: str, state: dict) -> dict[str, Any]:
    """Use LLM to generate contextual, personalized guidance."""
    from .llm_resilient import generate_content_json

    context = _build_llm_context(state)

    prompt = f"""You are the intelligence layer of Maigie, a learning platform. Your role is to \
decide what to present to this learner right now. You don't suggest — you prepare and present.

LEARNER STATE:
{context}

RULES:
- The learner should never have to plan, organize, or decide what to study
- Present what's ready for them, not what they could do
- Be specific: "Today: Graph Traversal (45 min)" not "Maybe study something?"
- If they have a preparation with topics, tell them exactly which topic is next
- If they have notes without flashcards, tell them flashcards were generated
- Be warm, brief, and action-oriented
- The message should make them feel "Maigie knows exactly what I need"

Return a JSON object with:
- "message": A 1-2 sentence personalized message (warm, specific, encouraging)
- "todaysFocusTitle": What they should focus on right now
- "todaysFocusReason": Why this is the right thing (one sentence)
- "todaysFocusMinutes": Estimated time in minutes
- "todaysFocusType": One of: study_topic, review_flashcards, take_quiz, complete_plan_item, explore
- "readyItems": Array of 0-3 objects with "title" and "description" of other things prepared for them

Return ONLY valid JSON."""

    try:
        # 500 tokens was hitting the ceiling mid-string, leaving the JSON
        # unrepairable. 1200 comfortably covers a warm message + a title +
        # reason + up to 3 ready items with descriptions.
        data = await generate_content_json(
            prompt,
            max_tokens=1200,
            temperature=0.7,
            timeout_s=20,
            fallback=None,
            user_id=user_id,
        )

        return {
            "message": data.get("message", "Ready to continue learning."),
            "todaysFocus": {
                "title": data.get("todaysFocusTitle", "Continue learning"),
                "reason": data.get("todaysFocusReason", "Consistency builds mastery."),
                "estimatedMinutes": data.get("todaysFocusMinutes", 30),
                "type": data.get("todaysFocusType", "explore"),
                "actionData": None,
            },
            "readyForYou": [
                {
                    "type": "prepared",
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                }
                for item in data.get("readyItems", [])
            ],
            "stage": "active",
        }
    except Exception:
        # Let the caller (_compute_intelligent_guidance) log once and
        # fall back — no need to double-report the same exception here.
        raise


async def _fallback_guidance(state: dict) -> dict[str, Any]:
    """Fallback when LLM is unavailable.

    Even in the fallback we surface concrete "ready for you" items so the
    UI is never empty when the learner has an active preparation.
    """
    if state["prep_count"] > 0:
        prep = state["active_preps"][0]
        ready_items = await _build_ready_from_prep(prep, state)
        first_topic_title = ready_items[0]["title"] if ready_items else f"Study: {prep.subject}"
        return {
            "message": f"Continue with your {prep.subject} preparation.",
            "todaysFocus": {
                "title": first_topic_title,
                "reason": "Steady progress toward your goal.",
                "estimatedMinutes": 30,
                "type": "study_topic",
                "actionData": {"prepId": prep.id},
            },
            "readyForYou": ready_items,
            "stage": "active",
        }

    return {
        "message": "Ready to learn. Ask me anything or start a new topic.",
        "todaysFocus": {
            "title": "Explore or ask Maigie",
            "reason": "Every session moves you forward.",
            "estimatedMinutes": 15,
            "type": "explore",
            "actionData": None,
        },
        "readyForYou": [],
        "stage": "active",
    }


async def _build_ready_from_prep(prep: Any, state: dict) -> list[dict[str, Any]]:
    """
    Build up to 3 concrete "ready for you" items from an active preparation.

    Priority:
      1. Next unmastered prep topics (what to study next).
      2. Due flashcards summary (if any exist and none surfaced elsewhere).
    """
    items: list[dict[str, Any]] = []
    try:
        topics = await repo.list_prep_topics(prep.id)
    except Exception as e:  # pragma: no cover — belt-and-braces
        logger.warning(f"Failed to load prep topics for ready-for-you: {e}")
        topics = []

    # Prefer the first 3 not-yet-mastered topics
    unmastered = [t for t in topics if getattr(t, "status", "PENDING") != "MASTERED"]
    for topic in unmastered[:3]:
        items.append(
            {
                "type": "prep_topic",
                "title": getattr(topic, "title", "Study topic"),
                "description": getattr(topic, "description", None) or f"Part of {prep.subject}",
                "actionData": {
                    "prepId": prep.id,
                    "prepTopicId": getattr(topic, "id", None),
                },
            }
        )

    if state.get("flashcard_total", 0) > 0 and len(items) < 3:
        items.append(
            {
                "type": "flashcards",
                "title": f"{state['flashcard_total']} flashcards ready",
                "description": "Practice recall anytime — 30 seconds per card.",
                "actionData": {"action": "browse_flashcards"},
            }
        )

    return items


def _build_llm_context(state: dict) -> str:
    """Build a concise context string for the LLM."""
    parts = []
    parts.append(f"Purpose: {state['purpose'] or 'not set'}")
    parts.append(f"Subjects: {', '.join(state['subjects'] or ['not set'])}")
    parts.append(f"Goals: {state['goals'] or 'not specified'}")
    parts.append(f"Days active: {state['maturity_days']}")
    parts.append(f"Notes: {state['note_count']}")
    parts.append(f"Flashcards: {state['flashcard_total']} (due: {state['due_flashcard_count']})")
    parts.append(f"Preparations: {state['prep_count']}")
    parts.append(f"Active plans: {state['plan_count']}")

    if state["active_preps"]:
        prep = state["active_preps"][0]
        parts.append(f"Current preparation: {prep.subject} (status: {prep.status})")

    return "\n".join(parts)


def _format_purpose(purpose: str | None) -> str:
    """Format purpose enum to human-readable."""
    mapping = {
        "exam_prep": "exam preparation",
        "skill_building": "skill building",
        "course_completion": "completing a course",
        "professional_certification": "professional certification",
        "general_learning": "learning",
    }
    return mapping.get(purpose or "", purpose or "learning")
