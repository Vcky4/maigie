"""Whether learners answer what this programme asks them.

**The number nobody could produce.** The adaptive goal lifecycle rests on two assumptions that have never
been observed: that a learner will tell us how an exam went, and that a learner will answer a nudge about a
goal falling behind. §6.2's readiness calibration is worthless below some response rate nobody can predict,
and phase 8 depends on nudge answers existing at all. The plan's own §11 says to instrument the ask "from day
one, so the next decision has a number" — and until this module, producing that number meant writing a
bespoke SQL script, which is why nobody did.

## Nothing new is stored

Everything here is derived from columns that already exist, and that is worth stating because the obvious
instinct is to add an events table:

| Fact | Column |
| --- | --- |
| we asked | `ExamPrep.reviewAskedAt`, `GoalLifecycleAction.createdAt` |
| we asked again | `ExamPrep.reviewRemindersSent` |
| they answered | `PrepOutcome.answeredAt`, `GoalLifecycleAction.respondedAt` |
| they declined | `ExamPrep.reviewDeclinedAt` |

`GoalLifecycleAction` even carries a CHECK pairing `respondedAt` with `learnerResponse`, so a reply time
without a reply cannot exist. A new table would have duplicated all of it and introduced the possibility of
the two disagreeing.

## The distinction the whole module exists for

**"Asked and ignored" and "never asked" are different failures with opposite remedies**, and a naive response
rate hides the second inside the first. A preparation whose exam has passed but which carries no
`reviewAskedAt` was never asked — the sweep did not run, or ran before this feature existed, or the
preparation was closed by the old date-based sweep. Reading that as a 0% response rate would send someone off
to redesign copy when the actual bug is that the question never left the building. That is not hypothetical:
this database has 18 preparations completed by the old sweep and 4 that were asked, so a rate computed over
"preparations past their exam" would have read 0% against a denominator that is 82% noise.

So every funnel below reports `never_asked` beside the rate, and the rate's denominator is **asks**, not
candidates.

## A decline is engagement, not silence

`answered` and `declined` are counted separately and both roll up into `engagement_rate`. Tapping "Not now"
is a learner responding to a prompt — it is a worse outcome for calibration and a *good* outcome for
consent, and averaging it with silence would lose the only signal that says the ask was seen.

Pure functions over lightweight rows, so the arithmetic is testable without a database. The reads that build
those rows are at the bottom.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.shared.database import get_session_factory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewAskRow:
    """One preparation whose exam has passed."""

    prep_id: str
    exam_date: datetime
    asked_at: datetime | None
    reminders_sent: int
    declined_at: datetime | None
    answered_at: datetime | None
    status: str


@dataclass(frozen=True)
class NudgeActionRow:
    """One thing the nightly ladder did to one goal."""

    action: str
    trigger: str
    created_at: datetime
    learner_response: str | None
    responded_at: datetime | None


# ---------------------------------------------------------------------------
# Funnels
# ---------------------------------------------------------------------------


@dataclass
class Funnel:
    """A shared shape, because the two asks want comparing.

    The whole point of measuring both is to learn which kind of question gets answered, and two funnels with
    different field names would make that comparison a manual exercise every time.
    """

    #: Everything that could have been asked about.
    candidates: int = 0
    #: Of those, the ones we actually asked. The denominator for every rate here.
    asked: int = 0
    #: Candidates we never asked. **Read this before the rates.** See the module docstring.
    never_asked: int = 0
    answered: int = 0
    declined: int = 0
    #: Asked, and neither answered nor declined.
    silent: int = 0
    #: Days from the ask to the answer, for the ones answered. Kept as the list so a caller can take a
    #: percentile rather than being handed a mean that one six-week reply would drag.
    days_to_answer: list[float] = field(default_factory=list)

    @property
    def response_rate(self) -> float | None:
        """Answered as a fraction of asked, or `None` when nothing was asked.

        `None`, never `0.0`. A programme that has asked nobody has not been ignored, and every other number
        in this module exists to stop that confusion — printing 0% here would put it straight back.
        """
        if self.asked == 0:
            return None
        return self.answered / self.asked

    @property
    def engagement_rate(self) -> float | None:
        """Answered *or* declined, as a fraction of asked.

        A decline is a learner responding. The gap between this and `response_rate` is the population who saw
        the ask and chose not to answer it, which is a copy problem; the gap between this and 1.0 is the
        population the ask never reached, which is a delivery problem. Two different teams.
        """
        if self.asked == 0:
            return None
        return (self.answered + self.declined) / self.asked

    @property
    def median_days_to_answer(self) -> float | None:
        """The middle reply time, or `None` with no replies.

        Median rather than mean: with a handful of answers one reply six weeks later moves a mean more than
        it should, and "how long until we hear back" is a typical-case question.
        """
        if not self.days_to_answer:
            return None
        ordered = sorted(self.days_to_answer)
        middle = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    """Elapsed days, tolerant of the naive timestamps this database stores.

    Several of these columns are `timestamp without time zone` while the ORM declares otherwise, so one side
    of a subtraction can arrive naive and raise. Read as UTC, the same convention `goal_metrics._utc` and
    `prep_outcome_service._as_utc` both apply.
    """
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - start).total_seconds() / 86_400


def review_funnel(rows: list[ReviewAskRow]) -> Funnel:
    """Did learners tell us how their exams went?

    `answered` counts a recorded `PrepOutcome`, not a `COMPLETED` status. The two used to be the same thing
    and are not any more: the 18 preparations the old date-based sweep closed are `COMPLETED` with no outcome
    behind them, and counting those as answers would report a response rate for a question never asked.

    A preparation both declined *and* answered — the learner set it aside and later changed their mind, which
    the review surface deliberately allows — counts as answered. That is the outcome we wanted, and the
    earlier decline is not a separate learner.
    """
    funnel = Funnel(candidates=len(rows))
    for row in rows:
        if row.asked_at is None:
            funnel.never_asked += 1
            continue
        funnel.asked += 1
        if row.answered_at is not None:
            funnel.answered += 1
            elapsed = _days_between(row.asked_at, row.answered_at)
            if elapsed is not None:
                funnel.days_to_answer.append(elapsed)
        elif row.declined_at is not None:
            funnel.declined += 1
        else:
            funnel.silent += 1
    return funnel


def nudge_funnel(rows: list[NudgeActionRow]) -> Funnel:
    """Did learners answer the ladder?

    Every action is an ask, so `candidates == asked` and `never_asked` is always zero — a row in this table
    exists only because the pass decided something. The field stays in the shape rather than being dropped so
    the two funnels stay comparable, and its being zero is itself the honest statement: unlike the review,
    there is no population here we failed to ask.

    **Nothing is counted as declined.** The nudge has three answers and none of them is "not now" — the
    dialog is dismissible instead, and a dismissal writes nothing. So `silent` here mixes "saw it and closed
    it" with "never saw it", which the review funnel can separate and this one cannot. Worth knowing before
    comparing the two response rates: this one's denominator is cleaner and its silence is muddier.
    """
    funnel = Funnel(candidates=len(rows), asked=len(rows))
    for row in rows:
        if row.learner_response is not None:
            funnel.answered += 1
            elapsed = _days_between(row.created_at, row.responded_at)
            if elapsed is not None:
                funnel.days_to_answer.append(elapsed)
        else:
            funnel.silent += 1
    return funnel


def split_by(rows: list[NudgeActionRow], key: str) -> dict[str, Funnel]:
    """The nudge funnel cut by `action` or `trigger`.

    **This is the cut phase 8 needs.** The ladder has three rungs and a single overall response rate cannot
    say which of them is worth keeping — an `asked_to_confirm` that gets answered half the time and a
    `warned` that gets answered never are a very different programme from two rungs at 25%.

    Sorted by count, descending, so the rungs that actually fire lead.
    """
    grouped: dict[str, list[NudgeActionRow]] = {}
    for row in rows:
        grouped.setdefault(getattr(row, key), []).append(row)
    return {
        name: nudge_funnel(group)
        for name, group in sorted(grouped.items(), key=lambda item: -len(item[1]))
    }


def response_breakdown(rows: list[NudgeActionRow]) -> dict[str, int]:
    """How many of each answer, for the answered rows.

    `already_done` is the one to watch: it says the *measurement* was wrong rather than the learner, and it is
    the only signal anywhere in the system that can say so. A high count is not learners misreporting, it is
    a metric that needs looking at.
    """
    counts: dict[str, int] = {}
    for row in rows:
        if row.learner_response is not None:
            counts[row.learner_response] = counts.get(row.learner_response, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

#: Preparations whose exam has passed, with whatever happened to the ask.
#:
#: A `LEFT JOIN` on the outcome for the **current sitting** only, matched on `examDate`. A postponed
#: preparation's earlier sittings are real history with their own outcomes, and joining them all would count
#: one preparation as several answers.
_REVIEW_SQL = """
    SELECT p.id                      AS prep_id,
           p."examDate"              AS exam_date,
           p."reviewAskedAt"         AS asked_at,
           p."reviewRemindersSent"   AS reminders_sent,
           p."reviewDeclinedAt"      AS declined_at,
           o."answeredAt"            AS answered_at,
           p.status                  AS status
    FROM "ExamPrep" p
    LEFT JOIN "PrepOutcome" o
           ON o."prepId" = p.id
          AND o."examDate" = p."examDate"
    WHERE p."examDate" < :now
    ORDER BY p."examDate" DESC
