"""The post-exam review: how a preparation actually completes.

**A clock is not an outcome.** Until this existed, the only completion path was a nightly sweep — then
called `mark_overdue_preparations_completed`, now `exam_prep_service.mark_preparations_awaiting_review` —
which set `status = COMPLETED` on every preparation whose `examDate` had passed. A learner who was 30
percent ready for an exam they missed got a preparation recorded as finished. The date passing says the
exam happened; it does not say they sat it, that they were ready, or that it went well. The only party who
knows is the learner, and nothing had asked them.

So the sweep now moves a passed preparation to `AWAITING_REVIEW`, and **only the learner's answer moves it
to `COMPLETED`**. Waiting is a third state, and it needed a name: it is neither finished nor overdue.

**What this buys beyond honesty.** `prep_readiness.progress_percent` is a prediction —
`topicsStrong / topicsTotal`, with "strong" at `MASTERY_STRONG_THRESHOLD = 80`. It is shown to learners,
used to gate goal progress, and recorded daily in `PrepReadinessSnapshot`. **It has never once been
compared against an outcome, because no outcome existed.** Every answer recorded here carries the readiness
figures as they stood, which makes the prediction falsifiable — per learner, in aggregate, and per
`prep_type`. That comparison is deliberately *not* made here: this module records facts, and any
recalibration is derived on read from these rows, the same discipline `goal_metrics.derived_progress`
follows.

**Nothing is inferred from a date.** A postponed exam gets a new date because the learner said it was
postponed. An unanswered preparation stays unanswered rather than being assumed to have gone well.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.shared.exceptions import NotFoundError, ValidationError

from ..repository import personal_learning_repo as repo

logger = logging.getLogger(__name__)

#: Answers that end the preparation. `postponed` is the exception — it opens a new sitting instead.
_CONCLUDING = frozenset({"sat", "missed", "cancelled"})

#: How many reminders may follow the first ask, per sitting.
#:
#: Two, and the number is a judgement rather than a tuning parameter: this is a message arriving after a
#: possibly bad experience. A learner who has ignored three prompts has answered.
MAX_REVIEW_REMINDERS = 2


def is_awaiting_review(prep: Any, *, now: datetime | None = None) -> bool:
    """Whether this preparation is waiting on the learner to say how the exam went.

    Three conditions, and each excludes a state that looks similar and is not:

    - the exam date has passed — before that there is nothing to review;
    - it is not already `COMPLETED` — an answered preparation is finished;
    - the learner has not declined — **a dismissal is an answer**, and continuing to ask someone who
      said no is the failure mode this budget exists to prevent.

    Derived rather than stored, for the reason `goal_metrics.status_label` gives about its own labels: a
    stored "awaiting" is wrong the moment the learner answers, and two readers computing it separately
    from a status plus a date comparison is how they come to disagree.
    """
    moment = now or datetime.now(UTC)
    exam_date = _as_utc(getattr(prep, "exam_date", None))
    if exam_date is None or exam_date >= moment:
        return False
    if getattr(prep, "status", None) == "COMPLETED":
        return False
    return getattr(prep, "review_declined_at", None) is None


def _as_utc(value: datetime | None) -> datetime | None:
    """A naive timestamp read as UTC, so comparisons here cannot raise.

    `ExamPrep.examDate` is one of the columns stored without an offset while the ORM declares it
    `DateTime(timezone=True)`, so asyncpg hands it back naive. Comparing that against an aware
    `datetime.now(UTC)` raises `TypeError` — the same defect that made `GET /progress/goals` a 500 for any
    goal with a target date, recorded in `goal_metrics._utc`.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _validate(data: dict[str, Any]) -> None:
    """Refuse answers that contradict themselves, with a message the client can act on.

    Refused rather than silently dropped, which is the rule this backend applies everywhere else — see
    `goal_service._reject_asserted_current_value`. A learner who rates an exam they did not sit and
    watches the rating vanish has been overruled with no explanation.
    """
    attended = data.get("attended")

    if attended == "postponed" and data.get("postponedTo") is None:
        raise ValidationError(
            "A postponed exam needs its new date.",
            detail="Set 'postponedTo' when 'attended' is 'postponed'.",
        )
    if attended != "postponed" and data.get("postponedTo") is not None:
        raise ValidationError(
            "'postponedTo' only applies to a postponed exam.",
            detail=f"'attended' is '{attended}', so there is no new sitting to schedule.",
        )
    # There is no experience of an exam nobody took. `preparationRating` is deliberately still allowed:
    # a learner who missed the exam can still have a view on whether the preparation was any good, and
    # that is the rating this whole exercise exists to collect.
    if attended != "sat" and data.get("experienceRating") is not None:
        raise ValidationError(
            "Only a sitting that happened can be rated.",
            detail=(
                f"'attended' is '{attended}', so 'experienceRating' does not apply. "
                "'preparationRating' still does."
            ),
        )


