"""Every number a reflection reports, measured from persisted rows.

This module exists because the reflection service used to ask a language model for its
metrics. The model was given the behaviour profile and nothing else, then asked for
`topics_studied`, `sessions_completed`, `notes_created`, `total_minutes`,
`concepts_mastered`, `retention_score`, `streak_days` and `milestones` — counts for data it
had never seen — and the answers were persisted as though measured. The rule that replaced
that is: **the model narrates, and never supplies a number.** Everything below is the other
half of that rule.

Shaped like `behaviour_service`: a loader that gathers evidence, and a **pure** function that
turns evidence into metrics. The split is not decoration. Database tests in this repository
are opt-in (`RUN_DB_TESTS=1`), so arithmetic behind a live query is arithmetic that does not
run in CI, and this is arithmetic where being wrong is indistinguishable from being right
without checking.

Three honesty rules run through all of it:

1. **`None` is not `0`.** A learner who reviewed no cards and a learner whose card count was
   never computed are different situations. The old failure path wrote zeros for both, which
   is what made a broken generation look like an inactive week.
2. **Nothing is inferred from a proxy.** Where a number cannot be measured, it stays `None`
   and the reason is written down here rather than filled with something plausible.
3. **A claim about the learner's day needs to know their day.** Active days and the strongest
   day are calendar questions, so they are resolved through the learner's timezone, and the
   strongest day is withheld entirely when that timezone was never captured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select

from src.domains.knowledge.db_models import (
    Course,
    Module,
    Topic,
    TopicCheckAttempt,
    UserTopicProgress,
)
from src.domains.progress.db_models import Achievement, Goal, StudySession, UserStreak
from src.shared.database import get_session_factory
from src.shared.time import LearnerTimezone, resolve_learner_timezone, to_learner_local

from .. import models
from ..db_models import FlashcardReview, LearningProfile, Note, QuizAnswer, QuizSession

# A shared formatter that happens to live in the prose module because the prompts needed it first.
# Imported rather than duplicated: it exists to stop a division printing as `57.1429`, and a second
# copy of that rule is the same defect waiting on the other code path — which is the reasoning its own
# docstring gives for being shared between the two prompts. `reflection_narrative` imports nothing
# from here, so this direction closes no cycle.
from .reflection_narrative import render_figure

logger = logging.getLogger(__name__)

#: A review at or above this grade counts as recalled. Mirrors `flashcard_service`, which
#: stores the verdict per row as `wasLapse` so that retuning the threshold does not
#: retroactively rewrite history.
LAPSE_QUALITY_THRESHOLD = 3


@dataclass
class MetricEvidence:
    """What the database says about one learner over one period.

    Deliberately flat and free of ORM rows: the pure function below must be constructible in
    a test without importing a session, and a dataclass of primitives is what makes that
    cheap enough that the tests actually get written.
    """

    #: Instants of anything that counts as showing up. Used only for calendar-day questions.
    activity_instants: list[datetime] = field(default_factory=list)

    #: Minutes anyone actually reported. `None` means no source reported any — which is not
    #: the same as a learner having spent no time, and must not be rendered as zero.
    tracked_minutes: float | None = None

    study_sessions_ended: int = 0
    quizzes_completed: int = 0
    quiz_answers_total: int = 0
    quiz_answers_correct: int = 0

    reviews_total: int = 0
    reviews_lapsed: int = 0

    notes_created: int = 0

    #: Topic ids touched in any way during the period.
    topics_touched: set[str] = field(default_factory=set)
    #: Titles of topics completed during the period, for the "Topics Mastered" chips.
    topics_mastered: list[str] = field(default_factory=list)

    #: Denominator for overall mastery: topics in the learner's own unarchived courses.
    own_topics_total: int = 0
    own_topics_done_at_start: int = 0
    own_topics_done_at_end: int = 0

    #: `None` when the learner has no streak row at all, i.e. never studied.
    streak_current: int | None = None
    streak_best: int | None = None

    milestones: list[str] = field(default_factory=list)

    #: Read from `LearningProfile`, never recomputed here. One definition, one writer.
    consistency_score: float | None = None
    average_session_minutes: float | None = None
    best_day_of_week: str | None = None


def compute(evidence: MetricEvidence, timezone_: LearnerTimezone) -> models.ReflectionMetrics:
    """Turn evidence into metrics. Pure, so the arithmetic is testable without a database."""
    active_days = len({to_learner_local(i, timezone_).date() for i in evidence.activity_instants})
    had_activity = bool(evidence.activity_instants)

    return models.ReflectionMetrics(
        # --- Activities
        # Rounded to a whole minute, and `None` rather than `0` when nothing reported a
        # duration. `QuizSession.duration_seconds` is nullable and only set when the client
        # sends it, and `StudySession` is written by one endpoint that most learners never
        # touch — so absent time means untracked far more often than it means idle.
        focused_minutes=(
            int(round(evidence.tracked_minutes)) if evidence.tracked_minutes is not None else None
        ),
        # Zero active days is a real finding once there is any evidence at all; before that
        # it is silence.
        active_days=active_days if had_activity else None,
        # A sitting that finished. Counts ended study sessions and completed quizzes, which
        # can double-count a learner who runs a quiz inside a tracked session — rare, since
        # almost nothing writes `StudySession`, and preferable to dropping either source.
        sessions_completed=(
            evidence.study_sessions_ended + evidence.quizzes_completed if had_activity else None
        ),
        topics_studied=len(evidence.topics_touched) if had_activity else None,
        notes_created=evidence.notes_created if had_activity else None,
        flashcards_reviewed=evidence.reviews_total if had_activity else None,
        quizzes_completed=evidence.quizzes_completed if had_activity else None,
        # --- Progress
        topics_mastered=len(evidence.topics_mastered) if had_activity else None,
        new_topics_mastered=evidence.topics_mastered if had_activity else None,
        mastery_gained_percent=_mastery_gain(evidence),
        recall_percent=recall_percent(evidence),
        accuracy_percent=_accuracy_percent(evidence),
        consistency_score=evidence.consistency_score,
        average_session_minutes=evidence.average_session_minutes,
        # Withheld when the timezone was never captured. "Your strongest day is Thursday" is
        # a claim about the learner's own calendar, and `UserPreferences.timezone` is NOT NULL
        # with a `"UTC"` default — so an unresolved learner looks like a learner in London and
        # the claim would be confidently wrong for most of the world.
        best_day=evidence.best_day_of_week if timezone_.is_known else None,
        # Left unmeasured on purpose. `Goal.updatedAt` moves when a title is edited, so
        # counting it would report renamed goals as advanced. A real count needs the progress
        # history the daily snapshot introduces.
        goals_advanced=None,
        # --- Achievements
        streak_current=evidence.streak_current,
        streak_best=evidence.streak_best,
        milestones_reached=evidence.milestones if had_activity else None,
    )


def _mastery_gain(evidence: MetricEvidence) -> float | None:
    """Percentage points of course completion gained across the period.

    Reconstructed from `Topic.completedAt` rather than read from a stored history, which does
    not exist yet. Two known distortions, both understating: the denominator is *today's*
    topic count, so topics added since the period began make earlier progress look smaller;
    and a topic completed and later reopened has lost its `completedAt`, so it reads as never
    done. Understating is the tolerable direction — the trend never invents growth.
    """
    if evidence.own_topics_total <= 0:
        return None
    start = evidence.own_topics_done_at_start / evidence.own_topics_total * 100
    end = evidence.own_topics_done_at_end / evidence.own_topics_total * 100
    return round(end - start, 1)


def recall_percent(evidence: MetricEvidence) -> float | None:
    """Share of reviews the learner recalled, from the stored per-row verdict.

    Public, unlike its siblings, because the daily snapshot writer needs the same figure for a
    single day. One definition rather than two: a snapshot's recall and a reflection's recall
    describe the same thing over different windows, and if they drifted apart the growth curve
    would disagree with the reflection that narrates it.
    """
    if evidence.reviews_total <= 0:
        return None
    recalled = evidence.reviews_total - evidence.reviews_lapsed
    return round(recalled / evidence.reviews_total * 100, 1)


def _accuracy_percent(evidence: MetricEvidence) -> float | None:
    """Share of quiz answers that were correct."""
    if evidence.quiz_answers_total <= 0:
        return None
    return round(evidence.quiz_answers_correct / evidence.quiz_answers_total * 100, 1)


async def load_evidence(
    *, user_id: str, period_start: datetime, period_end: datetime
) -> MetricEvidence:
    """Gather one learner's period in a single session.

    One session and one pass rather than a repository method per metric: these are a dozen
    small aggregates over five domains for one screen, and threading them through
    `PersonalLearningRepository` would add a dozen methods with one caller each.
    `progress/services/weekly_summary.py` reads across domains the same way.
    """
    evidence = MetricEvidence()
    factory = get_session_factory()

    async with factory() as session:
        # --- Tracked study sessions (progress domain; written only by /progress/sessions) ---
        study_rows = (
            await session.execute(
                select(
                    StudySession.start_time,
                    StudySession.end_time,
                    StudySession.duration,
                    StudySession.topic_id,
                ).where(
                    StudySession.user_id == user_id,
                    StudySession.start_time >= period_start,
                    StudySession.start_time <= period_end,
                )
            )
        ).all()

        minutes: float | None = None
        for start_time, end_time, duration, topic_id in study_rows:
            evidence.activity_instants.append(start_time)
            if end_time is not None:
                evidence.study_sessions_ended += 1
            # `duration` is minutes here, unlike `QuizSession.duration_seconds`.
            if duration:
                minutes = (minutes or 0.0) + float(duration)
            if topic_id:
                evidence.topics_touched.add(topic_id)

        # --- Quiz sessions ------------------------------------------------
        quiz_rows = (
            await session.execute(
                select(
                    QuizSession.created_at,
                    QuizSession.status,
                    QuizSession.duration_seconds,
                    QuizSession.topic_id,
                ).where(
                    QuizSession.user_id == user_id,
                    QuizSession.created_at >= period_start,
                    QuizSession.created_at <= period_end,
                )
            )
        ).all()

        for created_at, status, duration_seconds, topic_id in quiz_rows:
            if created_at is None:
                continue
            evidence.activity_instants.append(created_at)
            if status == "COMPLETED":
                evidence.quizzes_completed += 1
            if duration_seconds and duration_seconds > 0:
                minutes = (minutes or 0.0) + duration_seconds / 60.0
            if topic_id:
                evidence.topics_touched.add(topic_id)

        evidence.tracked_minutes = minutes

        # --- Quiz answers -------------------------------------------------
        # Joined through `QuizSession`, because `QuizAnswer` carries no `userId`. Filtering it
        # on its own would aggregate every learner's answers.
        answer_row = (
            await session.execute(
                select(
                    func.count(QuizAnswer.id),
                    func.count(QuizAnswer.id).filter(QuizAnswer.is_correct.is_(True)),
                )
                .select_from(QuizAnswer)
                .join(QuizSession, QuizAnswer.quiz_session_id == QuizSession.id)
                .where(
                    QuizSession.user_id == user_id,
                    QuizAnswer.created_at >= period_start,
                    QuizAnswer.created_at <= period_end,
                )
            )
        ).one()
        evidence.quiz_answers_total = int(answer_row[0] or 0)
        evidence.quiz_answers_correct = int(answer_row[1] or 0)

        # --- Flashcard reviews --------------------------------------------
        # One row per grade, which is why frequency is answerable at all: `Flashcard`
        # keeps only the latest review, so re-reviewing a card would erase the day it
        # belonged to and could shorten a learner's streak by studying.
        review_rows = (
            await session.execute(
                select(FlashcardReview.reviewed_at, FlashcardReview.was_lapse).where(
                    FlashcardReview.user_id == user_id,
                    FlashcardReview.reviewed_at >= period_start,
                    FlashcardReview.reviewed_at <= period_end,
                )
            )
        ).all()
        for reviewed_at, was_lapse in review_rows:
            evidence.activity_instants.append(reviewed_at)
            evidence.reviews_total += 1
            if was_lapse:
                evidence.reviews_lapsed += 1

        # --- Notes --------------------------------------------------------
        evidence.notes_created = int(
            (
                await session.execute(
                    select(func.count(Note.id)).where(
                        Note.user_id == user_id,
                        Note.created_at >= period_start,
                        Note.created_at <= period_end,
                    )
                )
            ).scalar()
            or 0
        )

        # --- Topic completions in the learner's own courses ---------------
        own_topics = (
            select(Topic.id, Topic.title, Topic.completed, Topic.completed_at)
            .select_from(Topic)
            .join(Module, Topic.module_id == Module.id)
            .join(Course, Module.course_id == Course.id)
            .where(Course.user_id == user_id, Course.archived.is_(False))
        )
        for topic_id, title, completed, completed_at in (await session.execute(own_topics)).all():
            evidence.own_topics_total += 1
            if not completed or completed_at is None:
                continue
            if completed_at <= period_start:
                evidence.own_topics_done_at_start += 1
            if completed_at <= period_end:
                evidence.own_topics_done_at_end += 1
            if period_start < completed_at <= period_end:
                evidence.topics_mastered.append(title)
                evidence.topics_touched.add(topic_id)
                evidence.activity_instants.append(completed_at)

        # --- Topic completions in shared courses --------------------------
        # Counted as achievements but deliberately absent from the mastery denominator: a
        # shared course's size belongs to its owner, so folding it in would make the
        # learner's own progress ratio move when someone else adds a topic.
        shared_rows = (
            await session.execute(
                select(
                    UserTopicProgress.topic_id,
                    Topic.title,
                    UserTopicProgress.completed_at,
                )
                .join(Topic, UserTopicProgress.topic_id == Topic.id)
                .where(
                    UserTopicProgress.user_id == user_id,
                    UserTopicProgress.completed.is_(True),
                    UserTopicProgress.completed_at > period_start,
                    UserTopicProgress.completed_at <= period_end,
                )
            )
        ).all()
        for topic_id, title, completed_at in shared_rows:
            evidence.topics_mastered.append(title)
            evidence.topics_touched.add(topic_id)
            if completed_at is not None:
                evidence.activity_instants.append(completed_at)

        # --- Knowledge checks attempted -----------------------------------
        check_rows = (
            await session.execute(
                select(TopicCheckAttempt.topic_id, TopicCheckAttempt.created_at).where(
                    TopicCheckAttempt.user_id == user_id,
                    TopicCheckAttempt.created_at >= period_start,
                    TopicCheckAttempt.created_at <= period_end,
                )
            )
        ).all()
        for topic_id, created_at in check_rows:
            evidence.topics_touched.add(topic_id)
            evidence.activity_instants.append(created_at)

        # --- Streak -------------------------------------------------------
        # `None` when there is no row: never studied, which the design must be able to show
        # differently from a streak that lapsed to zero.
        streak = (
            await session.execute(select(UserStreak).where(UserStreak.user_id == user_id))
        ).scalar_one_or_none()
        if streak is not None:
            evidence.streak_current = streak.current_streak
            evidence.streak_best = streak.longest_streak

        # --- Milestones ---------------------------------------------------
        # `Achievement` is the single milestone source for Reflect. `LearningMilestone` exists
        # for the share flow and is not read here; two surfaces reading two tables would show
        # a milestone in one place and not the other.
        evidence.milestones = [
            title
            for (title,) in (
                await session.execute(
                    select(Achievement.title)
                    .where(
                        Achievement.user_id == user_id,
                        Achievement.unlocked_at >= period_start,
                        Achievement.unlocked_at <= period_end,
                    )
                    .order_by(Achievement.unlocked_at.desc())
                )
            ).all()
        ]

        # --- Behaviour, read not recomputed -------------------------------
        profile_row = (
            await session.execute(
                select(
                    LearningProfile.consistency_score,
                    LearningProfile.avg_session_minutes,
                    LearningProfile.best_day_of_week,
                ).where(LearningProfile.user_id == user_id)
            )
        ).one_or_none()
        if profile_row is not None:
            evidence.consistency_score = profile_row[0]
            evidence.average_session_minutes = profile_row[1]
            evidence.best_day_of_week = profile_row[2]

    return evidence


async def compute_metrics(
    *, user_id: str, period_start: datetime, period_end: datetime
) -> models.ReflectionMetrics:
    """The learner's measured metrics for a period.

    Never raises for want of data: a learner with no history gets an all-null metrics object,
    which is the honest description of that state.
    """
    evidence = await load_evidence(
        user_id=user_id, period_start=period_start, period_end=period_end
    )
    timezone_ = await resolve_learner_timezone(user_id)
    return compute(evidence, timezone_)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """`1 day`, `2 days`. These strings are rendered verbatim on the reflection page, so
    "1 active days" is a visible defect rather than a cosmetic one."""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def build_highlights(metrics: models.ReflectionMetrics) -> list[str]:
    """The short evidence chips beside the narrative, formatted from measurements.

    Built here rather than by the model. They read as facts — "12-day learning streak" — so a
    model writing them would be inventing statistics in the one place they look most credible.

    Only measured values appear. A null metric produces no chip rather than a chip saying
    zero, which is why this returns a variable-length list and the design tolerates that.
    """
    highlights: list[str] = []
    if metrics.streak_current:
        highlights.append(f"{metrics.streak_current}-day learning streak")
    if metrics.focused_minutes:
        hours, minutes = divmod(metrics.focused_minutes, 60)
        highlights.append(f"{hours}h {minutes:02d}m focused" if hours else f"{minutes}m focused")
    if metrics.active_days:
        highlights.append(_plural(metrics.active_days, "active day"))
    if metrics.topics_mastered:
        highlights.append(_plural(metrics.topics_mastered, "topic") + " mastered")
    if metrics.recall_percent is not None:
        highlights.append(f"{metrics.recall_percent:g}% recall")
    if metrics.flashcards_reviewed:
        highlights.append(_plural(metrics.flashcards_reviewed, "card") + " reviewed")
    return highlights[:4]


def _format_minutes(minutes: float) -> str:
    hours, remainder = divmod(int(minutes), 60)
    return f"{hours}h {remainder:02d}m" if hours else f"{remainder}m"


def build_measured_summary(type_: models.ReflectionType, metrics: models.ReflectionMetrics) -> str:
    """The reflection's opening paragraph, composed from measurements with no model call.

    **This is the free tier's summary, and it exists because the alternative was error copy.**
    Decision M rule 2 asks for the free weekly reflection to be composed with no model call at all.
    The only non-model string in this module was `_fallback_summary` — "the narrative could not be
    generated this time" — which is an *error* message, so using it as the free version would have
    told every free learner their reflection had failed. That was the whole of the blockage; the
    content itself was already measured and sitting in `compute_metrics`.

    It is also the better failure path for **Plus**. A subscriber whose narrative generation fails
    used to get the apology; now they get their actual figures, which is what they came for. One
    function, both tiers, and the apology is left to the one case that is genuinely an error.

    Two sentences at most: effort, then outcome. Written from the same measurements `build_highlights`
    formats into chips, but as prose rather than a list — a summary that reads as a comma-separated
    dump of statistics is what the chips are already for.

    Only measured values appear. A learner with nothing measured gets an honest sentence about a quiet
    period rather than a paragraph of zeros, because "0 topics mastered, 0% recall" reads as a
    judgement where "a quiet week" reads as a fact.
    """
    period = "week" if type_ is models.ReflectionType.WEEKLY else "month"

    # Sentence one: what the learner put in.
    effort: list[str] = []
    if metrics.focused_minutes:
        effort.append(f"{_format_minutes(metrics.focused_minutes)} of focused study")
    if metrics.active_days:
        effort.append(f"across {_plural(metrics.active_days, 'day')}")
    if not effort and metrics.sessions_completed:
        effort.append(_plural(metrics.sessions_completed, "study session"))

    # Sentence two: what came of it. Recall and accuracy first, because they are the only two figures
    # here that describe how well the time went rather than how much of it there was.
    outcome: list[str] = []
    if metrics.recall_percent is not None:
        outcome.append(f"recall was {render_figure(metrics.recall_percent)}%")
    if metrics.accuracy_percent is not None:
        outcome.append(f"quiz accuracy {render_figure(metrics.accuracy_percent)}%")
    if metrics.topics_mastered:
        outcome.append(f"you completed {_plural(metrics.topics_mastered, 'topic')}")
    if metrics.flashcards_reviewed:
        outcome.append(f"{_plural(metrics.flashcards_reviewed, 'card')} reviewed")

    if not effort and not outcome:
        return (
            f"A quiet {period} — nothing was tracked. "
            "Your figures pick up again as soon as you study."
        )

    sentences: list[str] = []
    if effort:
        sentences.append(f"You logged {' '.join(effort)} this {period}.")
    if outcome:
        # Capped at three so the sentence stays a sentence. The chips carry the rest.
        joined = outcome[:3]
        if len(joined) == 1:
            body = joined[0]
        else:
            body = ", ".join(joined[:-1]) + f", and {joined[-1]}"
        lead = "Along the way, " if effort else "This period, "
        sentences.append(lead + body + ".")
    if metrics.streak_current and metrics.streak_current > 1:
        sentences.append(f"That keeps a {metrics.streak_current}-day streak going.")
    return " ".join(sentences)


async def count_reflection_streak(*, user_id: str) -> int | None:
    """Consecutive periods the learner engaged with, most recent first.

    Counts reflections with a non-null `openedAt`, not reflections that exist. A weekly
    reflection is produced *for* the learner by a Sunday task, so counting rows would measure
    the scheduler rather than the learner — and the book is explicit that the point is
    meaningful progress rather than activity, which rules out a streak nobody had to do
    anything to earn.

    `None` when nothing has ever been opened, `0` once a streak has lapsed, so "never
    engaged" stays distinguishable from "broke a streak".
    """
    from ..repository import personal_learning_repo as repo

    opened = await repo.list_opened_reflection_periods(
        user_id, type_filter=models.ReflectionType.WEEKLY.value
    )
    if not opened:
        return None

    # Walk back a week at a time from the most recent opened period. A gap ends the run.
    ordered = sorted(opened, reverse=True)
    now = datetime.now(UTC)
    # A run that ended more than two periods ago has lapsed.
    if (now - ordered[0]).days > 14:
        return 0

    streak = 1
    for previous, current in zip(ordered[1:], ordered, strict=False):
        if 0 < (current - previous).days <= 10:
            streak += 1
        else:
            break
    return streak


async def load_daily_evidence(
    *,
    user_id: str,
    period_start: datetime,
    period_end: datetime,
    timezone_: LearnerTimezone,
) -> dict[date, MetricEvidence]:
    """One learner's whole window, bucketed into learner-local days.

    **Exists because calling `load_evidence` per day does not scale, and the arithmetic is not
    close.** `load_evidence` issues about eleven queries for one period. Reconstructing ninety days
    through it means ninety times that — roughly 1,260 round trips per learner over the same rows,
    which measured at 26 minutes for six learners against a ~209 ms round trip, and would be some
    seventy hours at a thousand learners. This makes one pass per source over the entire window and
    groups the rows in memory, which is the same trade `load_local_session_times` already makes for
    the consistency replay: one query and ninety filters beat ninety queries.

    **Returns a deliberately partial `MetricEvidence` per day**, populating exactly the fields the
    daily snapshot reads: `activity_instants`, `tracked_minutes`, `study_sessions_ended`,
    `quizzes_completed`, `quiz_answers_total`, `reviews_total`, `reviews_lapsed`, `topics_touched`
    and `topics_mastered`. The rest — `notes_created`, the `own_topics_*` denominators, `streak_*`,
    `milestones`, and the behaviour figures — are left at their defaults, because a *day* is the
    wrong window for most of them and the snapshot does not store them. `load_evidence` remains the
    authority for a reflection period. A test pins that `snapshot_values` reads nothing outside the
    populated set, so this cannot rot into silently returning zeros for a field someone starts using.

    Days with no evidence at all are still present in the result, holding an empty `MetricEvidence`.
    A learner's day off is a fact that needs a row, not a gap.
    """
    days: dict[date, MetricEvidence] = {}

    def bucket(instant: datetime) -> MetricEvidence:
        day = to_learner_local(instant, timezone_).date()
        if day not in days:
            days[day] = MetricEvidence()
        return days[day]

    factory = get_session_factory()
    async with factory() as session:
        # --- Tracked study sessions ---
        study_rows = (
            await session.execute(
                select(
                    StudySession.start_time,
                    StudySession.end_time,
                    StudySession.duration,
                    StudySession.topic_id,
                ).where(
                    StudySession.user_id == user_id,
                    StudySession.start_time >= period_start,
                    StudySession.start_time <= period_end,
                )
            )
        ).all()
        for start_time, end_time, duration, topic_id in study_rows:
            evidence = bucket(start_time)
            evidence.activity_instants.append(start_time)
            if end_time is not None:
                evidence.study_sessions_ended += 1
            if duration:
                evidence.tracked_minutes = (evidence.tracked_minutes or 0.0) + float(duration)
            if topic_id:
                evidence.topics_touched.add(topic_id)

        # --- Quiz sessions ---
        quiz_rows = (
            await session.execute(
                select(
                    QuizSession.created_at,
                    QuizSession.status,
                    QuizSession.duration_seconds,
                    QuizSession.topic_id,
                ).where(
                    QuizSession.user_id == user_id,
                    QuizSession.created_at >= period_start,
                    QuizSession.created_at <= period_end,
                )
            )
        ).all()
        for created_at, status, duration_seconds, topic_id in quiz_rows:
            if created_at is None:
                continue
            evidence = bucket(created_at)
            evidence.activity_instants.append(created_at)
            if status == "COMPLETED":
                evidence.quizzes_completed += 1
            if duration_seconds and duration_seconds > 0:
                evidence.tracked_minutes = (
                    evidence.tracked_minutes or 0.0
                ) + duration_seconds / 60.0
            if topic_id:
                evidence.topics_touched.add(topic_id)

        # --- Quiz answers ---
        # Timestamps rather than a count, because the count has to be split across days here.
        # Joined through `QuizSession`, since `QuizAnswer` carries no `userId`.
        answer_rows = (
            await session.execute(
                select(QuizAnswer.created_at)
                .select_from(QuizAnswer)
                .join(QuizSession, QuizAnswer.quiz_session_id == QuizSession.id)
                .where(
                    QuizSession.user_id == user_id,
                    QuizAnswer.created_at >= period_start,
                    QuizAnswer.created_at <= period_end,
                )
            )
        ).all()
        for (created_at,) in answer_rows:
            if created_at is not None:
                bucket(created_at).quiz_answers_total += 1

        # --- Flashcard reviews ---
        review_rows = (
            await session.execute(
                select(FlashcardReview.reviewed_at, FlashcardReview.was_lapse).where(
                    FlashcardReview.user_id == user_id,
                    FlashcardReview.reviewed_at >= period_start,
                    FlashcardReview.reviewed_at <= period_end,
                )
            )
        ).all()
        for reviewed_at, was_lapse in review_rows:
            evidence = bucket(reviewed_at)
            evidence.activity_instants.append(reviewed_at)
            evidence.reviews_total += 1
            if was_lapse:
                evidence.reviews_lapsed += 1

        # --- Topic completions in the learner's own courses ---
        own_rows = (
            await session.execute(
                select(Topic.id, Topic.title, Topic.completed_at)
                .select_from(Topic)
                .join(Module, Topic.module_id == Module.id)
                .join(Course, Module.course_id == Course.id)
                .where(
                    Course.user_id == user_id,
                    Course.archived.is_(False),
                    Topic.completed_at.is_not(None),
                    Topic.completed_at > period_start,
                    Topic.completed_at <= period_end,
                )
            )
        ).all()
        for topic_id, title, completed_at in own_rows:
            evidence = bucket(completed_at)
            evidence.topics_mastered.append(title)
            evidence.topics_touched.add(topic_id)
            evidence.activity_instants.append(completed_at)

        # --- Topic completions in shared courses ---
        shared_rows = (
            await session.execute(
                select(
                    UserTopicProgress.topic_id,
                    Topic.title,
                    UserTopicProgress.completed_at,
                )
                .join(Topic, UserTopicProgress.topic_id == Topic.id)
                .where(
                    UserTopicProgress.user_id == user_id,
                    UserTopicProgress.completed.is_(True),
                    UserTopicProgress.completed_at > period_start,
                    UserTopicProgress.completed_at <= period_end,
                )
            )
        ).all()
        for topic_id, title, completed_at in shared_rows:
            if completed_at is None:
                continue
            evidence = bucket(completed_at)
            evidence.topics_mastered.append(title)
            evidence.topics_touched.add(topic_id)
            evidence.activity_instants.append(completed_at)

        # --- Knowledge checks attempted ---
        check_rows = (
            await session.execute(
                select(TopicCheckAttempt.topic_id, TopicCheckAttempt.created_at).where(
                    TopicCheckAttempt.user_id == user_id,
                    TopicCheckAttempt.created_at >= period_start,
                    TopicCheckAttempt.created_at <= period_end,
                )
            )
        ).all()
        for topic_id, created_at in check_rows:
            evidence = bucket(created_at)
            evidence.topics_touched.add(topic_id)
            evidence.activity_instants.append(created_at)

    return days
