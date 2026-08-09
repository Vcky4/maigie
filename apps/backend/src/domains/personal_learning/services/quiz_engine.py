"""
Quiz Engine — generates, serves, and scores practice quizzes.

Supports multiple modes: FULL_PRACTICE (all topics), WEAK_AREAS (mastery < 70),
TOPIC_FOCUS (single topic), and QUICK_REVIEW.
"""

import logging
import time
from datetime import UTC, datetime, timezone
from typing import Any

from src.shared.exceptions import MaigieError, NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo
from . import prep_adaptive

logger = logging.getLogger(__name__)

# A multiple-choice question needs at least two options to be a choice at all.
_MIN_OPTIONS = 2

# Recognised difficulty labels. Anything else the generator returns is dropped
# rather than stored, so the column cannot become a free-text dumping ground.
_DIFFICULTIES = ("EASY", "MEDIUM", "HARD")

# Provenance. Always set by the server: a generator's claim about where its own
# output came from is not evidence.
QUESTION_SOURCE_AI = "AI_GENERATED"
QUESTION_SOURCE_PAST_PAPER = "PAST_PAPER"

# An exam tip is capped rather than trusted to be "one sentence".
_MAX_EXAM_TIP_CHARS = 500

# A hint is capped for the same reason, and more tightly: a long hint is an
# explanation wearing a different hat.
_MAX_HINT_CHARS = 300

# Hint levels a learner can ask for, in order.
HINT_LEVEL_NUDGE = 1
HINT_LEVEL_NARROW = 2
MAX_HINT_LEVEL = HINT_LEVEL_NARROW

# Modes played under examination conditions: no hints, and no feedback of any kind
# until the session is complete.
#
# This is a deliberate, narrow exception to the per-question disclosure boundary,
# not a hole in it. Learning in small steps and rehearsing an exam are opposite
# requirements — one teaches as you go, the other measures you under pressure — and
# a simulation that tells you the answer after every question simulates nothing.
# The guarantee that matters is unchanged: a learner still never sees an answer to a
# question they have not committed to.
EXAM_CONDITION_MODES = ("PAST_PAPER_SIM",)


def defers_feedback(mode: str | None) -> bool:
    """Whether this mode withholds all feedback until the session completes."""
    return (mode or "").upper() in EXAM_CONDITION_MODES


# A topic at or below this score is reported as a weak area on completion.
_WEAK_AREA_THRESHOLD = 70


