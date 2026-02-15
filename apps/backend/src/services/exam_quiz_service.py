"""
Exam Quiz Service: quiz session management, question selection, scoring.

Handles the core quiz engine logic including adaptive question selection
for weak-areas mode and mastery tracking.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime
from typing import Any

from prisma import Prisma

from src.services.exam_prep_service import _calculate_question_mastery

logger = logging.getLogger(__name__)

# Default question counts per quiz mode
QUIZ_COUNTS = {
    "QUICK_REVIEW": 10,
    "FULL_PRACTICE": 25,
    "WEAK_AREAS": 15,
    "TOPIC_FOCUS": 15,
    "PAST_PAPER_SIM": 30,
}


async def start_quiz(
    db: Prisma,
    exam_prep_id: str,
    user_id: str,
    mode: str,
    topic_id: str | None = None,
    question_count: int | None = None,
) -> dict:
    """
    Start a new quiz session. Selects questions based on mode and returns
    the quiz session with the first question.

    Returns:
        dict with quizSession info and selected questionIds
    """
    # Verify ownership
    exam_prep = await db.examprep.find_first(where={"id": exam_prep_id, "userId": user_id})
    if not exam_prep:
        raise ValueError("ExamPrep not found")

    # Determine question count
    count = question_count or QUIZ_COUNTS.get(mode, 15)

    # Select questions based on mode
    question_ids = await _select_questions(db, exam_prep_id, mode, topic_id, count)

    if not question_ids:
        raise ValueError(
            "No questions available for this quiz mode. Upload materials and process them first."
        )

    # Create quiz session
    session = await db.examquizsession.create(
        data={
            "examPrepId": exam_prep_id,
            "mode": mode,
            "totalQuestions": len(question_ids),
            "topicId": topic_id,
        }
    )

    # Fetch the full question objects
    questions = await db.examquestion.find_many(
        where={"id": {"in": question_ids}},
        include={"topic": True},
    )

    # Build ordered question list (preserve selection order)
    question_map = {q.id: q for q in questions}
    ordered_questions = [question_map[qid] for qid in question_ids if qid in question_map]

    return {
        "quizSessionId": session.id,
        "mode": mode,
        "totalQuestions": len(ordered_questions),
        "questions": [
            {
                "id": q.id,
                "questionText": q.questionText,
                "questionType": q.questionType,
                "options": q.options,
                "difficulty": q.difficulty,
                "topicTitle": q.topic.title if q.topic else "",
                "source": q.source,
                "year": q.year,
            }
            for q in ordered_questions
        ],
    }


async def submit_answer(
    db: Prisma,
    quiz_session_id: str,
    question_id: str,
    user_answer: str,
    time_taken_seconds: int | None = None,
) -> dict:
    """
    Submit an answer for a question in a quiz session.
    Returns correctness, correct answer, and explanation.
    """
    # Get the question
    question = await db.examquestion.find_unique(where={"id": question_id})
    if not question:
        raise ValueError("Question not found")

    # Determine correctness
    is_correct = _check_answer(question, user_answer)

    # Save the attempt
    attempt = await db.examquestionattempt.create(
        data={
            "quizSessionId": quiz_session_id,
            "questionId": question_id,
            "userAnswer": user_answer,
            "isCorrect": is_correct,
            "timeTakenSeconds": time_taken_seconds,
        }
    )

    # Update session correct count if correct
    if is_correct:
        await db.examquizsession.update(
            where={"id": quiz_session_id},
            data={"correctCount": {"increment": 1}},
        )

    # Build the correct answer display
    correct_answer_display = _get_correct_answer_display(question)

    return {
        "attemptId": attempt.id,
        "isCorrect": is_correct,
        "correctAnswer": correct_answer_display,
        "explanation": question.explanation,
        "userAnswer": user_answer,
    }


async def complete_quiz(
    db: Prisma,
    quiz_session_id: str,
    duration_seconds: int | None = None,
) -> dict:
    """
    Complete a quiz session. Calculates final score and returns summary.
    """
    session = await db.examquizsession.find_unique(
        where={"id": quiz_session_id},
        include={"attempts": {"include": {"question": {"include": {"topic": True}}}}},
    )
    if not session:
        raise ValueError("Quiz session not found")

    # Calculate score
    total = session.totalQuestions
    correct = session.correctCount
    score = (correct / total * 100) if total > 0 else 0

    # Update session
    await db.examquizsession.update(
        where={"id": quiz_session_id},
        data={
            "score": score,
            "completedAt": datetime.now(UTC),
            "durationSeconds": duration_seconds,
        },
    )

    # Build per-question breakdown
    question_results = []
    topic_stats: dict[str, dict] = {}

    for attempt in session.attempts or []:
        q = attempt.question
        topic_title = q.topic.title if q and q.topic else "Unknown"

        question_results.append(
            {
                "questionId": attempt.questionId,
                "questionText": q.questionText if q else "",
                "topicTitle": topic_title,
                "userAnswer": attempt.userAnswer,
                "isCorrect": attempt.isCorrect,
                "correctAnswer": _get_correct_answer_display(q) if q else "",
                "explanation": q.explanation if q else "",
            }
        )

        # Track per-topic stats
        if topic_title not in topic_stats:
            topic_stats[topic_title] = {"total": 0, "correct": 0}
        topic_stats[topic_title]["total"] += 1
        if attempt.isCorrect:
            topic_stats[topic_title]["correct"] += 1

    # Build topic breakdown
    topic_breakdown = [
        {
            "topic": topic,
            "total": stats["total"],
            "correct": stats["correct"],
            "percentage": (
                round(stats["correct"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
            ),
        }
        for topic, stats in topic_stats.items()
    ]
    topic_breakdown.sort(key=lambda x: x["percentage"])

    wrong_count = total - correct

    return {
        "quizSessionId": quiz_session_id,
        "mode": session.mode,
        "totalQuestions": total,
        "correctCount": correct,
        "wrongCount": wrong_count,
        "score": round(score, 1),
        "durationSeconds": duration_seconds,
        "questionResults": question_results,
        "topicBreakdown": topic_breakdown,
        "weakestTopics": [t["topic"] for t in topic_breakdown if t["percentage"] < 60][:3],
    }


# ---------------------------------------------------------------------------
# Question Selection Algorithm
# ---------------------------------------------------------------------------


async def _select_questions(
    db: Prisma,
    exam_prep_id: str,
    mode: str,
    topic_id: str | None,
    count: int,
) -> list[str]:
    """Select question IDs based on quiz mode."""

    if mode == "TOPIC_FOCUS" and topic_id:
        return await _select_topic_focus(db, topic_id, count)
    elif mode == "WEAK_AREAS":
        return await _select_weak_areas(db, exam_prep_id, count)
    elif mode == "PAST_PAPER_SIM":
        return await _select_past_paper(db, exam_prep_id, count)
    elif mode == "QUICK_REVIEW":
        return await _select_quick_review(db, exam_prep_id, count)
    else:  # FULL_PRACTICE
        return await _select_full_practice(db, exam_prep_id, count)


async def _select_full_practice(db: Prisma, exam_prep_id: str, count: int) -> list[str]:
    """Random mix of all questions, weighted by mastery (prefer less mastered)."""
    questions = await db.examquestion.find_many(
        where={"topic": {"examPrepId": exam_prep_id}},
        include={"attempts": True},
    )
    if not questions:
        return []

    # Group by mastery
    buckets: dict[str, list] = {"NEW": [], "LEARNING": [], "FAMILIAR": [], "MASTERED": []}
    for q in questions:
        mastery = _calculate_question_mastery(q.attempts)
        buckets[mastery].append(q.id)

    # Weighted selection: NEW 30%, LEARNING 30%, FAMILIAR 25%, MASTERED 15%
    selected = []
    weights = [("NEW", 0.3), ("LEARNING", 0.3), ("FAMILIAR", 0.25), ("MASTERED", 0.15)]

    for mastery, weight in weights:
        pool = buckets[mastery]
        n = min(len(pool), max(1, int(count * weight)))
        selected.extend(random.sample(pool, n))

    # Fill remaining from all questions
    remaining_ids = [q.id for q in questions if q.id not in selected]
    if len(selected) < count and remaining_ids:
        additional = min(count - len(selected), len(remaining_ids))
        selected.extend(random.sample(remaining_ids, additional))

    random.shuffle(selected)
    return selected[:count]


async def _select_weak_areas(db: Prisma, exam_prep_id: str, count: int) -> list[str]:
    """Select questions the user has gotten wrong, prioritizing most recent failures."""
    questions = await db.examquestion.find_many(
        where={"topic": {"examPrepId": exam_prep_id}},
        include={"attempts": {"order_by": {"createdAt": "desc"}}},
    )
    if not questions:
        return []

    # Find questions with wrong answers that aren't mastered
    weak_questions = []
    for q in questions:
        mastery = _calculate_question_mastery(q.attempts)
        if mastery in ("LEARNING", "FAMILIAR"):
            # Calculate error weight (more recent errors = higher priority)
            wrong_count = sum(1 for a in q.attempts if not a.isCorrect)
            weak_questions.append((q.id, wrong_count, mastery))

    # Sort by: LEARNING first, then by wrong count descending
    weak_questions.sort(key=lambda x: (0 if x[2] == "LEARNING" else 1, -x[1]))

    selected = [q[0] for q in weak_questions[:count]]

    # If not enough weak questions, fill with NEW questions
    if len(selected) < count:
        new_questions = [
            q.id
            for q in questions
            if q.id not in selected and _calculate_question_mastery(q.attempts) == "NEW"
        ]
        additional = min(count - len(selected), len(new_questions))
        if additional > 0:
            selected.extend(random.sample(new_questions, additional))

    random.shuffle(selected)
    return selected[:count]


async def _select_past_paper(db: Prisma, exam_prep_id: str, count: int) -> list[str]:
    """Select only questions sourced from past papers."""
    questions = await db.examquestion.find_many(
        where={
            "topic": {"examPrepId": exam_prep_id},
            "source": "PAST_QUESTION",
        },
    )
    if not questions:
        # Fallback to all questions
        questions = await db.examquestion.find_many(
            where={"topic": {"examPrepId": exam_prep_id}},
        )

    ids = [q.id for q in questions]
    random.shuffle(ids)
    return ids[:count]


async def _select_topic_focus(db: Prisma, topic_id: str, count: int) -> list[str]:
    """Select questions from a specific topic."""
    questions = await db.examquestion.find_many(
        where={"topicId": topic_id},
    )
    ids = [q.id for q in questions]
    random.shuffle(ids)
    return ids[:count]


async def _select_quick_review(db: Prisma, exam_prep_id: str, count: int) -> list[str]:
    """Quick review: mix of weak + random, small count."""
    questions = await db.examquestion.find_many(
        where={"topic": {"examPrepId": exam_prep_id}},
        include={"attempts": {"order_by": {"createdAt": "desc"}}},
    )
    if not questions:
        return []

    # Half weak, half random
    half = count // 2
    weak = []
    others = []

    for q in questions:
        mastery = _calculate_question_mastery(q.attempts)
        if mastery in ("LEARNING", "FAMILIAR", "NEW"):
            weak.append(q.id)
        else:
            others.append(q.id)

    selected = []
    if weak:
        selected.extend(random.sample(weak, min(half, len(weak))))
    if others:
        remaining = count - len(selected)
        selected.extend(random.sample(others, min(remaining, len(others))))

    # Fill up if needed
    all_ids = [q.id for q in questions if q.id not in selected]
    if len(selected) < count and all_ids:
        selected.extend(random.sample(all_ids, min(count - len(selected), len(all_ids))))

    random.shuffle(selected)
    return selected[:count]


# ---------------------------------------------------------------------------
# Answer Checking
# ---------------------------------------------------------------------------


def _check_answer(question: Any, user_answer: str) -> bool:
    """Check if the user's answer is correct."""
    q_type = question.questionType

    if q_type == "MULTIPLE_CHOICE":
        # user_answer is the option label (e.g., "A", "B", "C", "D")
        if question.options and isinstance(question.options, list):
            for opt in question.options:
                if isinstance(opt, dict):
                    if opt.get("label", "").upper() == user_answer.upper():
                        return opt.get("isCorrect", False)
        # Fallback: compare with correctAnswer
        if question.correctAnswer:
            return user_answer.strip().upper() == question.correctAnswer.strip().upper()
        return False

    elif q_type == "TRUE_FALSE":
        correct = (question.correctAnswer or "").strip().upper()
        answer = user_answer.strip().upper()
        return answer == correct

    elif q_type in ("SHORT_ANSWER", "FILL_IN_BLANK"):
        # Case-insensitive, trimmed comparison
        correct = (question.correctAnswer or "").strip().lower()
        answer = user_answer.strip().lower()
        # Exact match or contained within
        return answer == correct or correct in answer or answer in correct

    return False


def _get_correct_answer_display(question: Any) -> str:
    """Get a human-readable correct answer for display."""
    if question.questionType == "MULTIPLE_CHOICE" and question.options:
        if isinstance(question.options, list):
            for opt in question.options:
                if isinstance(opt, dict) and opt.get("isCorrect"):
                    label = opt.get("label", "")
                    text = opt.get("text", "")
                    return f"{label}. {text}" if label else text
    return question.correctAnswer or ""
