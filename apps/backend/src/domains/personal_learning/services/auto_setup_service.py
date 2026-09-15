"""
Auto-Setup Service — proactive content creation.

When a learner provides their purpose and subjects, the system
automatically prepares everything they need to start learning.

"Autonomous learning is a state where the environment handles
the planning, scheduling, searching, and organising.
The learner simply learns."
"""

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


#: Purposes whose first artefact is a **course**, not a preparation.
#:
#: A preparation is built around a date: it has a target, a readiness score and a plan that
#: counts down to the day. That is the right shape for an exam and the wrong shape for "I want
#: to learn Python", which was nonetheless what the picker's *Learn* option produced — an
#: `ExamPrep` of type `PROJECT`, with a deadline thirty days out that the learner never
#: mentioned, and a landing on `/prepare`. So the one option naming a surface delivered a
#: different one, and no branch of onboarding ever created the course a learner was promised.
#:
#: `course_completion` is here because a learner completing a course needs the course.
#: `exam_prep` and `professional_certification` stay on the preparation path: both are dated by
#: definition, and everything the prep surface does is built around that date.
COURSE_FIRST_PURPOSES = frozenset({"skill_building", "general_learning", "course_completion"})

#: What the learner's stated level means to a course. `Course.difficulty` is an enum, and the
#: onboarding answers are three words, so the mapping is written once here rather than guessed
#: at the call site.
_DIFFICULTY_BY_LEVEL = {
    "beginner": "BEGINNER",
    "intermediate": "INTERMEDIATE",
    "advanced": "ADVANCED",
}


def _primary_subject(profile: Any) -> str | None:
    """What this learner is here to learn, in one line.

    `subjects` first, because a learner who listed subjects chose those words. Falling back to
    `skillName` closes a real hole rather than being tidy: the mobile skill-details screen
    requires a skill name and treats subjects as optional, so "Python" with no subjects was a
    complete answer to the form and an incomplete profile to this service. Auto-setup returned
    `skipped`, nothing was created, `onboardingState` never reached `content_ready`, and the
    progress screen polled a status that could never change. `examName` is included for the same
    reason on the preparation side.
    """
    subjects = profile.subjects or []
    for candidate in (*subjects, profile.skill_name, profile.exam_name):
        text = (candidate or "").strip() if isinstance(candidate, str) else ""
        if text:
            return text
    return None


async def auto_setup_for_learner(*, user_id: str) -> dict[str, Any]:
    """
    Proactively create initial content based on the learner's profile.

    Called once the learner has given a purpose and said what they want to learn. What gets
    created depends on which of those two things they came for: a **course** for the Learn
    purposes, a **preparation** for the dated ones. See `COURSE_FIRST_PURPOSES`.

    Returns a summary of what was created. Every step is individually best-effort — a failed
    outline should not cost the learner their flashcards — so a `completed` status does not
    promise that every key is populated, and `get_onboarding_status` decides what counts as
    usable from what actually exists.
    """
    profile = await repo.get_profile_by_user(user_id)
    if not profile or not profile.purpose:
        logger.info(f"Auto-setup skipped for user {user_id}: no purpose set")
        return {"status": "skipped", "reason": "incomplete_profile"}

    subject = _primary_subject(profile)
    if not subject:
        logger.info(f"Auto-setup skipped for user {user_id}: nothing to learn recorded")
        return {"status": "skipped", "reason": "incomplete_profile"}

    purpose = profile.purpose
    # Downstream helpers still think in terms of a subject list. The resolved subject leads it,
    # so a profile carrying only a skill name behaves exactly like one carrying a subject.
    subjects = [subject, *[s for s in (profile.subjects or []) if s and s != subject]]
    goals = profile.goals_text or ""

    if purpose in COURSE_FIRST_PURPOSES:
        return await _setup_course_first(
            user_id=user_id, profile=profile, subject=subject, goals=goals
        )

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
            #
            # The deck origin is passed so the cards land somewhere visible. They used to be
            # created unfiled, which is the one state the flashcards dashboard cannot show — its
            # deck list joins from `FlashcardDeck`, so a null `deckId` matches no row — so
            # onboarding generated a learner's first cards straight into a place they could not
            # see them.
            flashcards = await _generate_initial_flashcards(
                user_id,
                subject,
                origin_type="prep",
                origin_id=prep.id,
                deck_title=f"{prep.subject} — starter cards",
                deck_subject=prep.subject,
            )
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