"""

_NUDGE_SQL = """
    SELECT a.action,
           a.trigger,
           a."createdAt"       AS created_at,
           a."learnerResponse" AS learner_response,
           a."respondedAt"     AS responded_at
    FROM "GoalLifecycleAction" a
    ORDER BY a."createdAt" DESC
"""


async def load_review_rows(*, now: datetime | None = None) -> list[ReviewAskRow]:
    """Every preparation whose exam has passed, across all learners.

    Unscoped by user on purpose: the question this answers is about the programme, not about a person, and a
    per-learner response rate over one preparation is not a rate. There is no route to this — it is read by
    `scripts/check_adaptive_response_rates.py`.
    """
    moment = (now or datetime.now(UTC)).replace(tzinfo=None)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text(_REVIEW_SQL), {"now": moment})
        return [
            ReviewAskRow(
                prep_id=row.prep_id,
                exam_date=row.exam_date,
                asked_at=row.asked_at,
                reminders_sent=row.reminders_sent or 0,
                declined_at=row.declined_at,
                answered_at=row.answered_at,
                status=row.status,
            )
            for row in result.all()
        ]


async def load_nudge_rows() -> list[NudgeActionRow]:
    """Every action the goal ladder has ever taken, across all learners."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text(_NUDGE_SQL))
        return [
            NudgeActionRow(
                action=row.action,
                trigger=row.trigger,
                created_at=row.created_at,
                learner_response=row.learner_response,
                responded_at=row.responded_at,
            )
            for row in result.all()
        ]


# ---------------------------------------------------------------------------
# The log line
# ---------------------------------------------------------------------------


def log_ask_event(event: str, **fields: Any) -> None:
    """One line per transition in the two asks, for the log stream.

    Redundant against the queries above, deliberately. The rows already answer "how many", so this is not
    where the counts come from — it is what makes the sequence visible in an aggregator without a database
    connection, and what survives a row being deleted with its preparation. Cheap enough that having both is
    not a decision worth agonising over.

    `extra` rather than an f-string, so a structured handler can index the fields instead of a human having
    to parse a sentence.
    """
    logger.info("adaptive_ask %s", event, extra={"adaptive_ask": {"event": event, **fields}})
