"""
Quiz Engine — generates, serves, and scores practice quizzes.

Supports multiple modes: FULL_PRACTICE (all topics), WEAK_AREAS (mastery < 70),
TOPIC_FOCUS (single topic), and QUICK_REVIEW.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from src.shared.exceptions import NotFoundError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)


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

    # Verify prep exists and belongs to user
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    # Get topics based on mode
    all_topics = await repo.list_prep_topics(prep_id)
    if not all_topics:
        raise ValueError("No topics available for this preparation. Extract topics first.")

    if mode == "FULL_PRACTICE":
        target_topics = all_topics
    elif mode == "WEAK_AREAS":
        target_topics = [t for t in all_topics if t.mastery_score < 70.0]
        if not target_topics:
            target_topics = all_topics  # Fallback to all if none are weak
    elif mode == "TOPIC_FOCUS":
        if not topic_id:
            raise ValueError("topic_id required for TOPIC_FOCUS mode")
        target_topics = [t for t in all_topics if t.id == topic_id]
        if not target_topics:
            raise NotFoundError("PrepTopic", topic_id)
    else:
        # QUICK_REVIEW — mix of topics, fewer questions
        target_topics = all_topics[:5]

    # Determine question count
    count = question_count or min(len(target_topics) * 2, 20)

    # Create quiz session
    quiz_session = await repo.create_quiz_session(
        {
            "userId": user_id,
            "prepId": prep_id,
            "mode": mode,
            "topicId": topic_id,
            "status": "IN_PROGRESS",
            "totalQuestions": count,
        }
    )

    # Generate questions via LLM
    topics_text = "\n".join([f"- {t.title}: {t.description or ''}" for t in target_topics])
    prompt = (
        f"Generate {count} quiz questions for these topics:\n{topics_text}\n\n"
        f"Return a JSON array of question objects with:\n"
        f"- 'topicTitle': which topic this tests\n"
        f"- 'questionText': the question\n"
        f"- 'questionType': 'MULTIPLE_CHOICE'\n"
        f"- 'options': array of 4 options (strings)\n"
        f"- 'correctAnswer': the correct option (must match one of the options exactly)\n"
        f"- 'explanation': brief explanation of why the answer is correct\n\n"
        f"Return ONLY the JSON array."
    )

    try:
        questions_data = await generate_content_json(
            prompt, max_tokens=8000, timeout_s=60, fallback=[]
        )
    except Exception as e:
        logger.warning(f"Failed to generate quiz questions for prep {prep_id}: {e}")
        questions_data = []

    # Create question records
    topic_map = {t.title.lower(): t.id for t in target_topics}
    for idx, q in enumerate(questions_data[:count]):
        if not isinstance(q, dict) or "questionText" not in q:
            continue

        # Match topic
        topic_title = q.get("topicTitle", "").lower()
        matched_topic_id = topic_map.get(topic_title)

        await repo.create_quiz_question(
            {
                "quizSessionId": quiz_session.id,
                "prepTopicId": matched_topic_id,
                "questionText": q["questionText"],
                "questionType": q.get("questionType", "MULTIPLE_CHOICE"),
                "options": q.get("options"),
                "correctAnswer": q.get("correctAnswer", ""),
                "explanation": q.get("explanation"),
                "orderIndex": idx,
            }
        )

    # Update total questions to actual generated count
    actual_count = min(len(questions_data), count)
    if actual_count != count:
        await repo.update_quiz_session(quiz_session.id, {"totalQuestions": actual_count})

    return await repo.get_quiz_session(quiz_session.id, user_id)


async def submit_answer(*, user_id: str, quiz_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Submit an answer to a quiz question.

    Req 4.8: Evaluate correctness, track time, update topic mastery.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    question_id = data["questionId"]
    user_answer = data["userAnswer"]
    time_taken = data.get("timeTakenSeconds")

    # Get the question to check correctness
    from sqlalchemy import select as sa_select
    from src.domains.personal_learning.db_models import QuizQuestion
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = sa_select(QuizQuestion).where(QuizQuestion.id == question_id)
        result = await session.execute(stmt)
        question = result.scalar_one_or_none()

    if not question:
        raise NotFoundError("QuizQuestion", question_id)

    is_correct = _check_answer_correctness(
        user_answer=user_answer,
        correct_answer=question.correct_answer,
        options=question.options,
    )

    # Record the answer
    await repo.create_quiz_answer(
        {
            "quizSessionId": quiz_id,
            "questionId": question_id,
            "userAnswer": user_answer,
            "isCorrect": is_correct,
            "timeTakenSeconds": time_taken,
        }
    )

    # Update quiz correct count
    if is_correct:
        new_correct = (quiz.correct_count or 0) + 1
        await repo.update_quiz_session(quiz_id, {"correctCount": new_correct})

    # Update topic mastery
    if question.prep_topic_id:
        await _update_topic_mastery(question.prep_topic_id)

    return {
        "questionId": question_id,
        "isCorrect": is_correct,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
    }


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

    now = datetime.now(timezone.utc)
    total = quiz.total_questions or 1
    correct = quiz.correct_count or 0
    score_pct = (correct / total) * 100 if total > 0 else 0.0

    # Update quiz session
    await repo.update_quiz_session(
        quiz_id,
        {
            "status": "COMPLETED",
            "scorePercentage": round(score_pct, 1),
            "durationSeconds": duration_seconds,
            "completedAt": now,
        },
    )

    # Compute per-topic breakdown
    answers = await repo.list_quiz_answers(quiz_id)
    topic_breakdown = await _compute_topic_breakdown(quiz_id, answers)
    weak_areas = [t["title"] for t in topic_breakdown if t.get("score", 0) < 70]

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
    """Get a quiz session with its answers."""
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)
    return quiz


async def _update_topic_mastery(topic_id: str) -> None:
    """Recalculate topic mastery based on all quiz answers for this topic."""
    from sqlalchemy import select as sa_select
    from src.domains.personal_learning.db_models import QuizAnswer, QuizQuestion
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            sa_select(QuizAnswer)
            .join(QuizQuestion, QuizAnswer.question_id == QuizQuestion.id)
            .where(QuizQuestion.prep_topic_id == topic_id)
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
    from src.domains.personal_learning.db_models import QuizQuestion, PrepTopic
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = sa_select(QuizQuestion).where(QuizQuestion.quiz_session_id == quiz_id)
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
