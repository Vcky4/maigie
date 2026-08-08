"""Single source of truth for preparation progress and readiness.

Every surface that reports on a preparation reads from here, so the Learn
dashboard and the Prepare surface cannot drift into showing different numbers
for the same preparation.

# The mastery ladder

Topic mastery (0-100) is divided by two boundaries into three bands. Both
boundaries already existed in the codebase and are unchanged; this module only
names them and puts them in one place:

    focus       mastery < 70     what WEAK_AREAS practice already selects
    review      70 <= m < 80     the band between the two, previously unnamed
    strong      mastery >= 80    what _update_topic_mastery already calls MASTERED

# The two numbers, and why they are different

`progress_percent` is `topicsStrong / topicsTotal`. It is the headline number
and is shared with the Learn dashboard, where a path card renders the percent
directly above an "x / y complete" line. Anything other than this ratio would
make that card contradict itself.

`average_mastery_percent` is the mean of every topic's mastery. It is the better
readiness signal because it has no cliff at the 80 boundary, but it must never
be presented as "progress" next to those unit counts. The Prepare surface has
room to label it separately.

`topics_assessed` exists so a surface can say "based on 4 of 12 topics
practised" rather than letting an unpractised preparation read as though the
learner knows nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from ..repository import personal_learning_repo as repo

# A topic is considered strong, i.e. exam-ready. Matches the MASTERED label
# written by quiz_engine._update_topic_mastery.
MASTERY_STRONG_THRESHOLD = 80.0
# Below this a topic is prioritised for practice. Matches the WEAK_AREAS filter
# in quiz_engine.start_quiz.
MASTERY_FOCUS_THRESHOLD = 70.0

MasteryBand = Literal["focus", "review", "strong"]


def mastery_band(mastery_score: float | None) -> MasteryBand:
    """Classify a topic's mastery into the shared three-band ladder."""
    score = mastery_score or 0.0
    if score >= MASTERY_STRONG_THRESHOLD:
        return "strong"
    if score >= MASTERY_FOCUS_THRESHOLD:
        return "review"
    return "focus"


def _clamp_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return _clamp_percent((part / whole) * 100)


@dataclass(frozen=True)
class PrepProgress:
    """Derived progress for one preparation. No column stores any of this."""

    topics_total: int
    topics_strong: int
    topics_focus: int
    topics_assessed: int
    questions_answered: int
    questions_correct: int
    quizzes_taken: int
    practice_seconds: int

    @property
    def progress_percent(self) -> float:
        """Headline number, shared with the Learn dashboard."""
        return _percent(self.topics_strong, self.topics_total)

    @property
    def average_mastery_percent(self) -> float | None:
        """Mean topic mastery. `None` when there are no topics to average.

        Reported as `None` rather than `0` so "not measured yet" is
        distinguishable from "measured and scoring zero".

        The mean covers *all* topics, including unpractised ones: untested
        material is a real readiness risk, so averaging only practised topics
        would overstate readiness.
        """
        if self.topics_total <= 0:
            return None
        return _clamp_percent(self.mastery_sum / self.topics_total)

    @property
    def accuracy_percent(self) -> float | None:
        """`None` until at least one question has been answered."""
        if self.questions_answered <= 0:
            return None
        return _percent(self.questions_correct, self.questions_answered)

    @property
    def practice_ready(self) -> bool:
        """Whether practice can start.

        Quiz generation requires topics, so without them `start_quiz` returns a
        `PREP_TOPICS_REQUIRED` conflict. Surfaces use this to route the learner
        to topic extraction instead of into a failing request.
        """
        return self.topics_total > 0

    #: Sum of every topic's mastery, used to derive the mean. Carried rather
    #: than recomputed so the average is not re-derived from rounded values.
    mastery_sum: float = 0.0


def _build(aggregate: dict[str, Any] | None) -> PrepProgress:
    data = aggregate or {}
    return PrepProgress(
        topics_total=int(data.get("topics_total", 0) or 0),
        topics_strong=int(data.get("topics_strong", 0) or 0),
        topics_focus=int(data.get("topics_focus", 0) or 0),
        topics_assessed=int(data.get("topics_assessed", 0) or 0),
        questions_answered=int(data.get("answers_total", 0) or 0),
        questions_correct=int(data.get("answers_correct", 0) or 0),
        quizzes_taken=int(data.get("quizzes_completed", 0) or 0),
        practice_seconds=int(data.get("practice_seconds", 0) or 0),
        mastery_sum=float(data.get("mastery_sum", 0.0) or 0.0),
    )


# How far back the practice streak is allowed to look. A streak longer than this
# is reported as this value; the window keeps the query bounded and no learner is
# materially misrepresented by it.
PRACTICE_STREAK_WINDOW_DAYS = 120


def practice_streak(practice_days: Sequence[date], *, today: date) -> int | None:
    """Consecutive days of completed practice, ending today or yesterday.

    `practice_days` is the set of days the learner completed at least one quiz
    session, newest first. Pure so the run-length rule is testable without a
    database.

    Returns `None` when the learner has never completed a session, and `0` when a
    streak has lapsed, so "never practised" stays distinguishable from "broke a
    streak" — the same not-measured-versus-zero rule the rest of this module uses.

    **Yesterday still counts.** A learner who practised yesterday but not yet
    today has not broken anything; treating that as `0` would pressure them into
    practising to defend a number, which is the behaviour Decision I rules out.
    """
    if not practice_days:
        return None

    unique_days = sorted(set(practice_days), reverse=True)
    most_recent = unique_days[0]

    # More than one day since the last session means the run is over.
    if (today - most_recent).days > 1:
        return 0

    streak = 1
    for previous, current in zip(unique_days, unique_days[1:]):
        if (previous - current).days != 1:
            break
        streak += 1
    return streak


async def load_practice_streak(user_id: str, *, today: date | None = None) -> int | None:
    """Load the learner's practice streak over the bounded window."""
    reference = today or datetime.now(UTC).date()
    since = datetime.now(UTC) - timedelta(days=PRACTICE_STREAK_WINDOW_DAYS)
    days = await repo.list_practice_days(user_id, since=since)
    return practice_streak(days, today=reference)


async def load_for_preparations(prep_ids: list[str]) -> dict[str, PrepProgress]:
    """Load progress for several preparations in a fixed number of queries.

    Callers must have already verified that the preparations belong to the
    authenticated user.
    """
    if not prep_ids:
        return {}
    aggregates = await repo.get_prep_progress_aggregates(
        prep_ids,
        strong_threshold=MASTERY_STRONG_THRESHOLD,
        focus_threshold=MASTERY_FOCUS_THRESHOLD,
    )
    return {prep_id: _build(aggregates.get(prep_id)) for prep_id in prep_ids}


async def load_for_preparation(prep_id: str) -> PrepProgress:
    """Load progress for a single preparation."""
    result = await load_for_preparations([prep_id])
    return result.get(prep_id, _build(None))