async def start_quiz(
    *,
    user_id: str,
    prep_id: str,
    mode: str,
    topic_id: str | None = None,
    question_count: int | None = None,
) -> Any:
    """
    Start a quiz session and generate questions.

    Req 4.5: FULL_PRACTICE — cover all topics
    Req 4.6: WEAK_AREAS — topics with mastery < 70
    Req 4.7: TOPIC_FOCUS — single specified topic
    """
    from . import feature_tier_service
    from .llm_resilient import generate_content_json

    # --- Commercial gate: check mode access ---
    if mode in ("PAST_PAPER_SIM", "ADAPTIVE"):
        cap_result = await feature_tier_service.check_capability(
            user_id, "quiz_modes", requested_value=mode
        )
        if not cap_result.allowed:
            from fastapi import HTTPException

            # Built through the published model rather than an inline dict, so
            # the payload the client renders its upgrade path from cannot drift
            # from the declared 403 schema.
            detail = models.UpgradeRequiredDetail(
                upgrade_required=True,
                reason=cap_result.reason,
                capability=cap_result.capability,
                upgrade_url=cap_result.upgrade_url,
                trial_available=cap_result.trial_available,
                upgrade_value=cap_result.upgrade_value,
            )
            raise HTTPException(status_code=403, detail=detail.model_dump(by_alias=True))
        # Record PLUS feature usage
        await feature_tier_service.get_quality_tier(user_id)  # side-effect: validates tier
        from . import trial_service

        await trial_service.record_plus_feature_used(user_id, "quiz_modes")

    # Verify prep exists and belongs to user
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    # Get topics based on mode
    all_topics = await repo.list_prep_topics(prep_id)
    if not all_topics:
        # A bare ValueError here reached clients as a generic 500 via the
        # catch-all handler, hiding an actionable next step.
        raise MaigieError(
            "This preparation has no topics yet. Extract topics before practising.",
            status_code=409,
            code="PREP_TOPICS_REQUIRED",
        )

    if mode == "FULL_PRACTICE":
        target_topics = all_topics
    elif mode == "WEAK_AREAS":
        target_topics = [t for t in all_topics if t.mastery_score < 70.0]
        if not target_topics:
            target_topics = all_topics  # Fallback to all if none are weak
    elif mode == "TOPIC_FOCUS":
        if not topic_id:
            raise MaigieError(
                "A topic is required for topic-focused practice.",
                status_code=400,
                code="PREP_TOPIC_REQUIRED",
            )
        target_topics = [t for t in all_topics if t.id == topic_id]
        if not target_topics:
            raise NotFoundError("PrepTopic", topic_id)
    elif mode in ("ADAPTIVE", "PAST_PAPER_SIM"):
        # Both work across the whole preparation. ADAPTIVE then narrows by
        # competence below; PAST_PAPER_SIM deliberately covers everything, because
        # an exam does not visit only your weak topics.
        target_topics = all_topics
    else:
        # QUICK_REVIEW — mix of topics, fewer questions
        target_topics = all_topics[:5]

    # Determine question count
    count = question_count or min(len(target_topics) * 2, 20)

    # PAST_PAPER_SIM is grounded in the learner's *own* uploaded material and is
    # scoped to that learner. Nobody else's documents, and no third-party past
    # papers — which would be someone else's copyright to license, not ours to use.
    source_excerpt: str | None = None
    if mode == "PAST_PAPER_SIM":
        source_excerpt = await _own_material_excerpt(user_id=user_id, prep_id=prep_id)
        if not source_excerpt:
            raise MaigieError(
                "Upload some course material first — exam simulation is built from "
                "your own documents.",
                status_code=409,
                code="PREP_MATERIAL_REQUIRED",
            )

    # ADAPTIVE composes a plan from what practice has revealed: which topics, at
    # which difficulty, ordered to ramp gently. Before this, the mode was billed as
    # a Plus feature and behaved exactly like the free quick-review path.
    adaptive_plan: list[Any] = []
    if mode == "ADAPTIVE":
        adaptive_plan, _competence = await prep_adaptive.load_plan(
            user_id=user_id, topics=target_topics, count=count
        )
        if adaptive_plan:
            planned_ids = {slot.topic_id for slot in adaptive_plan}
            target_topics = [t for t in target_topics if t.id in planned_ids] or target_topics

    # Create the session before generating, so an attempt is never lost, and mark
    # it GENERATING rather than IN_PROGRESS (Decision H). A request that dies
    # mid-generation would otherwise leave a 0-question session sitting in
    # IN_PROGRESS forever, indistinguishable from one that is still working.
    quiz_session = await repo.create_quiz_session(
        {
            "userId": user_id,
            "prepId": prep_id,
            "mode": mode,
            "topicId": topic_id,
            "status": "GENERATING",
            "totalQuestions": count,
        }
    )

    # ADAPTIVE draws on the bank before generating anything. This is the first real
    # payoff of promoting questions out of the session that created them: a question
    # written last week at the right difficulty beats a fresh one, because it is
    # already validated and it carries its own answer history. Done *before*
    # generation so we do not pay for questions we would then discard.
    reused = 0
    if adaptive_plan:
        reused = await _fill_from_bank(
            prep_id=prep_id, quiz_session_id=quiz_session.id, plan=adaptive_plan
        )

    remaining = max(0, count - reused)

    # Generate questions via LLM.
    #
    # Topics are numbered and the model is asked for the number rather than the
    # title. Attribution used to be a lowercased match on an LLM-returned title,
    # so any paraphrase silently produced `prepTopicId = None`, which broke both
    # the topic breakdown and the per-topic mastery updates that readiness is
    # derived from. Numbering also keeps internal topic ids out of the prompt.
    topics_text = "\n".join(
        f"{index}. {topic.title}: {topic.description or ''}"
        for index, topic in enumerate(target_topics, start=1)
    )
    # Exam simulation is grounded in the learner's own material rather than invented
    # from the topic titles, so the questions test what they were actually given.
    grounding = ""
    if source_excerpt:
        grounding = (
            "Base every question strictly on this source material, which the learner "
            "uploaded themselves. Do not introduce facts that are not present in it.\n"
            f"--- SOURCE MATERIAL ---\n{source_excerpt}\n--- END SOURCE MATERIAL ---\n\n"
            "Write questions in the style of a written examination: no hints in the "
            "wording, and a spread of difficulty across the paper.\n\n"
        )

    prompt = (
        f"{grounding}"
        f"Generate {remaining} quiz questions for these numbered topics:\n{topics_text}\n\n"
        f"Return a JSON array of question objects with:\n"
        f"- 'topicNumber': the number of the topic this tests, from the list above\n"
        f"- 'questionText': the question\n"
        f"- 'questionType': 'MULTIPLE_CHOICE'\n"
        f"- 'options': array of 4 options (strings)\n"
        f"- 'correctAnswer': the correct option (must match one of the options exactly)\n"
        f"- 'explanation': brief explanation of why the answer is correct\n"
        f"- 'difficulty': one of EASY, MEDIUM, HARD\n"
        f"- 'examTip': one sentence on how to approach this kind of question\n"
        f"- 'hint': one sentence pointing at the concept or method needed, which "
        f"must NOT reveal, restate or paraphrase the correct answer\n\n"
        f"Return ONLY the JSON array."
    )

    # Timed so the sync-versus-queued decision (Decision H) can be revisited from
    # measurements rather than from opinion. The provider is chosen per user by
    # llm_resilient, so this covers whichever of Gemini/OpenAI/Anthropic ran.
    started = time.monotonic()
    questions_data: Any = []
    if remaining > 0:
        try:
            questions_data = await generate_content_json(
                prompt, max_tokens=8000, timeout_s=60, fallback=[], user_id=user_id
            )
        except Exception as e:
            logger.warning(f"Failed to generate quiz questions for prep {prep_id}: {e}")
            questions_data = []
    generation_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Quiz generation finished",
        extra={
            "prep_id": prep_id,
            "quiz_id": quiz_session.id,
            "mode": mode,
            "requested": count,
            "generation_ms": generation_ms,
        },
    )

    if not isinstance(questions_data, list):
        logger.warning(
            "Quiz generation returned a non-list payload",
            extra={"prep_id": prep_id, "quiz_id": quiz_session.id},
        )
        questions_data = []

    # Persist only questions that can actually be scored — see _usable_question.
    # Generation fills whatever the bank could not supply.
    created = reused
    rejected = 0
    unattributed = 0
    for candidate in questions_data:
        if created >= count:
            break
        normalized = _usable_question(candidate)
        if normalized is None:
            rejected += 1
            continue

        matched_topic_id = _resolve_topic_id(candidate, target_topics)
        if matched_topic_id is None:
            unattributed += 1

        # The question is banked against the preparation, then linked to this
        # session at this position. The bank outlives the session, so the question
        # remains browsable and reusable afterwards.
        question = await repo.create_prep_question(
            {
                "prepId": prep_id,
                "prepTopicId": matched_topic_id,
                "questionText": normalized["question_text"],
                "questionType": normalized["question_type"],
                "options": normalized["options"],
                "correctAnswer": normalized["correct_answer"],
                "explanation": normalized["explanation"],
                "difficulty": normalized["difficulty"],
                "examTip": normalized["exam_tip"],
                "hintNudge": normalized["hint_nudge"],
                # Set here, not taken from the model. A generator asked to report
                # its own provenance is not a source of truth about it, and
                # `sourceYear` stays null because a generated question has no year.
                "source": QUESTION_SOURCE_AI,
            }
        )
        await repo.attach_question_to_session(
            quiz_session_id=quiz_session.id,
            prep_question_id=question.id,
            order_index=created,
        )
        created += 1

    if rejected or unattributed:
        # Unattributed questions are still scorable, but they update no topic's
        # mastery, so they quietly weaken readiness. Worth seeing in logs.
        logger.warning(
            "Generated quiz questions were discarded or unattributed",
            extra={
                "prep_id": prep_id,
                "quiz_id": quiz_session.id,
                "rejected": rejected,
                "unattributed": unattributed,
            },
        )

    if created == 0:
        # Decision F: a session with no usable questions is a failure, not a
        # quiz. The row is kept as FAILED so the attempt stays visible for
        # support, and the caller is told, rather than being handed a 201 with an
        # empty `questions` array that no client can render.
        await repo.update_quiz_session(quiz_session.id, {"status": "FAILED", "totalQuestions": 0})
        logger.error(
            "Quiz generation produced no usable questions",
            extra={
                "prep_id": prep_id,
                "quiz_id": quiz_session.id,
                "mode": mode,
                "returned": len(questions_data),
            },
        )
        raise MaigieError(
            "We could not generate questions for this practice session. Please try again.",
            status_code=503,
            code="QUIZ_GENERATION_FAILED",
        )

    # Generation succeeded: the session is now playable. Partial generation still
    # makes a usable quiz, so report the real number and give the score an honest
    # denominator.
    await repo.update_quiz_session(
        quiz_session.id, {"status": "IN_PROGRESS", "totalQuestions": created}
    )

    session = await repo.get_quiz_session(quiz_session.id, user_id)
    questions = await repo.list_quiz_questions(quiz_session.id)
    # A new quiz has no answers yet, and being IN_PROGRESS it carries no answer
    # key either.
    return _build_quiz_response(session, questions, [])


