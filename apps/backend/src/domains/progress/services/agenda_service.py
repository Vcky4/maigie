"""The learner's agenda: everything scheduled, from every place that schedules it.

**Why this exists.** `ScheduleBlock` was being presented as "your schedule", and only two of the five
things that plan a learner's time ever write one — the goal planner and manual creation. Study plans write
`StudyPlanItem` rows, flashcard reviews live on `Flashcard.nextReviewAt`, live classes are `SpaceSession`
rows, and `create_schedule_block_for_review` — a function whose docstring says *"Create a ScheduleBlock
for a ReviewItem so it appears on the calendar"* — has never had a caller. So the schedule page showed a
learner with four plan items due today and 65 cards due this week the words "Nothing scheduled", while
Home showed four sessions. Each screen was right about its own table.

**Composed on read, never materialised.** Every source keeps its own store and this reads across them,
tagging each entry with `source`. The alternative — have every planner also write a `ScheduleBlock` — needs
every writer to remember, which is exactly how `ActivityFeedEntry` ended up with `entityType` columns no
writer populates; and it creates two records of one commitment, so a review whose due date moves leaves a
stale block behind. The same reasoning made goal evidence read the domain tables rather than the feed.

**Timed against day-scoped, and placement.** A `ScheduleBlock` and a `SpaceSession` were put at an hour by
someone, so their clock is real. A study-plan item is scheduled for a *day*: its `scheduledDate` carries
whatever time the plan was generated at — live, one learner's items cluster at `01:06`, `12:10`, `12:11`
and `23:26`, which are generation timestamps. Publishing those as start times invents appointments. So
day-scoped work is *placed* inside the day around what is already fixed, and every placed entry says so
through `placement` and the window it was put in. **A placement is a suggestion, not a commitment**: it is
computed on read and stored nowhere until the learner accepts it, at which point it becomes a real block
(`accept_placement`).

**`placement` is a token, not prose.** The service decides where and why; the client writes the words. The
same split the goals greeting, the driver impacts and the goal signals use.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from src.shared.time import LearnerTimezone, ensure_utc, resolve_learner_timezone, to_learner_local

logger = logging.getLogger(__name__)

#: Which store an entry came from. A closed set, because a client renders each differently and each is a
#: different query — the same reason `EvidenceKind` is closed.
AgendaSource = Literal["schedule", "study_plan", "review", "space_session"]

#: How the entry got its time.
#:
#: - `fixed` — the source put it at that hour and nothing here moved it.
#: - `preferred_window` — placed inside a part of the day this learner actually studies in.
#: - `default_window` — placed in daytime hours because there is no behavioural signal yet. Honest
#:   about being a default rather than implying the system learned something.
#: - `no_room` — the day is full, or its remaining hours are too short for the item. It stays on the day
#:   with no suggested clock rather than being crammed somewhere it does not fit.
PlacementBasis = Literal["fixed", "preferred_window", "default_window", "no_room"]

#: Parts of the day, exactly as `behaviour_service` buckets them. Used to *read* the learner's profile, so
#: "afternoon" here means what "afternoon" means there — a second definition would have the agenda place
#: work in a window the profile never described.
TIME_OF_DAY_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (5, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 24),
}

#: Where work may be *proposed*, which is not the same question as how observed sessions are classified.
#:
#: The bucket bounds above exist to sort sessions a learner already had: a 05:30 session is a morning one,
#: and recording it that way is correct. Proposing 05:30 to someone whose mornings are busiest is not — the
#: earliest edge of a classification bucket is not a sensible time to suggest. So placement uses its own
#: bounds, narrower at the day's edges, and the difference is deliberate rather than a drifted copy.
#:
#: `night` exists only because a learner whose top window genuinely is night should be offered something,
#: and it stops well short of the small hours.
PLACEMENT_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (8, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 23),
}

#: Where day-scoped work goes when nothing is known about the learner yet. Daytime, earliest first —
#: a neutral choice, and `placement` reports `default_window` so a client can say it is a default.
DEFAULT_WINDOW_ORDER: tuple[str, ...] = ("morning", "afternoon", "evening")

#: A gap between placed items, so a day of suggestions does not read as one unbroken block.
PLACEMENT_GAP_MINUTES = 10

#: How soon after "now" a placement may start, when placing into the day already in progress. Suggesting
#: a session two minutes from now is not a suggestion anybody can act on.
PLACEMENT_LEAD_MINUTES = 15

#: Cap on the review batch a single agenda entry proposes. A learner with 300 cards due does not have a
#: single five-hour session on their day; they have as much of it as fits.
MAX_REVIEW_BATCH = 40


@dataclass(frozen=True)
class AgendaEntry:
    """One thing on the learner's day.

    `startAt`/`endAt` are always populated so a client can lay the day out, but they only *mean* a clock
    reading when `timed` is true. For a placed entry they are the suggestion; for `no_room` they are the
    day the work belongs to and nothing more.
    """

    id: str
    source: AgendaSource
    title: str
    detail: str | None
    start_at: datetime
    end_at: datetime
    minutes: int
    #: `True` when the time came from the source. `False` for day-scoped work, placed or not.
    timed: bool
    placement: PlacementBasis
    #: Which part of the day it was placed in, for a placed entry. `None` for fixed and unplaced ones.
    window: str | None = None
    completed: bool = False
    #: How many underlying things this entry stands for — cards in a review batch. `None` when it is one.
    count: int | None = None
    #: Whatever the source knows this work is attached to, so a client can route from the row.
    links: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise both instants.

        These come from four tables that disagree about offsets — `ScheduleBlock.startAt` and
        `Flashcard.nextReviewAt` are `timestamp without time zone` while `SpaceSession.scheduledAt` is not
        — and the whole list gets sorted together. That mix is what made subject evidence a 500.
        """
        object.__setattr__(self, "start_at", ensure_utc(self.start_at))
        object.__setattr__(self, "end_at", ensure_utc(self.end_at))