async def _setup_course_first(
    *, user_id: str, profile: Any, subject: str, goals: str
) -> dict[str, Any]:
    """Create the learner's first course, outline it, and hang a deck and a plan off it.

    The ordering is the design. The course row is written first and without a model call, so the
    client has something to upload a syllabus to; the outline is generated second and reads
    whatever was uploaded. Reversing those two would mean the one document that authoritatively
    describes the course could never influence it.

    Each step after the course is wrapped on its own. A learner whose outline call fails still
    owns a course they can outline from the course page, which is a recoverable position; losing
    the course as well is not.
    """
    from src.domains.knowledge.services import course_service

    created: dict[str, Any] = {
        "courses": [],
        "modules": 0,
        "flashcards": [],
        "studyPlan": None,
    }

    course = await _ensure_onboarding_course(
        user_id=user_id, profile=profile, subject=subject, goals=goals
    )
    if course is None:
        return {"status": "partial", "created": created, "error": "course_creation_failed"}

    created["courses"].append(course.id)

    try:
        modules = await _generate_course_outline(
            user_id=user_id, course=course, subject=subject, goals=goals, profile=profile
        )
        if modules:
            created["modules"] = await course_service.apply_generated_outline(
                user_id=user_id, course_id=course.id, modules=modules
            )
    except Exception as e:  # noqa: BLE001 - an un-outlined course is still usable
        logger.warning(f"Failed to outline onboarding course for {user_id}: {e}")

    try:
        flashcards = await _generate_initial_flashcards(
            user_id,
            subject,
            origin_type="course",
            origin_id=course.id,
            deck_title=f"{course.title} — starter cards",
            deck_subject=subject,
        )
        created["flashcards"] = [f.id for f in flashcards]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to generate onboarding flashcards for {user_id}: {e}")

    try:
        plan = await _create_course_study_plan(user_id, course, subject, goals)
        if plan:
            created["studyPlan"] = plan.id
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to create onboarding study plan for {user_id}: {e}")

    logger.info(
        f"Auto-setup (course) complete for user {user_id}: "
        f"course {course.id}, {created['modules']} modules, "
        f"{len(created['flashcards'])} flashcards"
    )
    return {"status": "completed", "created": created}


async def _ensure_onboarding_course(*, user_id: str, profile: Any, subject: str, goals: str) -> Any:
    """The course this onboarding is building — reused if it already exists, else created.

    Reuse is what keeps a retry from handing the learner two courses for one answer, and it is
    also how a course created early (so an upload had a target) gets picked up here rather than
    duplicated. A recorded id that no longer resolves — deleted course, or a profile carried
    across environments — falls through to creating a fresh one instead of failing.
    """
    from src.domains.identity.repository import IdentityRepository
    from src.domains.knowledge.repository import knowledge_repo
    from src.domains.knowledge.services import course_service

    recorded_id = getattr(profile, "onboarding_course_id", None)
    if recorded_id:
        existing = await knowledge_repo.find_course_with_modules(recorded_id, user_id)
        if existing is not None:
            return existing
        logger.info(
            "Recorded onboarding course no longer exists; creating a new one",
            extra={"userId": user_id, "courseId": recorded_id},
        )

    user = await IdentityRepository().find_by_id(user_id)
    if user is None:
        logger.warning(f"Cannot create onboarding course: user {user_id} not found")
        return None

    try:
        course = await course_service.create_course(
            user=user,
            data={
                "title": subject,
                "description": goals or f"Learning {subject} with Maigie.",
                "difficulty": _DIFFICULTY_BY_LEVEL.get(
                    (getattr(profile, "current_level", None) or "").lower(), "BEGINNER"
                ),
                # The learner's own words, kept so the course page can show what it was built
                # from and so a regenerated outline answers the same brief.
                "sourcePrompt": goals or f"Learn {subject}",
                "isAIGenerated": True,
            },
        )
    except Exception as e:  # noqa: BLE001 - includes the free-tier course limit
        logger.warning(f"Failed to create onboarding course for {user_id}: {e}")
        return None

    await repo.update_profile(user_id, {"onboardingCourseId": course.id})
    return course