async def _readiness_at_answer(prep_id: str) -> dict[str, Any]:
    """The readiness figures as they stand, snapshotted onto the answer.

    Copied rather than left to a later join, so the calibration question is a single-table query and
    survives `PrepReadinessSnapshot` being pruned. A failure here must not cost the learner their answer:
    the answer is the thing worth keeping, and unmeasured readiness is honestly null.
    """
    try:
        from . import prep_readiness

        progress = (await prep_readiness.load_for_preparations([prep_id])).get(prep_id)
    except Exception:
        logger.warning("Readiness unavailable for outcome snapshot", extra={"prep_id": prep_id})
        return {}

    if progress is None or progress.topics_total <= 0:
        # Nothing to measure. Null rather than zero, which would claim a measured absence of readiness.
        return {}

    return {
        "readiness_percent": progress.progress_percent,
        "average_mastery_percent": progress.average_mastery_percent,
        "topics_total": progress.topics_total,
        "topics_strong": progress.topics_strong,
    }


async def record_outcome(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """Record how a sitting went, and resolve the preparation from it.

    Returns the stored outcome. The preparation's own transition depends on the answer:

    - `sat`, `missed`, `cancelled` — the preparation is over and becomes `COMPLETED`. Note that
      `COMPLETED` here means *this preparation is finished*, not *you passed*: the outcome row carries
      what actually happened, and the status is a lifecycle value rather than a verdict.
    - `postponed` — the exam moved, so the preparation returns to `IN_PROGRESS` with the learner's new
      date and a fresh ask budget. This is the one path on which a date moves automatically, and it moves
      because the learner supplied it.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    _validate(data)

    now = datetime.now(UTC)
    attended = data["attended"]
    # The sitting this answer is about, which a postponed preparation is about to move away from.
    sitting = _as_utc(prep.exam_date)

    values: dict[str, Any] = {
        "user_id": user_id,
        "attended": attended,
        "experience_rating": data.get("experienceRating"),
        "preparation_rating": data.get("preparationRating"),
        "reflection": (data.get("reflection") or None),
        "answered_at": now,
        # Stored so "did they reach what they were aiming at" stays answerable after they edit the target.
        "target_readiness": prep.target_readiness,
        **await _readiness_at_answer(prep_id),
    }

    outcome = await repo.upsert_prep_outcome(
        prep_id=prep_id, exam_date=sitting, values=values
    )

    if attended == "postponed":
        await repo.update_exam_prep(
            prep_id,
            {
                "examDate": data["postponedTo"],
                "status": "IN_PROGRESS",
                # A new sitting is a new question, so the budget starts again.
                "reviewAskedAt": None,
                "reviewRemindersSent": 0,
                "reviewDeclinedAt": None,
            },
        )
    else:
        await repo.update_exam_prep(prep_id, {"status": "COMPLETED"})
        await _resolve_linked_goal(user_id=user_id, prep_id=prep_id)

    await _record_activity(user_id=user_id, prep=prep, attended=attended)
    return outcome


async def record_result(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """Attach the result to the most recent recorded sitting.

    Separate from `record_outcome` because results arrive weeks later. Requiring one up front would either
    block the review or invite a made-up number.

    Refuses when nothing has been reviewed yet: a result without an answer would be a score attached to a
    sitting nobody has confirmed happened.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    outcomes = await repo.list_prep_outcomes(prep_id)
    if not outcomes:
        raise ValidationError(
            "There is no reviewed sitting to attach a result to.",
            detail="Record how the exam went first.",
        )

    latest = outcomes[-1]
    return await repo.upsert_prep_outcome(
        prep_id=prep_id,
        exam_date=latest.exam_date,
        values={
            "result_value": data["resultValue"],
            "result_scale": data.get("resultScale"),
            "result_recorded_at": datetime.now(UTC),
        },
    )


async def decline_review(*, user_id: str, prep_id: str) -> Any:
    """The learner saying they would rather not answer.

    **A dismissal is an answer**, and recording it is what stops the asking. Without this the only way
    out of the reminder budget is to exhaust it, which means the learner who least wants to talk about the
    exam is the one who gets asked the most.

    The preparation is **not** marked `COMPLETED`: nothing has been said about how it went, and asserting
    completion here would put back the exact lie this whole change removes. It stays out of the ask list
    and out of the way.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return await repo.update_exam_prep(prep_id, {"reviewDeclinedAt": datetime.now(UTC)})


async def get_review_state(*, user_id: str, prep_id: str) -> dict[str, Any]:
    """Whether this preparation is waiting on an answer, and what has already been asked.

    Published as one object so a client renders the review from a single field rather than inferring it
    from `status` plus a date comparison. Two clients inferring the same thing separately is how they come
    to disagree about it — and mobile and web would each have had to get the `examDate` timezone handling
    right on their own.

    `outcome` is the answer for the **current** sitting only. A postponed preparation's earlier sittings
    are real history and are read through `list_outcomes`; putting them here would make the field mean
    two different things depending on the preparation.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    outcome = await repo.find_prep_outcome(prep_id=prep_id, exam_date=_as_utc(prep.exam_date))
    return {
        # False once answered, because an answered sitting is not waiting on anything — the `COMPLETED`
        # status already says so, and this must agree with it.
        "awaiting": is_awaiting_review(prep) and outcome is None,
        "askedAt": prep.review_asked_at,
        "remindersSent": prep.review_reminders_sent or 0,
        "declinedAt": prep.review_declined_at,
        "outcome": outcome,
    }


async def list_outcomes(*, user_id: str, prep_id: str) -> list[Any]:
    """Every recorded sitting, oldest first. More than one means the exam was postponed."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return await repo.list_prep_outcomes(prep_id)


async def _resolve_linked_goal(*, user_id: str, prep_id: str) -> None:
    """Close the goal that was measuring readiness for this exam.

    A `prep_readiness` goal is "be ready for X by date". Once the exam has happened there is nothing left
    to be ready for, so leaving it `ACTIVE` reports it as overdue forever — which is the symptom that
    started this work.

    **Completed when the learner reached their stated target, archived otherwise.** That distinction is
    measured, not guessed: `derived_progress` already computes readiness against the target the learner
    set. Marking every goal `COMPLETED` would claim they were ready; marking every one `ARCHIVED` would
    discard the achievement of the ones who were.

    `plan_derivations` already applies the same judgement at the other end — it refuses to derive a goal
    for a preparation whose date has passed, on the grounds that "getting ready for it is no longer a
    goal".

    Failures are contained. The learner has answered and that answer is stored; a goal that could not be
    resolved is a stale label, not a reason to reject their answer.
    """
    try:
        from src.domains.progress.repository import progress_repo
        from src.domains.progress.services import goal_metrics

        goals = [
            goal
            for goal in await progress_repo.list_goals_for_prep(prep_id)
            if goal.user_id == user_id and goal.status == "ACTIVE"
        ]
        if not goals:
            return

        measurements = await goal_metrics.derive_current_values(goals)
        for goal in goals:
            reached = goal_metrics.derived_progress(goal, measurements.get(goal.id)) >= 100
            await progress_repo.update_goal(
                goal.id, {"status": "COMPLETED" if reached else "ARCHIVED"}
            )
    except Exception:
        logger.warning(
            "Could not resolve the goal linked to a reviewed preparation",
            extra={"user_id": user_id, "prep_id": prep_id},
        )


async def _record_activity(*, user_id: str, prep: Any, attended: str) -> None:
    """Put the review on the activity feed.

    The preparation, not the outcome: an outcome has no page of its own, so routing to it would be a link
    to nowhere — the same rule `study_plan_service` follows when it records the plan rather than the item.
    """
    try:
        from . import activity_feed_service

        await activity_feed_service.record(
            user_id=user_id,
            activity_type="preparation_reviewed",
            title=f"Reviewed {prep.subject}",
            entity_type="preparation",
            entity_id=prep.id,
            context={"source": "personal", "prepId": prep.id, "attended": attended},
        )
    except Exception:
        logger.warning("Could not record a preparation review", extra={"prep_id": prep.id})