def _usable_question(candidate: Any) -> dict[str, Any] | None:
    """Normalize one generated question, or return `None` if it cannot be scored.

    Rejecting is better than repairing here, because each rejected case produces
    a score the learner would be right to dispute:

    - No `questionText`: nothing to ask.
    - Empty `correctAnswer`: the column is NOT NULL and this previously
      defaulted to `""`, so the question could never be answered correctly and
      counted against the learner regardless of what they picked.
    - Multiple choice whose `correctAnswer` is not one of its own `options`: the
      right answer is not on offer, so the question can only ever be wrong.
    """
    if not isinstance(candidate, dict):
        return None

    question_text = str(candidate.get("questionText") or "").strip()
    correct_answer = str(candidate.get("correctAnswer") or "").strip()
    if not question_text or not correct_answer:
        return None

    question_type = str(candidate.get("questionType") or "MULTIPLE_CHOICE").strip()

    raw_options = candidate.get("options")
    options: list[str] | None = None
    if isinstance(raw_options, list):
        cleaned = [str(option).strip() for option in raw_options if str(option).strip()]
        options = cleaned or None

    if question_type == "MULTIPLE_CHOICE":
        if not options or len(options) < _MIN_OPTIONS:
            return None
        if not any(option.lower() == correct_answer.lower() for option in options):
            return None

    explanation = candidate.get("explanation")

    # Metadata is normalized, never trusted. An unrecognised difficulty becomes
    # None rather than being stored, because a badge reading "quite hard" or
    # "Level 4" is worse than no badge: the client would have to render it.
    difficulty = str(candidate.get("difficulty") or "").strip().upper()
    if difficulty not in _DIFFICULTIES:
        difficulty = None

    exam_tip = candidate.get("examTip")
    exam_tip = str(exam_tip).strip()[:_MAX_EXAM_TIP_CHARS] or None if exam_tip else None

    hint = _usable_hint(candidate.get("hint"), correct_answer=correct_answer)

    return {
        "question_text": question_text,
        "question_type": question_type,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": str(explanation).strip() or None if explanation is not None else None,
        "difficulty": difficulty,
        "exam_tip": exam_tip,
        "hint_nudge": hint,
    }


def _usable_hint(raw: Any, *, correct_answer: str) -> str | None:
    """Normalize a hint, or drop it if it gives the answer away.

    A hint that contains the correct answer is not a hint, it is the answer key
    with a different label — and it would defeat withholding the key at all. Asking
    the model not to do it is not the same as it not doing it, so this checks.

    Dropped rather than repaired: no hint is a perfectly acceptable state, and
    editing a model's hint to remove the answer risks leaving a sentence that still
    implies it.
    """
    if not raw:
        return None

    hint = str(raw).strip()[:_MAX_HINT_CHARS].strip()
    if not hint:
        return None

    answer = correct_answer.strip().lower()
    if answer and answer in hint.lower():
        logger.warning("Discarded a generated hint containing the correct answer")
        return None

    return hint