async def _generate_course_outline(
    *, user_id: str, course: Any, subject: str, goals: str, profile: Any
) -> list[dict[str, Any]]:
    """Design the curriculum, grounded in whatever the learner uploaded.

    Runs through `llm_resilient` as `onboarding_auto_setup`, which is in `UNCHARGED_OPERATIONS`:
    a learner's first course is not billed against an allowance they have not started using.

    An empty list is a normal return, not an exception. The caller keeps the course, and the
    learner can generate an outline from the course page — a first course with no modules is a
    poor start but a recoverable one.
    """
    from src.domains.knowledge.services import course_material_context, lesson_service

    from .llm_resilient import generate_content_json

    context = await course_material_context.for_course(course_id=course.id, user_id=user_id)
    source_material = course_material_context.as_prompt_material(context)

    brief = goals.strip() or f"I want to learn {subject} from where I am now."
    payload = await generate_content_json(
        lesson_service.build_outline_prompt(
            title=course.title or subject,
            brief=brief,
            level=(getattr(profile, "current_level", None) or "").capitalize() or None,
            source_material=source_material,
        ),
        max_tokens=4096,
        fallback={},
        user_id=user_id,
        operation="onboarding_auto_setup",
    )
    parsed = lesson_service.parse_outline(payload)
    modules = parsed.get("modules") or []
    if not modules:
        logger.warning(
            "Onboarding outline generation returned nothing usable",
            extra={"userId": user_id, "courseId": course.id, "grounded": bool(source_material)},
        )
    return modules


async def _create_course_study_plan(user_id: str, course: Any, subject: str, goals: str) -> Any:
    """A first study plan for the course, or None.

    Linked through `courseIds`, which `generate_plan` resolves and validates ownership for before
    any model call. There is no deadline to work back from here — nobody gave one — so the plan
    gets the same thirty-day horizon the preparation path uses, which is a shape for the first
    few weeks rather than a claim about when the learner will be finished.
    """
    from . import study_plan_service

    deadline = datetime.now(UTC) + timedelta(days=30)
    return await study_plan_service.generate_plan(
        user_id=user_id,
        data={
            "title": f"Study Plan: {course.title or subject}",
            "goalDescription": goals or f"Learn {subject}",
            "deadline": deadline.isoformat(),
            "courseIds": [course.id],
        },
    )


async def _create_preparation(user_id: str, purpose: str, subjects: list[str], goals: str) -> Any:
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
    default_deadline = datetime.now(UTC) + timedelta(days=30)

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


async def _extract_topics(user_id: str, prep_id: str, subjects: list[str], goals: str) -> list[Any]:
    """Extract topics using AI from the subject matter."""
    from . import exam_prep_service

    try:
        topics = await exam_prep_service.extract_topics(user_id=user_id, prep_id=prep_id)
        return topics
    except Exception as e:
        logger.warning(f"Failed to auto-extract topics: {e}")
        return []