@dataclass(frozen=True)
class _Pending:
    """Day-scoped work waiting to be placed."""

    day: date
    minutes: int
    build: Any  # (start, end, placement, window) -> AgendaEntry


def _window_bounds(day: date, window: str, timezone_: LearnerTimezone) -> tuple[datetime, datetime]:
    """The hours work may be proposed in on that day, in the learner's wall clock, as instants."""
    start_hour, end_hour = PLACEMENT_WINDOWS[window]
    start_local = datetime.combine(day, time(hour=start_hour)).replace(tzinfo=timezone_.zone)
    if end_hour >= 24:
        end_local = datetime.combine(day + timedelta(days=1), time(hour=0)).replace(
            tzinfo=timezone_.zone
        )
    else:
        end_local = datetime.combine(day, time(hour=end_hour)).replace(tzinfo=timezone_.zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _parse_clock(value: str | None) -> time | None:
    """`"22:00"` → a time. Anything unparseable is treated as unset rather than raising."""
    if not value:
        return None
    try:
        hour, _, minute = value.partition(":")
        return time(hour=int(hour), minute=int(minute or 0))
    except (TypeError, ValueError):
        return None


def preferred_window_order(preferred_times: dict | None) -> tuple[tuple[str, ...], bool]:
    """The parts of the day to try, best first, and whether that order came from the learner.

    Only a distribution computed in the learner's own timezone can order their day — `behaviour_service`
    publishes `basis: "utc_assumed"` when the zone was never captured, and those hours describe the
    server's day. Using them would place a Lagos learner's work by a London clock.

    **Night is only ever used when it is genuinely their top window.** Otherwise a learner with a handful
    of late sessions gets study suggested at 23:00, which is a worse default than admitting we do not know.
    """
    buckets = (preferred_times or {}).get("buckets") if isinstance(preferred_times, dict) else None
    basis = (preferred_times or {}).get("basis") if isinstance(preferred_times, dict) else None

    if not isinstance(buckets, dict) or basis != "local":
        return DEFAULT_WINDOW_ORDER, False

    scored = [
        (name, float(share or 0.0))
        for name, share in buckets.items()
        if name in TIME_OF_DAY_WINDOWS
    ]
    if not scored or all(share <= 0 for _, share in scored):
        return DEFAULT_WINDOW_ORDER, False

    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[0][0]
    order = tuple(name for name, share in scored if share > 0 and (name != "night" or name == top))
    return (order or DEFAULT_WINDOW_ORDER), True


def _busy_for_day(
    day: date, timed: list[AgendaEntry], timezone_: LearnerTimezone
) -> list[tuple[datetime, datetime]]:
    """The intervals already taken on that day, oldest first."""
    intervals = [
        (entry.start_at, entry.end_at)
        for entry in timed
        if to_learner_local(entry.start_at, timezone_).date() == day
    ]
    intervals.sort()
    return intervals


def _first_free_start(
    *,
    cursor: datetime,
    minutes: int,
    window_end: datetime,
    busy: list[tuple[datetime, datetime]],
) -> datetime | None:
    """The earliest start at or after `cursor` where `minutes` fits before `window_end`.

    Walks the busy list rather than scanning minute by minute: the day holds a handful of commitments, and
    stepping over each one is both exact and cheap.
    """
    candidate = cursor
    for busy_start, busy_end in busy:
        if candidate + timedelta(minutes=minutes) <= busy_start:
            break
        if candidate < busy_end:
            candidate = busy_end + timedelta(minutes=PLACEMENT_GAP_MINUTES)

    if candidate + timedelta(minutes=minutes) <= window_end:
        return candidate
    return None


def place_pending(
    *,
    pending: list[_Pending],
    timed: list[AgendaEntry],
    timezone_: LearnerTimezone,
    preferred_times: dict | None,
    quiet_start: str | None = None,
    quiet_end: str | None = None,
    now: datetime | None = None,
) -> list[AgendaEntry]:
    """Place day-scoped work inside its day, around what is already fixed there.

    Deterministic and re-derivable: the same inputs always produce the same day, so nothing has to be
    stored for the suggestion to be stable, and a learner who ignores it loses nothing.

    Quiet hours are honoured when the learner set any — live, nobody has, so this is a branch that exists
    because the column does, not a figure being invented for them.
    """
    now = now or datetime.now(UTC)
    order, from_learner = preferred_window_order(preferred_times)
    quiet_from = _parse_clock(quiet_start)
    quiet_to = _parse_clock(quiet_end)

    placed: list[AgendaEntry] = []
    # Placed entries become obstacles for the next item, so two suggestions never overlap each other.
    obstacles = list(timed)

    by_day: dict[date, list[_Pending]] = {}
    for item in pending:
        by_day.setdefault(item.day, []).append(item)

    for day in sorted(by_day):
        busy = _busy_for_day(day, obstacles, timezone_)
        cursors: dict[str, datetime] = {}

        for item in by_day[day]:
            chosen: tuple[datetime, str] | None = None

            for window in order:
                window_start, window_end = _window_bounds(day, window, timezone_)
                if _within_quiet_hours(window_start, timezone_, quiet_from, quiet_to):
                    continue

                cursor = cursors.get(window, window_start)
                # Never suggest a start that has already passed, or is about to.
                cursor = max(cursor, now + timedelta(minutes=PLACEMENT_LEAD_MINUTES))

                start = _first_free_start(
                    cursor=cursor,
                    minutes=item.minutes,
                    window_end=window_end,
                    busy=_busy_for_day(day, obstacles, timezone_),
                )
                if start is not None:
                    chosen = (start, window)
                    cursors[window] = start + timedelta(
                        minutes=item.minutes + PLACEMENT_GAP_MINUTES
                    )
                    break

            if chosen is None:
                # The day has no room. It keeps the work rather than pushing it to another day, because
                # moving a learner's plan is their decision, not a side effect of reading the agenda.
                entry = item.build(None, None, "no_room", None)
            else:
                start, window = chosen
                entry = item.build(
                    start,
                    start + timedelta(minutes=item.minutes),
                    "preferred_window" if from_learner else "default_window",
                    window,
                )
                obstacles.append(entry)
                busy = _busy_for_day(day, obstacles, timezone_)

            placed.append(entry)

    return placed


def _within_quiet_hours(
    instant: datetime,
    timezone_: LearnerTimezone,
    quiet_from: time | None,
    quiet_to: time | None,
) -> bool:
    """Whether an instant falls in the learner's quiet hours, handling a window that crosses midnight."""
    if quiet_from is None or quiet_to is None:
        return False
    local = to_learner_local(instant, timezone_).time()
    if quiet_from <= quiet_to:
        return quiet_from <= local < quiet_to
    return local >= quiet_from or local < quiet_to


async def get_agenda(
    *, user_id: str, since: datetime, until: datetime
) -> list[AgendaEntry]:
    """Everything on the learner's days between two instants, timed work first in clock order.

    **Read sequentially, not gathered.** Four sources across three domains, each opening its own
    connection, is exactly the fan-out that exhausted the session-mode pooler's tenant allowance and made
    `daily-counts` return intermittent 500s. Each source is also wrapped on its own: losing space sessions
    must not cost the learner their study plan.
    """
    timezone_ = await resolve_learner_timezone(user_id)

    timed: list[AgendaEntry] = []
    timed.extend(await _read_blocks(user_id=user_id, since=since, until=until))
    timed.extend(await _read_space_sessions(user_id=user_id, since=since, until=until))

    pending: list[_Pending] = []
    pending.extend(
        await _read_plan_items(user_id=user_id, since=since, until=until, timezone_=timezone_)
    )
    pending.extend(
        await _read_due_reviews(user_id=user_id, since=since, until=until, timezone_=timezone_)
    )
    pending.extend(
        await _read_topic_reviews(user_id=user_id, since=since, until=until, timezone_=timezone_)
    )

    preferred_times, quiet_start, quiet_end = await _placement_inputs(user_id)

    placed = place_pending(
        pending=pending,
        timed=timed,
        timezone_=timezone_,
        preferred_times=preferred_times,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )

    entries = [*timed, *placed]
    # Fixed work first within the same instant, then placed suggestions — a learner reads their
    # commitments before proposals about the same hour.
    entries.sort(key=lambda entry: (entry.start_at, not entry.timed, entry.title))
    return entries


async def _placement_inputs(user_id: str) -> tuple[dict | None, str | None, str | None]:
    """The learner's own study pattern and quiet hours, or nothing if neither was ever captured."""
    from src.domains.personal_learning.repository import personal_learning_repo

    try:
        profile = await personal_learning_repo.get_profile_by_user(user_id)
    except Exception:  # pragma: no cover - placement falls back to defaults
        return None, None, None

    if profile is None:
        return None, None, None
    return (
        getattr(profile, "preferred_study_times", None),
        getattr(profile, "quiet_hours_start", None),
        getattr(profile, "quiet_hours_end", None),
    )


async def _read_blocks(*, user_id: str, since: datetime, until: datetime) -> list[AgendaEntry]:
    """`ScheduleBlock` rows — work someone placed at an hour, including accepted placements."""
    from ..repository import progress_repo

    try:
        rows, _ = await progress_repo.list_blocks(
            user_id,
            where={"startAt": {"lte": until}, "endAt": {"gte": since}},
            skip=0,
            take=200,
        )
    except Exception:
        return []

    entries = []
    for row in rows:
        start = ensure_utc(row.start_at)
        end = ensure_utc(row.end_at)
        entries.append(
            AgendaEntry(
                id=f"block:{row.id}",
                source="schedule",
                title=row.title,
                detail=row.description,
                start_at=start,
                end_at=end,
                minutes=max(int((end - start).total_seconds() // 60), 0),
                timed=True,
                placement="fixed",
                completed=row.completed_at is not None,
                links={
                    "blockId": row.id,
                    "courseId": row.course_id,
                    "topicId": row.topic_id,
                    "goalId": row.goal_id,
                    "reviewItemId": row.review_item_id,
                },
            )
        )
    return entries


async def _read_space_sessions(
    *, user_id: str, since: datetime, until: datetime
) -> list[AgendaEntry]:
    """Live sessions in the learner's spaces. Fixed by definition — other people are attending."""
    from src.domains.learning_spaces.repository import space_repo

    try:
        rows = await space_repo.list_sessions_for_member(user_id, since=since, until=until)
    except Exception:
        # A failure in one source costs that source, not the learner's whole day. The import is at the top
        # of the function rather than inside the `try` on purpose: a missing reader is a programming error
        # and should raise on import, not be swallowed as an empty list that looks like "no classes".
        logger.warning("agenda: space sessions unavailable", exc_info=True)
        return []

    entries = []
    for row in rows:
        start = ensure_utc(row.scheduled_at)
        minutes = int(getattr(row, "duration", 60) or 60)
        entries.append(
            AgendaEntry(
                id=f"space_session:{row.id}",
                source="space_session",
                title=row.title,
                detail=getattr(row, "description", None),
                start_at=start,
                end_at=start + timedelta(minutes=minutes),
                minutes=minutes,
                timed=True,
                placement="fixed",
                links={"spaceId": row.space_id, "sessionId": row.id},
            )
        )
    return entries


async def _read_plan_items(
    *, user_id: str, since: datetime, until: datetime, timezone_: LearnerTimezone
) -> list[_Pending]:
    """Pending study-plan items, day-scoped.

    `list_items_due_by` already excludes paused and superseded plans, which is the right rule: pausing is a
    statement that the learner is not working on this now.
    """
    from src.domains.personal_learning.repository import personal_learning_repo

    try:
        rows = await personal_learning_repo.list_items_due_by(user_id, until=until)
    except Exception:
        return []

    # Compared as learner-local days, because that is the unit a plan item is scheduled in.
    first_day = to_learner_local(since, timezone_).date()

    # Items the learner has already accepted a time for hold a block, and that block is read by
    # `_read_blocks` — offering them a placement again would show one commitment twice. Verified against
    # the blocks that actually exist rather than trusting the id, so deleting the block puts the item back
    # on the agenda instead of losing it. Same rule as `_read_topic_reviews`.
    scheduled_item_ids = await _plan_items_holding_a_block(
        [item.id for item, _plan in rows],
    )

    pending: list[_Pending] = []
    for item, plan in rows:
        if item.id in scheduled_item_ids:
            continue
        scheduled = ensure_utc(item.scheduled_date)
        day = to_learner_local(scheduled, timezone_).date()
        if day < first_day:
            # `list_items_due_by` has no lower bound, so it also returns work whose day has passed.
            # **Overdue plan items are left out rather than rolled forward.** Moving a learner's plan onto
            # a different day is a planning decision and theirs to make; doing it silently on read would
            # have the agenda quietly rewrite the plan every time it was viewed. They remain visible where
            # they belong, on the study plan itself.
            continue
        minutes = int(getattr(item, "estimated_minutes", 30) or 30)

        pending.append(
            _Pending(
                day=day,
                minutes=minutes,
                build=_plan_item_builder(item=item, plan=plan, minutes=minutes, fallback=scheduled),
            )
        )
    return pending


async def _plan_items_holding_a_block(item_ids: list[str]) -> set[str]:
    """Which of these plan items point at a `ScheduleBlock` that still exists.

    One query, joined rather than trusting `scheduleBlockId`. The column is `ON DELETE SET NULL`, so a
    dangling id should not be possible — but a deleted block must put the item back on the agenda, and a
    join says that plainly instead of relying on a constraint to have fired.
    """
    if not item_ids:
        return set()

    from sqlalchemy import select

    from src.domains.personal_learning.db_models import StudyPlanItem
    from src.domains.progress.db_models import ScheduleBlock
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(StudyPlanItem.id)
                .join(ScheduleBlock, ScheduleBlock.id == StudyPlanItem.schedule_block_id)
                .where(StudyPlanItem.id.in_(item_ids))
            )
        ).all()
    return {row[0] for row in rows}


def _plan_item_builder(*, item: Any, plan: Any, minutes: int, fallback: datetime):
    def build(
        start: datetime | None, end: datetime | None, placement: str, window: str | None
    ) -> AgendaEntry:
        return AgendaEntry(
            id=f"plan_item:{item.id}",
            source="study_plan",
            title=item.title,
            detail=getattr(plan, "title", None),
            # When there is no room, the item keeps the day it belongs to and no meaningful clock.
            start_at=start or fallback,
            end_at=end or (fallback + timedelta(minutes=minutes)),
            minutes=minutes,
            timed=False,
            placement=placement,  # type: ignore[arg-type]
            window=window,
            links={
                "planId": plan.id,
                "planItemId": item.id,
                "topicId": getattr(item, "topic_id", None),
                "courseId": getattr(item, "course_id", None) or getattr(plan, "course_id", None),
                "prepTopicId": getattr(item, "prep_topic_id", None),
            },
        )

    return build


async def _read_due_reviews(
    *, user_id: str, since: datetime, until: datetime, timezone_: LearnerTimezone
) -> list[_Pending]:
    """Flashcards due, batched into one entry per deck per day.

    **One entry per card would bury the day.** Sixty-five cards due this week is one learner's real
    figure; sixty-five agenda rows is not a day anybody can read. The unit a learner acts on is a review
    session, so that is the unit — with `count` carrying how many cards it stands for.

    Minutes come from `flashcard_service.REVIEW_SECONDS_PER_CARD`, the same constant and the same rounding
    the Learn dashboard uses, so the two surfaces cannot estimate the same queue differently.
    """
    from src.domains.personal_learning.repository import personal_learning_repo
    from src.domains.personal_learning.services import flashcard_service

    try:
        batches = await personal_learning_repo.count_due_flashcards_by_deck_day(
            user_id,
            since=since,
            until=until,
            # Bucketed in the learner's own zone, so a card due late on Monday their time is Monday's
            # review and not Tuesday's.
            timezone_name=timezone_.name if timezone_.is_known else "UTC",
        )
    except Exception:
        logger.warning("agenda: due reviews unavailable", exc_info=True)
        return []

    pending: list[_Pending] = []
    for batch in batches:
        due_at = ensure_utc(batch["dueAt"])
        day = to_learner_local(due_at, timezone_).date()
        count = min(int(batch["count"]), MAX_REVIEW_BATCH)
        minutes = max(
            1, (count * flashcard_service.REVIEW_SECONDS_PER_CARD + 59) // 60
        )
        pending.append(
            _Pending(
                day=day,
                minutes=minutes,
                build=_review_builder(
                    deck_id=batch["deckId"],
                    deck_title=batch["deckTitle"],
                    day=day,
                    count=count,
                    total=int(batch["count"]),
                    minutes=minutes,
                    fallback=due_at,
                ),
            )
        )
    return pending


def _review_builder(
    *,
    deck_id: str | None,
    deck_title: str | None,
    day: date,
    count: int,
    total: int,
    minutes: int,
    fallback: datetime,
):
    def build(
        start: datetime | None, end: datetime | None, placement: str, window: str | None
    ) -> AgendaEntry:
        return AgendaEntry(
            # Keyed by deck and day, so the same batch keeps its identity across reads.
            id=f"review:{deck_id or 'unfiled'}:{day.isoformat()}",
            source="review",
            title=f"Review: {deck_title}" if deck_title else "Flashcard review",
            # States the batch, and says so when the queue is longer than one sitting.
            detail=(
                f"{count} cards"
                if total <= count
                else f"{count} of {total} cards due"
            ),
            start_at=start or fallback,
            end_at=end or (fallback + timedelta(minutes=minutes)),
            minutes=minutes,
            timed=False,
            placement=placement,  # type: ignore[arg-type]
            window=window,
            count=total,
            links={"deckId": deck_id},
        )

    return build


# ---------------------------------------------------------------------------
# Accepting a placement
# ---------------------------------------------------------------------------

#: What an agenda id's prefix says the entry came from. Ids are namespaced because four id spaces land in
#: one list, and an unprefixed id could collide — the same rule the evidence items follow.
_ACCEPTABLE_PREFIXES = ("plan_item", "review")


async def accept_placement(
    *,
    user_id: str,
    entry_id: str,
    start_at: datetime,
    minutes: int,
    title: str | None = None,
) -> Any:
    """Write a real `ScheduleBlock` for a suggested placement.

    **The only write in this module.** Everywhere else the agenda composes on read; here the learner has
    said "yes, put it there", so there is something to record.

    The block is linked back to what produced the suggestion — `topicId` for a plan item, `reviewItemId`
    left alone for a review batch, which stands for a deck rather than one review row — so the block is
    traceable to its origin rather than appearing as an unexplained entry the learner cannot place.

    A `schedule` or `space_session` id is refused rather than quietly duplicated: those are already fixed
    commitments, and accepting one would create a second block for the same hour.
    """
    from src.shared.exceptions import NotFoundError, ValidationError

    from . import schedule_service

    kind, _, remainder = entry_id.partition(":")
    if kind not in _ACCEPTABLE_PREFIXES:
        raise ValidationError(
            f"{entry_id!r} is not a suggested placement. "
            "Only study-plan items and review batches can be accepted; "
            "schedule blocks and live sessions are already scheduled."
        )

    start = ensure_utc(start_at)
    data: dict[str, Any] = {
        "startAt": start,
        "endAt": start + timedelta(minutes=minutes),
    }

    if kind == "plan_item":
        item = await _find_plan_item(user_id=user_id, item_id=remainder)
        if item is None:
            raise NotFoundError("StudyPlanItem", remainder)
        data["title"] = title or item.title
        data["description"] = "Scheduled from your study plan"
        data["topicId"] = getattr(item, "topic_id", None)
    else:
        # `review:{deckId}:{day}` — the batch is a deck and a day, not a single review row.
        deck_id = remainder.partition(":")[0]
        data["title"] = title or "Flashcard review"
        data["description"] = "Scheduled from your review queue"
        if deck_id and deck_id != "unfiled":
            data["courseId"] = None

    # Through `schedule_service`, not the repository, so an accepted placement syncs to Google Calendar
    # exactly like a block the learner typed in themselves.
    block = await schedule_service.create_block(user_id=user_id, data=data)

    if kind == "plan_item":
        # Record that this item is now scheduled, or the next read of the agenda returns both the block
        # and the same item still being offered a time — one commitment shown twice, the second copy
        # inviting the learner to schedule what they just scheduled. Migration 048 added the column.
        await _link_plan_item_to_block(item_id=remainder, block_id=block.id)

    return block


async def _link_plan_item_to_block(*, item_id: str, block_id: str) -> None:
    """Point a plan item at the block that now carries it.

    Its own session and its own failure mode: the block exists and is the learner's answer, so a failure
    to record the link must not fail the acceptance. The cost of losing it is a duplicate suggestion,
    which the learner can ignore — the cost of raising here is a block they cannot see.
    """
    from sqlalchemy import update

    from src.domains.personal_learning.db_models import StudyPlanItem
    from src.shared.database import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(StudyPlanItem)
                .where(StudyPlanItem.id == item_id)
                .values(schedule_block_id=block_id)
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Accepted placement could not be linked to its plan item",
            extra={"item_id": item_id, "block_id": block_id},
        )