def _resolve_topic_id(candidate: Any, target_topics: list[Any]) -> str | None:
    """Attribute a generated question to one of the requested topics.

    Tried in order of reliability: the topic number the prompt asked for, then a
    title match for a model that ignored the instruction. With a single target
    topic the attribution is unambiguous regardless of what came back.

    Returns `None` when the question cannot be attributed. Such a question is
    still scorable but updates no topic's mastery, so callers should count them.
    """
    if not target_topics:
        return None
    if len(target_topics) == 1:
        return target_topics[0].id
    if not isinstance(candidate, dict):
        return None

    try:
        index = int(candidate.get("topicNumber")) - 1
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(target_topics):
        return target_topics[index].id

    title = str(candidate.get("topicTitle") or "").strip().lower()
    if title:
        for topic in target_topics:
            if topic.title.strip().lower() == title:
                return topic.id

    return None


async def submit_answer(*, user_id: str, quiz_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Submit an answer to a quiz question.

    Req 4.8: Evaluate correctness, track time, update topic mastery.

    Ownership is checked twice: the session must belong to `user_id`, and the
    question must belong to that session. Both matter, because the response
    discloses the answer key for the question submitted.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    # Accept both snake_case (from model_dump) and camelCase (defensive)
    question_id = data.get("question_id") or data.get("questionId")
    user_answer = data.get("user_answer") or data.get("userAnswer")
    time_taken = data.get("time_taken_seconds") or data.get("timeTakenSeconds")

    if not question_id or user_answer is None:
        # Unreachable through the route, whose request model requires both, but
        # a bare ValueError here would surface as a 500 rather than a 400.
        raise MaigieError(
            "questionId and userAnswer are required.",
            status_code=400,
            code="QUIZ_ANSWER_INVALID",
        )

    if quiz.status == "COMPLETED":
        raise MaigieError(
            "This practice session is already complete.",
            status_code=409,
            code="QUIZ_ALREADY_COMPLETED",
        )
    if quiz.status == "GENERATING":
        raise MaigieError(
            "This practice session is still being prepared. Try again in a moment.",
            status_code=409,
            code="QUIZ_GENERATING",
        )
    if quiz.status == "FAILED":
        raise MaigieError(
            "This practice session could not be generated, so it cannot be answered.",
            status_code=409,
            code="QUIZ_GENERATION_FAILED",
        )

    # Scoped to this session. The lookup was previously by question id alone, so
    # any question id — including one from another learner's session — could be
    # answered here and its answer key read back out of the response.
    question = await repo.find_quiz_question(question_id, quiz_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    # Answering is idempotent. Resubmitting replays the stored result instead of
    # scoring again, so the key disclosed by the first submission cannot be fed
    # back to raise the score, and a client retry is harmless.
    existing = await repo.find_quiz_answer(quiz_id, question_id)
    if existing:
        return _answer_result(
            question_id=question_id,
            question=question,
            is_correct=existing.is_correct,
            mode=quiz.mode,
            already_answered=True,
        )

    is_correct = _check_answer_correctness(
        user_answer=user_answer,
        correct_answer=question.correct_answer,
        options=question.options,
    )

    # Record the answer (critical — must persist before responding)
    await repo.create_quiz_answer(
        {
            "quizSessionId": quiz_id,
            "questionId": question_id,
            "userAnswer": user_answer,
            "isCorrect": is_correct,
            "timeTakenSeconds": time_taken,
        }
    )

    # Recomputed from persisted answers rather than incremented, so the count
    # cannot drift from the answers or exceed the number of questions asked.
    await repo.update_quiz_session(
        quiz_id, {"correctCount": await repo.count_correct_quiz_answers(quiz_id)}
    )

    # Lifetime statistics on the banked question, across every session that has
    # ever asked it. Incremented in SQL so concurrent answers cannot lose a count.
    await repo.record_question_attempt(question_id, correct=is_correct)

    # Keep the evidence, not just the verdict, so a conclusion about a learner can
    # be revisited later rather than being a number with no reasoning behind it.
    #
    # Awaited *before* the mastery recompute below, which reads observations: the
    # newest answer has to be visible to it, or every estimate lags by one question.
    await _record_observation(
        user_id=user_id,
        quiz=quiz,
        question=question,
        is_correct=is_correct,
        time_taken=time_taken,
    )

    # Update topic mastery — fire-and-forget to avoid blocking the response.
    # Any failure is logged; user experience is not affected.
    if question.prep_topic_id:
        import asyncio

        asyncio.create_task(_update_topic_mastery_safe(question.prep_topic_id, user_id=user_id))

    return _answer_result(
        question_id=question_id,
        question=question,
        is_correct=is_correct,
        mode=quiz.mode,
        already_answered=False,
    )


def _answer_result(
    *,
    question_id: str,
    question: Any,
    is_correct: bool,
    mode: str | None,
    already_answered: bool,
) -> dict[str, Any]:
    """Build the answer result, respecting examination conditions.

    Under `PAST_PAPER_SIM` the answer is recorded but nothing is disclosed — not the
    key, not the explanation, not even whether it was right. A simulation that marks
    each question as you go is not simulating an exam.
    """
    if defers_feedback(mode):
        return {
            "questionId": question_id,
            "isCorrect": None,
            "correctAnswer": None,
            "explanation": None,
            "alreadyAnswered": already_answered,
            "feedbackDeferred": True,
        }

    return {
        "questionId": question_id,
        "isCorrect": is_correct,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "alreadyAnswered": already_answered,
        "feedbackDeferred": False,
    }


# How much of the learner's own material to ground an exam simulation in. The same
# cap topic extraction already uses, for the same reason: prompts have limits and a
# textbook chapter does not fit in one.
_MATERIAL_EXCERPT_CHARS = 5000


async def _own_material_excerpt(*, user_id: str, prep_id: str) -> str | None:
    """Text from the learner's own uploaded material for this preparation.

    Ownership is verified on the preparation before any material is read, so one
    learner's documents can never ground another learner's exam simulation.

    Returns `None` when there is nothing to work from — a preparation may have
    materials whose text was never extracted, which is different from having none.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    materials = await repo.list_prep_materials(prep_id)

    excerpts: list[str] = []
    budget = _MATERIAL_EXCERPT_CHARS
    for material in materials:
        text = (material.extracted_text or "").strip()
        if not text:
            continue
        excerpts.append(text[:budget])
        budget -= len(excerpts[-1])
        if budget <= 0:
            break

    return "\n\n".join(excerpts) if excerpts else None


async def _fill_from_bank(*, prep_id: str, quiz_session_id: str, plan: list[Any]) -> int:
    """Satisfy as many planned slots as possible from the existing bank.

    Returns how many questions were attached. Each banked question is used at most
    once per session, which the session link's unique constraint would enforce
    anyway — this just avoids relying on an error for control flow.

    A slot the bank cannot fill is left for generation rather than substituted with
    the wrong difficulty: asking a HARD question because no MEDIUM one exists would
    quietly defeat the point of planning.
    """
    used: list[str] = []

    for slot in plan:
        candidates = await repo.list_bank_questions_for_reuse(
            prep_id=prep_id,
            topic_id=slot.topic_id,
            difficulty=slot.difficulty,
            exclude_ids=used,
            take=1,
        )
        if not candidates:
            continue

        question = candidates[0]
        await repo.attach_question_to_session(
            quiz_session_id=quiz_session_id,
            prep_question_id=question.id,
            order_index=len(used),
        )
        used.append(question.id)

    if used:
        logger.info(
            "Adaptive session reused banked questions",
            extra={
                "prep_id": prep_id,
                "quiz_id": quiz_session_id,
                "reused": len(used),
                "planned": len(plan),
            },
        )
    return len(used)


async def _record_observation(
    *,
    user_id: str,
    quiz: Any,
    question: Any,
    is_correct: bool,
    time_taken: Any,
) -> None:
    """Append what this answer revealed. Failure must not fail the answer.

    An observation is valuable but it is not the learner's score. If writing it
    fails, the answer has still been recorded and the session continues; losing one
    row of evidence is a far smaller harm than rejecting a submitted answer.
    """
    try:
        hint_count = 0
        link = await repo.find_session_question_link(
            quiz_session_id=quiz.id, prep_question_id=question.id
        )
        if link is not None:
            hint_count = link.hint_count or 0

        response_ms: int | None = None
        if time_taken is not None:
            try:
                response_ms = max(0, int(time_taken) * 1000)
            except (TypeError, ValueError):
                response_ms = None

        await repo.record_practice_observation(
            {
                "userId": user_id,
                "prepId": getattr(question, "prep_id", None) or quiz.prep_id,
                "prepTopicId": question.prep_topic_id,
                "prepQuestionId": question.id,
                "quizSessionId": quiz.id,
                "isCorrect": is_correct,
                "responseMs": response_ms,
                "hintUsed": hint_count > 0,
                "hintCount": hint_count,
                # Copied now, because difficulty may be recalibrated later and this
                # observation should record what was true at the time.
                "difficulty": getattr(question, "difficulty", None),
                "observedAt": datetime.now(UTC),
            }
        )
    except Exception:
        logger.exception(
            "Failed to record practice observation",
            extra={"quiz_id": quiz.id, "question_id": question.id},
        )


async def request_hint(
    *, user_id: str, quiz_id: str, question_id: str, level: int = HINT_LEVEL_NUDGE
) -> dict[str, Any]:
    """Give the learner a hint, because they asked for one.

    Hints are pulled, never pushed:

        An answer given too quickly prevents learning.
        A hint at the right moment creates breakthrough.
        -- content/intelligence/ch22-the-nature-of-intelligence.mdx

    Two levels. `NUDGE` points at the concept. `NARROW` additionally eliminates one
    wrong multiple-choice option — still a real choice, and computed
    deterministically so asking twice gives the same answer rather than gradually
    eliminating everything.

    Taking a hint is recorded, and it is **not** a penalty. It marks the question as
    sitting at the edge of what the learner can currently do, which is the most
    useful thing practice can tell us and where the next question should aim.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    if quiz.status != "IN_PROGRESS":
        raise MaigieError(
            "Hints are only available while a practice session is in progress.",
            status_code=409,
            code="QUIZ_NOT_IN_PROGRESS",
        )

    if defers_feedback(quiz.mode):
        raise MaigieError(
            "This is an exam simulation — hints are not available.",
            status_code=409,
            code="QUIZ_EXAM_CONDITIONS",
        )

    # Scoped through the session link, exactly as answering is.
    question = await repo.find_quiz_question(question_id, quiz_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    # A hint after answering is pointless: the key has already been disclosed. More
    # importantly, allowing it would let hint counts be run up after the fact,
    # corrupting the signal rather than recording it.
    existing = await repo.find_quiz_answer(quiz_id, question_id)
    if existing:
        raise MaigieError(
            "This question has already been answered.",
            status_code=409,
            code="QUESTION_ALREADY_ANSWERED",
        )

    requested = max(HINT_LEVEL_NUDGE, min(int(level), MAX_HINT_LEVEL))

    nudge = getattr(question, "hint_nudge", None)
    eliminated: str | None = None
    if requested >= HINT_LEVEL_NARROW:
        eliminated = _eliminable_option(question)

    # Counted once per request, in SQL, so concurrent requests cannot both read the
    # same starting value.
    hint_count = await repo.increment_session_question_hints(
        quiz_session_id=quiz_id, prep_question_id=question_id
    )

    return {
        "questionId": question_id,
        "level": requested,
        "nudge": nudge,
        "eliminatedOption": eliminated,
        "hintCount": hint_count,
        # Honest about having nothing useful, rather than returning a hint-shaped
        # object with no hint in it.
        "hintAvailable": bool(nudge) or eliminated is not None,
    }


def _eliminable_option(question: Any) -> str | None:
    """One wrong multiple-choice option, chosen deterministically.

    Deterministic so that repeated requests eliminate the *same* option rather than
    working through them all — otherwise level 2 becomes "show me the answer" after
    enough taps.

    Returns `None` when eliminating would leave no real choice, which is the case
    for short answers and for two-option questions.
    """
    options = question.options if isinstance(question.options, list) else None
    if not options or len(options) <= 2:
        return None

    answer = (question.correct_answer or "").strip().lower()
    for option in options:
        if str(option).strip().lower() != answer:
            return str(option)
    return None


async def _update_topic_mastery_safe(topic_id: str, *, user_id: str) -> None:
    """Wrap _update_topic_mastery with error handling for fire-and-forget use."""
    try:
        await _update_topic_mastery(topic_id, user_id=user_id)
    except Exception as e:
        logger.warning(f"Background mastery update failed for topic {topic_id}: {e}")


async def complete_quiz(
    *, user_id: str, quiz_id: str, duration_seconds: int | None = None
) -> dict[str, Any]:
    """
    Complete a quiz session.

    Req 4.9: Return score summary with per-topic breakdown and weak areas.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    if quiz.status == "FAILED":
        raise MaigieError(
            "This practice session could not be generated, so it cannot be completed.",
            status_code=409,
            code="QUIZ_GENERATION_FAILED",
        )
    if quiz.status == "GENERATING":
        raise MaigieError(
            "This practice session is still being prepared.",
            status_code=409,
            code="QUIZ_GENERATING",
        )

    answers = await repo.list_quiz_answers(quiz_id)

    if quiz.status == "COMPLETED":
        # Completing twice must not re-record the activity or re-check
        # milestones, so an already-completed session returns its stored result.
        topic_breakdown = await _compute_topic_breakdown(quiz_id, answers)
        weak_areas = [
            t["title"] for t in topic_breakdown if t.get("score", 0) < _WEAK_AREA_THRESHOLD
        ]
        return {
            "quizId": quiz_id,
            "totalQuestions": quiz.total_questions,
            "correctCount": quiz.correct_count,
            "scorePercentage": quiz.score_percentage or 0.0,
            "topicBreakdown": topic_breakdown,
            "weakAreas": weak_areas,
            "suggestedNextStep": _suggest_next_step(weak_areas),
        }

    now = datetime.now(UTC)
    total = quiz.total_questions or 0
    # Derived from persisted answers, then clamped to the questions actually
    # asked. The denominator is no longer forced to 1 for a question-less
    # session, which reported a real-looking 0% for a quiz that asked nothing.
    correct = await repo.count_correct_quiz_answers(quiz_id)
    if total > 0:
        correct = min(correct, total)
    score_pct = (correct / total) * 100 if total > 0 else 0.0

    # Compute duration server-side if the client did not provide one.
    if duration_seconds is None and quiz.created_at:
        started_at = quiz.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        duration_seconds = int((now - started_at).total_seconds())

    # Update quiz session. `correctCount` is written from the recomputed value so
    # the persisted score and the returned summary cannot disagree.
    await repo.update_quiz_session(
        quiz_id,
        {
            "status": "COMPLETED",
            "correctCount": correct,
            "scorePercentage": round(score_pct, 1),
            "durationSeconds": duration_seconds,
            "completedAt": now,
        },
    )

    # Compute per-topic breakdown
    topic_breakdown = await _compute_topic_breakdown(quiz_id, answers)
    weak_areas = [t["title"] for t in topic_breakdown if t.get("score", 0) < _WEAK_AREA_THRESHOLD]

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="quiz_completed",
        title=f"Completed quiz — {round(score_pct, 1)}% ({correct}/{total})",
        context={"source": "personal", "quizId": quiz_id, "score": round(score_pct, 1)},
    )

    # Check milestones (quiz_90_plus)
    from . import milestone_service

    await milestone_service.check_milestones(user_id, {"quiz_score": round(score_pct, 1)})

    return {
        "quizId": quiz_id,
        "totalQuestions": total,
        "correctCount": correct,
        "scorePercentage": round(score_pct, 1),
        "topicBreakdown": topic_breakdown,
        "weakAreas": weak_areas,
        "suggestedNextStep": _suggest_next_step(weak_areas),
    }


async def get_quiz(*, user_id: str, quiz_id: str) -> Any:
    """Get a quiz session with its questions."""
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)
    questions = await repo.list_quiz_questions(quiz_id)
    answers = await repo.list_quiz_answers(quiz_id)
    return _build_quiz_response(quiz, questions, answers)


async def list_prep_quizzes(*, user_id: str, prep_id: str) -> list[Any]:
    """List all quiz sessions for a preparation, newest first."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    quizzes = await repo.list_prep_quizzes(prep_id, user_id)
    return [_build_quiz_response(q, [], []) for q in quizzes]


def _build_quiz_response(quiz: Any, questions: list[Any], answers: list[Any]) -> dict[str, Any]:
    """Build the quiz session response including questions and the learner's answers.

    The answer key is disclosed **per question, as soon as that question has been
    answered** — teaching in small steps is the point of practice, so the learner
    keeps the explanation for what they have already attempted, including after
    navigating back or resuming the session later.

    What is withheld is the key for questions not yet attempted (Decision C). A
    completed session reveals everything, including questions left unanswered, so
    review is complete.

    The distinction that matters is *answered*, not *in progress*: a client can
    never see the answer to a question the learner has not yet committed to.
    """
    # Index answers by question_id for O(1) lookup
    answer_map = {a.question_id: a for a in answers}
    session_completed = quiz.status == "COMPLETED"
    # Under examination conditions, answering a question earns no disclosure. Review
    # happens once, at the end. Everywhere else, answering reveals that question.
    reveal_on_answer = not defers_feedback(quiz.mode)
    # `questions` is a list of (question, link) pairs. Everything session-specific
    # — position, hints taken — belongs to the link, not to the banked question.
    ordered = list(questions)

    return {
        "id": quiz.id,
        "user_id": quiz.user_id,
        "prep_id": quiz.prep_id,
        "mode": quiz.mode,
        "topic_id": quiz.topic_id,
        "status": quiz.status,
        "total_questions": quiz.total_questions,
        "correct_count": quiz.correct_count,
        "score_percentage": quiz.score_percentage,
        "duration_seconds": quiz.duration_seconds,
        "completed_at": quiz.completed_at,
        "created_at": quiz.created_at,
        "questions": [
            _question_dict(
                question,
                answer_map.get(question.id),
                order_index=link.order_index,
                hints_used=getattr(link, "hint_count", 0) or 0,
                reveal_answers=session_completed
                or (reveal_on_answer and question.id in answer_map),
            )
            for question, link in ordered
        ],
    }


def _question_dict(
    question: Any,
    answer: Any | None,
    *,
    order_index: int,
    hints_used: int,
    reveal_answers: bool,
) -> dict[str, Any]:
    """Serialize a question with the learner's answer attached, if they have one.

    `reveal_answers` gates the answer key. It is not defaulted, so a new caller
    has to state which side of the disclosure boundary it is on rather than
    silently inheriting the leaky behaviour.
    """
    return {
        "id": question.id,
        "question_text": question.question_text,
        "question_type": question.question_type,
        "options": question.options if isinstance(question.options, list) else None,
        "order_index": order_index,
        "prep_topic_id": question.prep_topic_id,
        # Shown from the start: difficulty describes the question, not the answer.
        "difficulty": getattr(question, "difficulty", None),
        # So a resumed session can show what the learner already took, rather than
        # silently offering a fresh hint they have effectively already had.
        "hints_used": hints_used,
        "correct_answer": question.correct_answer if reveal_answers else None,
        "explanation": question.explanation if reveal_answers else None,
        # Withheld with the key: a tip about this question can hint at its answer.
        "exam_tip": getattr(question, "exam_tip", None) if reveal_answers else None,
        "user_answer": answer.user_answer if answer else None,
        "is_correct": answer.is_correct if answer else None,
        "time_taken_seconds": answer.time_taken_seconds if answer else None,
        "answered_at": answer.created_at if answer else None,
    }


async def _update_topic_mastery(topic_id: str, *, user_id: str) -> None:
    """Recompute a topic's mastery from the competence model.

    Phase B. This used to be `correct / total` over every answer ever recorded,
    which the book rules out twice: it never forgot a bad week, and it let a single
    mistake stand as the whole assessment.

    `PrepTopic.mastery_score` is now a **cache of the model's `retention`** rather
    than a truth of its own. Everything downstream — readiness, the Learn card, the
    dashboard aggregates — keeps reading the same column and simply gets a better
    number, so no consumer had to change.

    A topic with too little evidence is left **untouched** rather than written to
    zero. Writing zero would assert that the learner knows nothing, which is a claim
    three answers do not support.
    """
    from . import prep_competence

    competence = await prep_competence.load_for_topic(user_id=user_id, topic_id=topic_id)

    if not competence.is_measurable or competence.retention is None:
        logger.debug(
            "Topic mastery left unchanged: not enough evidence",
            extra={
                "topic_id": topic_id,
                "observations": competence.observations,
                "effective_weight": competence.effective_weight,
            },
        )
        return

    band = competence.band
    status = {
        "strong": "MASTERED",
        "review": "IN_PROGRESS",
        "focus": "IN_PROGRESS",
    }.get(band or "focus", "IN_PROGRESS")

    await repo.update_topic_mastery(topic_id, competence.retention, status)


async def _compute_topic_breakdown(quiz_id: str, answers: list[Any]) -> list[dict]:
    """Compute per-topic score breakdown."""
    from sqlalchemy import select as sa_select

    from src.domains.personal_learning.db_models import (
        PrepQuestion,
        PrepTopic,
        QuizSessionQuestion,
    )
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Reached through the session link, since a banked question is no longer
        # owned by the session that asked it.
        stmt = (
            sa_select(PrepQuestion)
            .join(
                QuizSessionQuestion,
                QuizSessionQuestion.prep_question_id == PrepQuestion.id,
            )
            .where(QuizSessionQuestion.quiz_session_id == quiz_id)
        )
        result = await session.execute(stmt)
        questions = list(result.scalars().all())

    # Group by topic
    topic_scores: dict[str, dict] = {}
    answer_map = {a.question_id: a for a in answers}

    for q in questions:
        topic_id = q.prep_topic_id or "general"
        if topic_id not in topic_scores:
            topic_scores[topic_id] = {"total": 0, "correct": 0, "title": "General"}
        topic_scores[topic_id]["total"] += 1
        answer = answer_map.get(q.id)
        if answer and answer.is_correct:
            topic_scores[topic_id]["correct"] += 1

    # Resolve topic titles
    if topic_scores:
        topic_ids = [k for k in topic_scores if k != "general"]
        if topic_ids:
            async with factory() as session:
                stmt = sa_select(PrepTopic).where(PrepTopic.id.in_(topic_ids))
                result = await session.execute(stmt)
                topics = list(result.scalars().all())
                for t in topics:
                    if t.id in topic_scores:
                        topic_scores[t.id]["title"] = t.title

    breakdown = []
    for tid, data in topic_scores.items():
        total = data["total"]
        correct = data["correct"]
        score = (correct / total * 100) if total > 0 else 0
        breakdown.append(
            {
                "topicId": tid,
                "title": data["title"],
                "total": total,
                "correct": correct,
                "score": round(score, 1),
            }
        )

    return breakdown


def _suggest_next_step(weak_areas: list[str]) -> str | None:
    """Suggest what to do next based on quiz results."""
    if not weak_areas:
        return "Great job! All topics are well covered. Try a Full Practice quiz next time."
    if len(weak_areas) == 1:
        return (
            f"Focus on reviewing: {weak_areas[0]}. Try a Topic Focus quiz to strengthen this area."
        )
    return f"Review these topics: {', '.join(weak_areas[:3])}. Try a Weak Areas quiz to improve."


def _check_answer_correctness(
    user_answer: str,
    correct_answer: str,
    options: list[str] | dict | None,
) -> bool:
    """Check if a user's answer is correct using multiple matching strategies.

    Handles these cases:
    1. Direct text match (case-insensitive, stripped)
    2. Option index match: user sends "A"/"B"/"C"/"D" or "0"/"1"/"2"/"3",
       and correct_answer is the full text of an option (or vice versa)
    3. Prefix match: user sends "A. <text>" or "A) <text>"
    4. Substring match: correct_answer is contained in user_answer or vice versa
       (for short answers where the user adds extra context)

    Returns True if the answer is considered correct by any strategy.
    """
    # Normalize
    user_norm = user_answer.strip().lower()
    correct_norm = correct_answer.strip().lower()

    # Strategy 1: Direct match
    if user_norm == correct_norm:
        return True

    # Strategy 2: Option index matching (A/B/C/D or 0/1/2/3)
    if options and isinstance(options, list) and len(options) > 0:
        # Normalize options
        options_norm = [str(o).strip().lower() for o in options]

        # Find the index of the correct answer in options
        correct_index = None
        for i, opt in enumerate(options_norm):
            if opt == correct_norm:
                correct_index = i
                break

        # Map letter/number to index
        index_map = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5}

        # Case: user sent an index (letter or number), correct_answer is option text
        user_index = index_map.get(user_norm)
        if user_index is None and user_norm.isdigit():
            user_index = int(user_norm)

        if user_index is not None and correct_index is not None:
            return user_index == correct_index

        # Case: user sent the full option text, correct_answer is a letter/number
        correct_as_index = index_map.get(correct_norm)
        if correct_as_index is None and correct_norm.isdigit():
            correct_as_index = int(correct_norm)

        if correct_as_index is not None and 0 <= correct_as_index < len(options_norm):
            # correct_answer is "A" or "0" — compare user's answer to the option text
            if user_norm == options_norm[correct_as_index]:
                return True

        # Case: user sent "A. <text>" or "A) <text>" — extract the text part
        if len(user_norm) >= 2 and user_norm[0] in index_map and user_norm[1] in ".):- ":
            user_text = user_norm[2:].strip().lstrip(".):- ").strip()
            if user_text == correct_norm:
                return True
            # Also check if the extracted text matches the option at that index
            letter_index = index_map[user_norm[0]]
            if correct_index is not None and letter_index == correct_index:
                return True

        # Case: user sent full option text, check if it matches the correct option
        if correct_index is not None and user_norm == options_norm[correct_index]:
            return True

        # Case: user's answer matches any option that matches the correct answer
        if user_norm in options_norm:
            user_option_index = options_norm.index(user_norm)
            if correct_index is not None and user_option_index == correct_index:
                return True

    # Strategy 3: One contains the other (for short-answer flexibility)
    # Only apply if both are non-trivial length (avoid "a" matching everything)
    if len(correct_norm) > 3 and len(user_norm) > 3:
        if correct_norm in user_norm or user_norm in correct_norm:
            return True

    return False