async def _generate_initial_flashcards(
    user_id: str,
    subject: str,
    *,
    origin_type: str | None = None,
    origin_id: str | None = None,
    deck_title: str | None = None,
    deck_subject: str | None = None,
) -> list[Any]:
    """Generate starter flashcards for what the learner is about to study.

    Filed into a deck for whatever onboarding built — a preparation on the dated path, a course
    on the Learn path. Both origins already exist on `FlashcardDeck` (`prepId`, and
    `DECK_ORIGIN_COURSE` for courses), so this takes the origin as an argument rather than
    knowing which of the two flows it is serving.

    The origin is optional so the function still works when the step before it failed; in that
    case the cards are created unfiled, which is worse than a deck but better than dropping the
    learner's first cards entirely. The backfill script picks those up.
    """
    import json

    # Through the chokepoint, so the exemption is honoured by the same machinery that would
    # otherwise charge. `onboarding_auto_setup` is in `llm_resilient.UNCHARGED_OPERATIONS`, so this is
    # neither charged nor gated — and it is far below the quality threshold, so it is not degraded
    # either, which is the pairing Decision P's threshold exists to get without a second list.
    from src.domains.personal_learning.services.llm_resilient import generate_content

    from . import flashcard_service

    if not subject:
        return []

    prompt = (
        f"Create 5 fundamental flashcards for someone beginning to study {subject}.\n"
        f"These should cover the most basic, essential concepts a beginner needs to know.\n\n"
        f"Return a JSON array of objects with 'front' (question) and 'back' (answer).\n"
        f"Keep answers concise (1-2 sentences).\n"
        f"Return ONLY the JSON array."
    )

    try:
        response = await generate_content(
            prompt, max_tokens=1500, user_id=user_id, operation="onboarding_auto_setup"
        )
        cards_data = json.loads(response)
    except Exception as e:
        logger.warning(f"Failed to generate initial flashcards: {e}")
        return []

    # Resolved once, after generation succeeded, so a failed model call does not leave an
    # empty deck behind for a learner who has no cards.
    deck_id: str | None = None
    if origin_type and origin_id:
        try:
            deck_id = await flashcard_service.ensure_deck_for_origin(
                user_id=user_id,
                origin_type=origin_type,
                origin_id=origin_id,
                title=deck_title or f"{subject} — starter cards",
                description="The first cards Maigie made for you when you started.",
                subject=deck_subject or subject,
            )
        except Exception as e:
            # Not fatal. Unfiled cards are recoverable by the backfill; losing the
            # learner's first cards is not.
            logger.warning(f"Could not resolve starter deck, cards will be unfiled: {e}")

    created_cards = []
    for card in cards_data:
        if isinstance(card, dict) and "front" in card and "back" in card:
            try:
                flashcard = await flashcard_service.create_flashcard(
                    user_id=user_id,
                    data={
                        "front": card["front"],
                        "back": card["back"],
                        "deckId": deck_id,
                        "sourceType": "auto_setup",
                        # The origin id when there is one, so the cards point at an entity
                        # rather than at a subject string that nothing can resolve.
                        "sourceId": origin_id or subject,
                    },
                )
                created_cards.append(flashcard)
            except Exception as e:
                logger.warning(f"Failed to create flashcard: {e}")

    return created_cards


async def _create_study_plan(user_id: str, prep: Any, topics: list[Any], goals: str) -> Any:
    """Create a study plan distributing topics across available days."""
    from . import study_plan_service

    if not topics:
        return None

    try:
        deadline = prep.exam_date if prep.exam_date else (datetime.now(UTC) + timedelta(days=30))
        plan = await study_plan_service.generate_plan(
            user_id=user_id,
            data={
                "title": f"Study Plan: {prep.subject}",
                "goalDescription": goals or f"Master {prep.subject}",
                "deadline": (
                    deadline.isoformat() if isinstance(deadline, datetime) else str(deadline)
                ),
                "prepId": prep.id,
            },
        )
        return plan
    except Exception as e:
        logger.warning(f"Failed to auto-create study plan: {e}")
        return None
