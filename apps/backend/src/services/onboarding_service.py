"""
Chat-based onboarding service.

Stores onboarding progress on the user (in UserPreferences.studyGoals.onboarding)
and drives a guided chat flow for new users.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from prisma import Json

from src.services.action_service import action_service

LearnerType = Literal["university", "self_paced"]


def _now_iso() -> str:
    # Keep it lightweight; we don't need timezone-aware for simple client display.
    from datetime import datetime, UTC

    return datetime.now(UTC).isoformat()


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _parse_yes_no(s: str) -> bool | None:
    t = _normalize_text(s)
    if t in {"yes", "y", "yeah", "yep", "sure", "ok", "okay"}:
        return True
    if t in {"no", "n", "nope", "nah"}:
        return False
    return None


def _extract_learner_type(text: str) -> LearnerType | None:
    t = _normalize_text(text)
    if any(x in t for x in ["self paced", "self-paced", "selfpace", "self"]):
        return "self_paced"
    if any(x in t for x in ["university", "college", "campus", "undergraduate", "postgraduate"]):
        return "university"
    if t in {"student"}:
        return "university"
    return None


def _parse_list_items(text: str, *, max_items: int = 10) -> list[str]:
    """
    Parse a free-form message into a list of items (courses).
    Handles:
    - newline lists
    - comma / semicolon separated
    - numbered lists (1. ..., 2) ...)
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # Prefer newline splitting first (more common for lists)
    if "\n" in raw:
        parts = raw.splitlines()
    else:
        parts = re.split(r"[;,]", raw)

    items: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Strip bullets / numbering prefixes
        p = re.sub(r"^\s*[-*•]\s*", "", p)
        p = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", p)
        p = p.strip()
        if not p:
            continue
        items.append(p)

    # De-duplicate (case-insensitive) while preserving order
    out: list[str] = []
    seen = set()
    for it in items:
        k = _normalize_text(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= max_items:
            break
    return out


def default_onboarding_state() -> dict[str, Any]:
    return {
        "version": 1,
        "stage": "welcome",  # welcome | uni_name | uni_details | focus_level | courses | creating | done
        "learnerType": None,
        "profile": {},
        "courses": [],
        "createdCourseIds": [],
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }


async def _get_user_study_goals(db, user_id: str) -> dict[str, Any]:
    user = await db.user.find_unique(where={"id": user_id}, include={"preferences": True})
    prefs = getattr(user, "preferences", None)
    study_goals = getattr(prefs, "studyGoals", None) if prefs else None
    if isinstance(study_goals, dict):
        return study_goals
    return {}


async def get_onboarding_state(db, user_id: str) -> dict[str, Any]:
    study_goals = await _get_user_study_goals(db, user_id)
    onboarding = study_goals.get("onboarding")
    if isinstance(onboarding, dict) and onboarding.get("version") == 1:
        return onboarding
    return default_onboarding_state()


async def save_onboarding_state(db, user_id: str, state: dict[str, Any]) -> None:
    study_goals = await _get_user_study_goals(db, user_id)
    state = dict(state or {})
    state["version"] = 1
    state["updatedAt"] = _now_iso()
    study_goals["onboarding"] = state

    # Upsert preferences and set studyGoals.
    await db.user.update(
        where={"id": user_id},
        data={
            "preferences": {
                "upsert": {
                    "create": {
                        "theme": "light",
                        "language": "en",
                        "notifications": True,
                        "studyGoals": Json(study_goals),
                    },
                    "update": {"studyGoals": Json(study_goals)},
                }
            }
        },
    )


async def ensure_onboarding_initialized(db, user_id: str) -> dict[str, Any]:
    state = await get_onboarding_state(db, user_id)
    # If it was missing, persist it so we have a stable place to write later.
    await save_onboarding_state(db, user_id, state)
    return state


@dataclass
class OnboardingResult:
    reply_text: str
    is_complete: bool = False
    created_courses: list[dict[str, Any]] | None = None  # lightweight course list


def _welcome_prompt() -> str:
    return (
        "Welcome! I’m Maigie.\n\n"
        "Before we start: are you a **university student** or a **self‑paced learner**?\n"
        "Reply with `university` or `self-paced`."
    )


async def handle_onboarding_message(
    db, *, user, session_id: str, user_text: str
) -> OnboardingResult:
    """
    Advance the onboarding state machine based on the user's message.
    """
    user_id = user.id
    state = await get_onboarding_state(db, user_id)
    stage = state.get("stage") or "welcome"
    learner_type: LearnerType | None = state.get("learnerType")
    profile: dict[str, Any] = state.get("profile") if isinstance(state.get("profile"), dict) else {}

    text = (user_text or "").strip()

    # Allow a manual reset any time during onboarding.
    if _normalize_text(text) in {"restart", "restart onboarding", "reset onboarding"}:
        state = default_onboarding_state()
        await save_onboarding_state(db, user_id, state)
        return OnboardingResult(reply_text=_welcome_prompt(), is_complete=False)

    if stage == "welcome":
        lt = _extract_learner_type(text)
        if lt is None:
            return OnboardingResult(reply_text=_welcome_prompt(), is_complete=False)

        state["learnerType"] = lt
        if lt == "university":
            state["stage"] = "uni_name"
            await save_onboarding_state(db, user_id, state)
            return OnboardingResult(
                reply_text="Nice — what’s the name of your university?", is_complete=False
            )

        # self-paced path
        state["stage"] = "focus_level"
        await save_onboarding_state(db, user_id, state)
        return OnboardingResult(
            reply_text=(
                "Great. What are you learning right now, and what’s your current level?\n"
                "Example: `Data analytics, beginner`"
            ),
            is_complete=False,
        )

    if stage == "uni_name":
        if not text:
            return OnboardingResult(
                reply_text="What’s the name of your university?", is_complete=False
            )
        profile["universityName"] = text
        state["profile"] = profile
        state["stage"] = "uni_details"
        await save_onboarding_state(db, user_id, state)
        return OnboardingResult(
            reply_text=(
                "Got it. What’s your **faculty**, **department**, and **current level**?\n"
                "Example: `Science, Computer Science, 300L`"
            ),
            is_complete=False,
        )

    if stage == "uni_details":
        # Best-effort parse: Faculty, Department, Level
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 3:
            profile["faculty"] = parts[0]
            profile["department"] = parts[1]
            profile["level"] = parts[2]
        else:
            # Store raw; we can continue anyway (don't block users).
            profile["uniDetailsRaw"] = text
        state["profile"] = profile
        state["stage"] = "courses"
        await save_onboarding_state(db, user_id, state)
        return OnboardingResult(
            reply_text=(
                "Awesome. List your courses for this term/semester (one per line or comma-separated).\n"
                "Example:\n"
                "- Calculus\n"
                "- Data Structures\n"
                "- Operating Systems"
            ),
            is_complete=False,
        )

    if stage == "focus_level":
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            profile["focusArea"] = parts[0]
            profile["level"] = parts[1]
        else:
            profile["focusRaw"] = text
        state["profile"] = profile
        state["stage"] = "courses"
        await save_onboarding_state(db, user_id, state)
        return OnboardingResult(
            reply_text=(
                "Nice. List the courses/topics you want to study (one per line or comma-separated)."
            ),
            is_complete=False,
        )

    if stage == "courses":
        courses = _parse_list_items(text, max_items=10)
        if not courses:
            return OnboardingResult(
                reply_text="Please list at least 1 course (one per line or comma-separated).",
                is_complete=False,
            )
        state["courses"] = courses
        state["stage"] = "creating"
        await save_onboarding_state(db, user_id, state)

        # Create courses now.
        # Avoid duplicates against existing courses.
        existing = await db.course.find_many(where={"userId": user_id}, take=200)
        existing_by_norm = {
            _normalize_text(c.title): c for c in existing if getattr(c, "title", None)
        }

        created_course_ids: list[str] = []
        created_courses_light: list[dict[str, Any]] = []

        # University: create courses WITHOUT AI outlines (no modules/topics).
        # Self-paced: create AI generated outlines (using action_service.create_course).
        if learner_type == "university":
            for title in courses:
                norm = _normalize_text(title)
                if norm in existing_by_norm:
                    continue
                course = await db.course.create(
                    data={
                        "userId": user_id,
                        "title": title,
                        "description": "University course (outline pending).",
                        "isAIGenerated": False,
                    }
                )
                created_course_ids.append(course.id)
                created_courses_light.append(
                    {
                        "courseId": course.id,
                        "id": course.id,
                        "title": course.title,
                        "description": course.description or "",
                        "progress": 0.0,
                        "difficulty": getattr(course, "difficulty", None),
                        "completedTopics": 0,
                        "totalTopics": 0,
                    }
                )
        else:
            # self-paced
            for title in courses:
                norm = _normalize_text(title)
                if norm in existing_by_norm:
                    continue
                # Let action_service generate outline by providing no modules.
                result = await action_service.create_course(
                    {
                        "title": title,
                        "description": f"A structured course on {title}.",
                        "difficulty": "BEGINNER",
                        "modules": [],
                    },
                    user_id,
                )
                if result and result.get("status") == "success":
                    cid = result.get("courseId") or result.get("course_id")
                    if cid:
                        created_course_ids.append(cid)

            # Load created courses for lightweight list
            if created_course_ids:
                created = await db.course.find_many(
                    where={"id": {"in": created_course_ids}, "userId": user_id},
                    include={"modules": {"include": {"topics": True}}},
                )
                for course in created:
                    total_topics = sum(len(m.topics) for m in course.modules)
                    completed_topics = sum(
                        sum(1 for t in m.topics if t.completed) for m in course.modules
                    )
                    progress = (completed_topics / total_topics * 100) if total_topics else 0.0
                    created_courses_light.append(
                        {
                            "courseId": course.id,
                            "id": course.id,
                            "title": course.title,
                            "description": course.description or "",
                            "progress": progress,
                            "difficulty": getattr(course, "difficulty", None),
                            "completedTopics": completed_topics,
                            "totalTopics": total_topics,
                        }
                    )

        state["createdCourseIds"] = created_course_ids
        state["stage"] = "done"
        await save_onboarding_state(db, user_id, state)

        # Mark user onboarded once at least one course exists (created now or already existed).
        total_course_count = await db.course.count(where={"userId": user_id, "archived": False})
        if total_course_count > 0:
            await db.user.update(where={"id": user_id}, data={"isOnboarded": True})

        if learner_type == "university":
            reply = (
                "Perfect — I’ve set up your courses.\n\n"
                "Next:\n"
                "- Open a course and start studying, or\n"
                "- When you’re ready, paste a course outline anytime and tell me which course it’s for.\n\n"
                "Example: `Outline for Data Structures: ...`"
            )
        else:
            reply = (
                "All set — I’ve created your courses with outlines.\n\n"
                "Tell me what you want to study first (e.g. `Help me study Data Structures today`)."
            )

        return OnboardingResult(
            reply_text=reply, is_complete=True, created_courses=created_courses_light
        )

    # Fallback: restart prompt if state is weird
    state = default_onboarding_state()
    await save_onboarding_state(db, user_id, state)
    return OnboardingResult(reply_text=_welcome_prompt(), is_complete=False)