async def _find_plan_item(*, user_id: str, item_id: str) -> Any | None:
    """The plan item, if it belongs to this learner. Scoped by user, not just by id."""
    from sqlalchemy import select

    from src.domains.personal_learning.db_models import StudyPlan, StudyPlanItem
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(StudyPlanItem)
                .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
                .where(StudyPlanItem.id == item_id, StudyPlan.user_id == user_id)
                .limit(1)
            )
        ).scalar_one_or_none()


async def _read_topic_reviews(
    *, user_id: str, since: datetime, until: datetime, timezone_: LearnerTimezone
) -> list[_Pending]:
    """Topic-level spaced repetition (`ReviewItem`), day-scoped.

    **A second review system, and not the same one as flashcards.** `Flashcard.nextReviewAt` schedules
    cards; `ReviewItem` schedules a return to a whole topic, with its own interval and ease factor. Both
    belong on the learner's day, and both are day-scoped: the algorithm decides *which day* a topic is due
    back, never which hour.

    **Items that already hold a schedule block are skipped.** `process_due_reviews` materialises reviews
    into blocks, so an item with a live block is already in the agenda through `_read_blocks`; reading it
    here as well would show the learner the same review twice. That sweep is not on a beat schedule and
    does not need to be — see its own docstring.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.shared.database import get_session_factory

    from ..db_models import ReviewItem

    try:
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ReviewItem)
                        .options(
                            selectinload(ReviewItem.topic),
                            selectinload(ReviewItem.schedule_block),
                        )
                        .where(
                            ReviewItem.user_id == user_id,
                            ReviewItem.next_review_at >= since,
                            ReviewItem.next_review_at <= until,
                        )
                        .order_by(ReviewItem.next_review_at.asc())
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
    except Exception:
        logger.warning("agenda: topic reviews unavailable", exc_info=True)
        return []

    from .spaced_repetition_impl import REVIEW_BLOCK_DURATION_MINUTES

    pending: list[_Pending] = []
    for review in rows:
        if getattr(review, "schedule_block", None) is not None:
            # Already materialised into a block, which `_read_blocks` returns. One commitment, one entry.
            continue

        due = ensure_utc(review.next_review_at)
        topic = getattr(review, "topic", None)
        title = getattr(topic, "title", None) or "Topic review"
        pending.append(
            _Pending(
                day=to_learner_local(due, timezone_).date(),
                minutes=REVIEW_BLOCK_DURATION_MINUTES,
                build=_topic_review_builder(
                    review_id=review.id,
                    title=title,
                    topic_id=review.topic_id,
                    minutes=REVIEW_BLOCK_DURATION_MINUTES,
                    fallback=due,
                ),
            )
        )
    return pending


def _topic_review_builder(
    *, review_id: str, title: str, topic_id: str | None, minutes: int, fallback: datetime
):
    def build(
        start: datetime | None, end: datetime | None, placement: str, window: str | None
    ) -> AgendaEntry:
        return AgendaEntry(
            id=f"topic_review:{review_id}",
            source="review",
            title=f"Review: {title}",
            detail="Spaced repetition",
            start_at=start or fallback,
            end_at=end or (fallback + timedelta(minutes=minutes)),
            minutes=minutes,
            timed=False,
            placement=placement,  # type: ignore[arg-type]
            window=window,
            links={"reviewItemId": review_id, "topicId": topic_id},
        )

    return build
