"""
Exam Preparation service.

Delegates to the implementation files within this domain.
"""

import logging
from typing import Any

from prisma.models import User
from src.shared.database import db

logger = logging.getLogger(__name__)


async def create_exam_prep(*, user: User, data: dict[str, Any]) -> Any:
    """Create a new exam prep."""
    from src.domains.personal_learning.services.exam_prep_impl import create_exam_prep as _create

    return await _create(
        user_id=user.id,
        subject=data["subject"],
        exam_date=data["exam_date"],
        description=data.get("description"),
        db_client=db,
    )


async def update_exam_prep(*, user: User, prep_id: str, data: dict[str, Any]) -> Any:
    """Update exam prep metadata."""
    from src.domains.personal_learning.services.exam_prep_impl import update_exam_prep as _update

    return await _update(user_id=user.id, prep_id=prep_id, data=data, db_client=db)


async def get_exam_prep_progress(*, user: User, prep_id: str) -> dict[str, Any]:
    """Get exam prep progress and statistics."""
    from src.domains.personal_learning.services.exam_prep_impl import get_exam_prep_progress as _progress

    return await _progress(user_id=user.id, prep_id=prep_id, db_client=db)


async def generate_study_plan(*, user: User, prep_id: str) -> Any:
    """Generate AI study plan for exam prep."""
    from src.domains.personal_learning.services.exam_prep_impl import generate_study_plan as _plan

    return await _plan(user_id=user.id, prep_id=prep_id, db_client=db)


async def start_quiz(*, user: User, prep_id: str, mode: str, topic_id: str | None = None, question_count: int | None = None) -> Any:
    """Start a quiz session."""
    from src.domains.personal_learning.services.exam_quiz_impl import start_quiz as _start

    return await _start(
        user_id=user.id, prep_id=prep_id, mode=mode,
        topic_id=topic_id, question_count=question_count, db_client=db,
    )


async def submit_answer(*, user: User, session_id: str, question_id: str, user_answer: str, time_taken: int | None = None) -> Any:
    """Submit an answer to a quiz question."""
    from src.domains.personal_learning.services.exam_quiz_impl import submit_answer as _submit

    return await _submit(
        user_id=user.id, session_id=session_id,
        question_id=question_id, user_answer=user_answer,
        time_taken_seconds=time_taken, db_client=db,
    )


async def complete_quiz(*, user: User, session_id: str, duration_seconds: int | None = None) -> Any:
    """Complete a quiz session."""
    from src.domains.personal_learning.services.exam_quiz_impl import complete_quiz as _complete

    return await _complete(
        user_id=user.id, session_id=session_id,
        duration_seconds=duration_seconds, db_client=db,
    )
