"""
Quiz Engine — generates, serves, and scores practice quizzes.

Supports multiple modes: FULL_PRACTICE (all topics), WEAK_AREAS (mastery < 70),
TOPIC_FOCUS (single topic), and QUICK_REVIEW.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.shared.exceptions import MaigieError, NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo

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
    from .llm_resilient import generate_content_json
    from . import feature_tier_service

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
    else:
        # QUICK_REVIEW — mix of topics, fewer questions
        target_topics = all_topics[:5]

    # Determine question count
    count = question_count or min(len(target_topics) * 2, 20)

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
    prompt = (
        f"Generate {count} quiz questions for these numbered topics:\n{topics_text}\n\n"
        f"Return a JSON array of question objects with:\n"
        f"- 'topicNumber': the number of the topic this tests, from the list above\n"
        f"- 'questionText': the question\n"
        f"- 'questionType': 'MULTIPLE_CHOICE'\n"
        f"- 'options': array of 4 options (strings)\n"
        f"- 'correctAnswer': the correct option (must match one of the options exactly)\n"
        f"- 'explanation': brief explanation of why the answer is correct\n"
        f"- 'difficulty': one of EASY, MEDIUM, HARD\n"
        f"- 'examTip': one sentence on how to approach this kind of question\n\n"
        f"Return ONLY the JSON array."
    )

    # Timed so the sync-versus-queued decision (Decision H) can be revisited from
    # measurements rather than from opinion. The provider is chosen per user by
    # llm_resilient, so this covers whichever of Gemini/OpenAI/Anthropic ran.
    started = time.monotonic()
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
    created = 0
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

    return {
        "question_text": question_text,
        "question_type": question_type,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": str(explanation).strip() or None if explanation is not None else None,
        "difficulty": difficulty,
        "exam_tip": exam_tip,
    }


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
        return {
            "questionId": question_id,
            "isCorrect": existing.is_correct,
            "correctAnswer": question.correct_answer,
            "explanation": question.explanation,
            "alreadyAnswered": True,
        }

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

    # Update topic mastery — fire-and-forget to avoid blocking the response.
    # Any failure is logged; user experience is not affected.
    if question.prep_topic_id:
        import asyncio

        asyncio.create_task(_update_topic_mastery_safe(question.prep_topic_id))

    return {
        "questionId": question_id,
        "isCorrect": is_correct,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "alreadyAnswered": False,
    }


async def _update_topic_mastery_safe(topic_id: str) -> None:
    """Wrap _update_topic_mastery with error handling for fire-and-forget use."""
    try:
        await _update_topic_mastery(topic_id)
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

    now = datetime.now(timezone.utc)
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
            started_at = started_at.replace(tzinfo=timezone.utc)
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
    # `questions` is a list of (question, orderIndex) pairs: order belongs to the
    # session that asked the question, not to the banked question.
    ordered = [(q, i) for q, i in questions]

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
                order_index=order_index,
                reveal_answers=session_completed or question.id in answer_map,
            )
            for question, order_index in ordered
        ],
    }


def _question_dict(
    question: Any, answer: Any | None, *, order_index: int, reveal_answers: bool
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
        "correct_answer": question.correct_answer if reveal_answers else None,
        "explanation": question.explanation if reveal_answers else None,
        # Withheld with the key: a tip about this question can hint at its answer.
        "exam_tip": getattr(question, "exam_tip", None) if reveal_answers else None,
        "user_answer": answer.user_answer if answer else None,
        "is_correct": answer.is_correct if answer else None,
        "time_taken_seconds": answer.time_taken_seconds if answer else None,
        "answered_at": answer.created_at if answer else None,
    }


async def _update_topic_mastery(topic_id: str) -> None:
    """Recalculate topic mastery based on all quiz answers for this topic."""
    from sqlalchemy import select as sa_select
    from src.domains.personal_learning.db_models import PrepQuestion, QuizAnswer
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            sa_select(QuizAnswer)
            .join(PrepQuestion, QuizAnswer.question_id == PrepQuestion.id)
            .where(PrepQuestion.prep_topic_id == topic_id)
        )
        result = await session.execute(stmt)
        answers = list(result.scalars().all())

    if not answers:
        return

    correct = sum(1 for a in answers if a.is_correct)
    mastery = (correct / len(answers)) * 100
    status = "MASTERED" if mastery >= 80 else "IN_PROGRESS" if mastery > 0 else "NOT_STARTED"

    await repo.update_topic_mastery(topic_id, mastery, status)


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
