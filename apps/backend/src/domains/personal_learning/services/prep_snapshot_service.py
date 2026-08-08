"""Daily readiness snapshots, and the trend they make possible.

Readiness is derived, not stored (Decision B), which is right for a live read and
useless for a trend: topic mastery is a mutable float, so the moment it changes the
previous value is gone. A trend therefore needs storage, and this is the only
place in the Prepare domain that keeps history.

Snapshots are written from `prep_readiness`, the same helper that serves live
reads, so a stored day can never disagree with what the dashboard showed that day.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..repository import personal_learning_repo as repo
from . import prep_readiness

logger = logging.getLogger(__name__)

# How far back a trend may be requested. A chart does not need a preparation's
# entire history, and an unbounded range is an unbounded query.
MAX_TREND_DAYS = 180
DEFAULT_TREND_DAYS = 30

# Preparations processed per batch by the daily writer, so memory does not scale
# with the number of learners.
_BATCH_SIZE = 100


def _snapshot_values(progress: prep_readiness.PrepProgress) -> dict[str, Any]:
    """The fields a snapshot stores, taken straight from the shared helper.

    Nullable percentages are passed through as `None` rather than coerced to zero:
    a preparation with no topics has no measurable readiness, and a chart should
    show no point rather than a point at the bottom.
    """
    return {
        "progress_percent": progress.progress_percent,
        "average_mastery_percent": progress.average_mastery_percent,
        "topics_total": progress.topics_total,
        "topics_strong": progress.topics_strong,
        "topics_focus": progress.topics_focus,
        "topics_assessed": progress.topics_assessed,
        "questions_answered": progress.questions_answered,
        "accuracy_percent": progress.accuracy_percent,
        "quizzes_taken": progress.quizzes_taken,
    }


async def capture_for_preparations(prep_ids: list[str], *, captured_on: date | None = None) -> int:
    """Snapshot the given preparations for one day. Returns rows written.

    Progress for the whole batch is loaded in a fixed number of queries, so a
    larger batch does not mean more round trips.
    """
    if not prep_ids:
        return 0

    day = captured_on or datetime.now(UTC).date()
    progress_by_prep = await prep_readiness.load_for_preparations(prep_ids)

    written = 0
    for prep_id in prep_ids:
        progress = progress_by_prep.get(prep_id)
        if progress is None:
            continue
        try:
            await repo.upsert_readiness_snapshot(
                prep_id=prep_id,
                captured_on=day,
                values=_snapshot_values(progress),
            )
            written += 1
        except Exception:
            # One preparation failing must not cost the rest of the batch.
            logger.exception("Failed to write readiness snapshot", extra={"prep_id": prep_id})
    return written


async def capture_all(*, captured_on: date | None = None) -> tuple[int, int]:
    """Snapshot every unfinished preparation. Returns ``(written, seen)``.

    Batched so memory does not scale with the number of preparations.
    """
    day = captured_on or datetime.now(UTC).date()
    written = 0
    seen = 0
    skip = 0

    while True:
        preparations = await repo.list_snapshot_candidate_preps(skip=skip, take=_BATCH_SIZE)
        if not preparations:
            break

        seen += len(preparations)
        written += await capture_for_preparations(
            [preparation.id for preparation in preparations], captured_on=day
        )

        if len(preparations) < _BATCH_SIZE:
            break
        skip += _BATCH_SIZE

    return written, seen


async def get_trend(
    *, user_id: str, prep_id: str, days: int = DEFAULT_TREND_DAYS
) -> dict[str, Any]:
    """A preparation's readiness trend over a bounded window.

    Returns only what the snapshot table holds. **A new preparation has no
    history**, and this reports that as an empty series rather than inventing a
    starting point or projecting backwards from today's value — either would be
    fabricating the very thing the chart claims to show.
    """
    from src.shared.exceptions import NotFoundError

    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    window = max(1, min(days, MAX_TREND_DAYS))
    since = datetime.now(UTC).date() - timedelta(days=window)
    snapshots = await repo.list_readiness_snapshots(prep_id, since=since)

    return {
        "preparationId": prep_id,
        "days": window,
        "points": [
            {
                "capturedOn": snapshot.captured_on,
                "progressPercent": snapshot.progress_percent,
                "averageMasteryPercent": snapshot.average_mastery_percent,
                "topicsTotal": snapshot.topics_total,
                "topicsStrong": snapshot.topics_strong,
                "topicsFocus": snapshot.topics_focus,
                "topicsAssessed": snapshot.topics_assessed,
                "questionsAnswered": snapshot.questions_answered,
                "accuracyPercent": snapshot.accuracy_percent,
                "quizzesTaken": snapshot.quizzes_taken,
            }
            for snapshot in snapshots
        ],
    }
