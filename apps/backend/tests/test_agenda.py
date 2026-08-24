"""The agenda: four sources in one list, and placing day-scoped work inside a day.

`ScheduleBlock` was being presented as the learner's schedule while only two of the five things that plan
their time ever write one. A learner with four study-plan items due today and 65 cards due this week was
told "Nothing scheduled" on one screen and shown four sessions on another, because the two screens read
different tables.

The rules pinned here:

1. **A clock only means something when the source set it.** A plan item is scheduled for a *day*; its
   `scheduledDate` carries whatever time the plan was generated at.
2. **Placement uses the learner's own windows, or admits it is guessing.** A distribution computed in UTC
   because their timezone was never captured cannot order their day.
3. **Placed work goes around what is already fixed**, and never on top of another suggestion.
4. **A full day says so** rather than cramming work into a gap it does not fit.
5. **Nothing is written until the learner accepts it.**
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domains.progress.services import agenda_service as agenda
from src.shared.time import UNKNOWN_TIMEZONE, LearnerTimezone

LAGOS = LearnerTimezone(
    zone=ZoneInfo("Africa/Lagos"), name="Africa/Lagos", is_known=True, source="profile"
)

#: Well clear of "now", so the lead-time guard never interferes with a placement assertion.
DAY = date.today() + timedelta(days=30)


def _local(hour: int, minute: int = 0, *, day: date = DAY, timezone_=LAGOS) -> datetime:
    """An instant from the learner's wall clock."""
    return datetime.combine(day, time(hour=hour, minute=minute)).replace(
        tzinfo=timezone_.zone
    ).astimezone(UTC)


def _fixed(start: datetime, minutes: int, title: str = "Class") -> agenda.AgendaEntry:
    return agenda.AgendaEntry(
        id=f"block:{title}",
        source="schedule",
        title=title,
        detail=None,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        minutes=minutes,
        timed=True,
        placement="fixed",
    )


def _pending(minutes: int, *, day: date = DAY, label: str = "Item") -> agenda._Pending:
    def build(start, end, placement, window):
        return agenda.AgendaEntry(
            id=f"plan_item:{label}",
            source="study_plan",
            title=label,
            detail=None,
            start_at=start or _local(0, day=day),
            end_at=end or _local(0, day=day) + timedelta(minutes=minutes),
            minutes=minutes,
            timed=False,
            placement=placement,
            window=window,
        )

    return agenda._Pending(day=day, minutes=minutes, build=build)


def _buckets(**shares: float) -> dict:
    full = {"morning": 0.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0}
    full.update(shares)
    return {"buckets": full, "basis": "local", "timezone": "Africa/Lagos", "sessionCount": 12}


class TestPreferredWindowOrder:
    def test_the_learners_own_windows_are_used_best_first(self):
        order, from_learner = agenda.preferred_window_order(
            _buckets(evening=55.0, morning=30.0, afternoon=15.0)
        )

        assert from_learner is True
        assert order[0] == "evening"
        assert list(order) == ["evening", "morning", "afternoon"]

    def test_a_utc_assumed_distribution_is_not_used_to_order_their_day(self):
        """`behaviour_service` flags this when the timezone was never captured. Those hours describe the
        server's day, so placing a Lagos learner's work by them would be placing it by a London clock."""
        utc_assumed = {
            "buckets": {"morning": 0.0, "afternoon": 0.0, "evening": 90.0, "night": 10.0},
            "basis": "utc_assumed",
            "timezone": None,
            "sessionCount": 20,
        }

        order, from_learner = agenda.preferred_window_order(utc_assumed)

        assert from_learner is False
        assert order == agenda.DEFAULT_WINDOW_ORDER

    def test_nothing_recorded_falls_back_to_daytime_and_says_so(self):
        for value in (None, {}, {"buckets": None, "basis": "local"}):
            order, from_learner = agenda.preferred_window_order(value)
            assert from_learner is False
            assert order == agenda.DEFAULT_WINDOW_ORDER

    def test_an_all_zero_distribution_is_no_signal(self):
        order, from_learner = agenda.preferred_window_order(_buckets())
        assert from_learner is False
        assert order == agenda.DEFAULT_WINDOW_ORDER

    def test_night_is_skipped_unless_it_is_genuinely_their_top_window(self):
        """A learner with a handful of late sessions should not get study suggested at 23:00."""
        order, _ = agenda.preferred_window_order(
            _buckets(morning=60.0, night=40.0)
        )
        assert "night" not in order

        top_night, _ = agenda.preferred_window_order(_buckets(night=70.0, morning=30.0))
        assert top_night[0] == "night", "when it is where they actually study, it is used"

    def test_the_default_order_is_daytime_only(self):
        assert "night" not in agenda.DEFAULT_WINDOW_ORDER


class TestPlacement:
    def test_work_lands_in_the_learners_top_window(self):
        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(evening=80.0, morning=20.0),
        )

        assert placed[0].placement == "preferred_window"
        assert placed[0].window == "evening"
        # 17:00–21:00 in the learner's own clock.
        assert 17 <= placed[0].start_at.astimezone(LAGOS.zone).hour < 21

    def test_placement_without_a_signal_is_reported_as_a_default(self):
        placed = agenda.place_pending(
            pending=[_pending(30)], timed=[], timezone_=LAGOS, preferred_times=None
        )

        assert placed[0].placement == "default_window"
        assert placed[0].window == "morning", "the neutral order starts with the earliest daytime window"

    def test_it_places_around_something_already_fixed(self):
        """The learner has a class from 09:00 to 10:30 their time; a 30-minute item must not land in it."""
        busy = _fixed(_local(9), 90)

        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[busy],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=100.0),
        )

        entry = placed[0]
        # Either side of the class is fine — earlier is better when the window has room before it. What
        # must never happen is an overlap.
        assert entry.end_at <= busy.start_at or entry.start_at >= busy.end_at

    def test_it_takes_the_gap_after_a_class_when_the_window_opens_inside_it(self):
        """A class covering the start of the window pushes the suggestion past it, plus the gap."""
        busy = _fixed(_local(8), 90, title="Class")

        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[busy],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=100.0),
        )

        assert placed[0].start_at >= busy.end_at + timedelta(minutes=agenda.PLACEMENT_GAP_MINUTES)

    def test_work_is_not_proposed_at_the_edge_of_a_classification_bucket(self):
        """`behaviour_service` calls 05:30 a morning session, and recording it that way is right. Proposing
        05:30 is not — the earliest edge of a bucket is not a time to suggest to anybody."""
        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=100.0),
        )

        local_hour = placed[0].start_at.astimezone(LAGOS.zone).hour
        assert local_hour >= agenda.PLACEMENT_WINDOWS["morning"][0]
        assert agenda.PLACEMENT_WINDOWS["morning"][0] > agenda.TIME_OF_DAY_WINDOWS["morning"][0]

    def test_two_suggestions_do_not_overlap_each_other(self):
        """A placed entry becomes an obstacle for the next one, or a day of suggestions would all sit at
        the same hour."""
        placed = agenda.place_pending(
            pending=[_pending(30, label="A"), _pending(45, label="B")],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=100.0),
        )

        first, second = sorted(placed, key=lambda entry: entry.start_at)
        assert second.start_at >= first.end_at

    def test_a_full_day_reports_no_room_rather_than_cramming(self):
        """Every daytime window is taken. The work stays on its day with no suggested clock."""
        wall = [
            _fixed(_local(5), 60 * 7, title="Morning"),
            _fixed(_local(12), 60 * 5, title="Afternoon"),
            _fixed(_local(17), 60 * 4, title="Evening"),
        ]

        placed = agenda.place_pending(
            pending=[_pending(45)],
            timed=wall,
            timezone_=LAGOS,
            preferred_times=None,
        )

        assert placed[0].placement == "no_room"
        assert placed[0].window is None
        assert placed[0].timed is False

    def test_an_item_too_long_for_the_remaining_gap_is_not_squeezed_in(self):
        # 20 minutes free in the morning window, and a 60-minute item.
        wall = [
            _fixed(_local(5), 60 * 6 + 40, title="Most of the morning"),
            _fixed(_local(12), 60 * 5, title="Afternoon"),
            _fixed(_local(17), 60 * 4, title="Evening"),
        ]

        placed = agenda.place_pending(
            pending=[_pending(60)], timed=wall, timezone_=LAGOS, preferred_times=None
        )

        assert placed[0].placement == "no_room"

    def test_it_spills_to_the_next_window_when_the_first_is_full(self):
        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[_fixed(_local(5), 60 * 7, title="Morning gone")],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=70.0, afternoon=30.0),
        )

        assert placed[0].window == "afternoon"
        assert placed[0].placement == "preferred_window"

    def test_it_never_suggests_a_time_that_has_already_passed(self):
        """Placing into the day already in progress must not propose this morning at 09:00 at 4pm."""
        today = date.today()
        now = datetime.now(UTC)

        placed = agenda.place_pending(
            pending=[_pending(30, day=today)],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=50.0, afternoon=30.0, evening=20.0),
            now=now,
        )

        entry = placed[0]
        if entry.placement != "no_room":
            assert entry.start_at >= now, "a suggestion in the past is not actionable"

    def test_quiet_hours_are_honoured_when_the_learner_set_any(self):
        """Nobody in this database has set any, so this is a branch that exists because the column does —
        not a figure being invented on their behalf."""
        placed = agenda.place_pending(
            pending=[_pending(30)],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(evening=100.0),
            quiet_start="17:00",
            quiet_end="23:00",
        )

        # The evening window is entirely quiet, so it must not be used.
        assert placed[0].window != "evening"

    def test_each_day_is_placed_independently(self):
        placed = agenda.place_pending(
            pending=[
                _pending(30, day=DAY, label="today"),
                _pending(30, day=DAY + timedelta(days=1), label="tomorrow"),
            ],
            timed=[],
            timezone_=LAGOS,
            preferred_times=_buckets(morning=100.0),
        )

        days = {entry.start_at.astimezone(LAGOS.zone).date() for entry in placed}
        assert days == {DAY, DAY + timedelta(days=1)}

    def test_nothing_pending_places_nothing(self):
        assert agenda.place_pending(
            pending=[], timed=[], timezone_=LAGOS, preferred_times=None
        ) == []

    def test_an_unknown_timezone_still_places_work(self):
        """Placement degrades to UTC hours rather than refusing to schedule anything."""
        placed = agenda.place_pending(
            pending=[_pending(30)], timed=[], timezone_=UNKNOWN_TIMEZONE, preferred_times=None
        )

        assert placed[0].placement == "default_window"


class TestQuietHourParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("22:00", time(22, 0)), ("7:30", time(7, 30)), ("22", time(22, 0))],
    )
    def test_it_reads_a_stored_clock_string(self, value, expected):
        assert agenda._parse_clock(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a time", "99:99"])
    def test_unusable_values_are_treated_as_unset_rather_than_raising(self, value):
        assert agenda._parse_clock(value) is None

    def test_a_window_crossing_midnight_is_handled(self):
        # 22:00–06:00: an instant at 23:00 and one at 02:00 are both quiet; 12:00 is not.
        assert agenda._within_quiet_hours(_local(23), LAGOS, time(22, 0), time(6, 0)) is True
        assert agenda._within_quiet_hours(_local(2), LAGOS, time(22, 0), time(6, 0)) is True
        assert agenda._within_quiet_hours(_local(12), LAGOS, time(22, 0), time(6, 0)) is False


class TestAgendaEntryNormalisesInstants:
    def test_a_naive_source_becomes_aware(self):
        """These come from four tables that disagree about offsets, and the whole list is sorted together —
        the mix that made subject evidence return 500."""
        entry = agenda.AgendaEntry(
            id="block:1",
            source="schedule",
            title="Study",
            detail=None,
            start_at=datetime(2026, 8, 24, 9, 0),
            end_at=datetime(2026, 8, 24, 10, 0),
            minutes=60,
            timed=True,
            placement="fixed",
        )

        assert entry.start_at.tzinfo is not None
        assert entry.end_at.tzinfo is not None

    def test_the_two_kinds_can_be_sorted_together(self):
        naive = agenda.AgendaEntry(
            id="a", source="schedule", title="A", detail=None,
            start_at=datetime(2026, 8, 24, 9, 0), end_at=datetime(2026, 8, 24, 10, 0),
            minutes=60, timed=True, placement="fixed",
        )
        aware = agenda.AgendaEntry(
            id="b", source="space_session", title="B", detail=None,
            start_at=datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
            end_at=datetime(2026, 8, 24, 8, 30, tzinfo=UTC),
            minutes=30, timed=True, placement="fixed",
        )

        assert [entry.id for entry in sorted([naive, aware], key=lambda e: e.start_at)] == ["b", "a"]


class TestAcceptPlacement:
    async def test_a_fixed_entry_cannot_be_accepted(self):
        """Accepting a block or a class would write a second record of one commitment."""
        from src.shared.exceptions import ValidationError

        for entry_id in ("block:abc", "space_session:abc"):
            with pytest.raises(ValidationError):
                await agenda.accept_placement(
                    user_id="u1",
                    entry_id=entry_id,
                    start_at=datetime.now(UTC),
                    minutes=30,
                )

    async def test_an_unknown_prefix_is_refused(self):
        from src.shared.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await agenda.accept_placement(
                user_id="u1", entry_id="nonsense", start_at=datetime.now(UTC), minutes=30
            )

    async def test_a_plan_item_belonging_to_someone_else_is_not_found(self):
        from unittest.mock import patch

        from src.shared.exceptions import NotFoundError

        async def _none(**_kwargs):
            return None

        with patch.object(agenda, "_find_plan_item", _none):
            with pytest.raises(NotFoundError):
                await agenda.accept_placement(
                    user_id="u1",
                    entry_id="plan_item:item-1",
                    start_at=datetime.now(UTC),
                    minutes=30,
                )

    async def test_accepting_goes_through_the_schedule_service_so_it_syncs(self):
        """Not straight to the repository: an accepted placement must reach Google Calendar exactly like a
        block the learner typed in themselves."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from src.domains.progress.services import schedule_service

        captured: dict = {}

        async def _create_block(*, user_id, data):
            captured["user_id"] = user_id
            captured["data"] = data
            return SimpleNamespace(id="new-block")

        async def _item(**_kwargs):
            return SimpleNamespace(id="item-1", title="Read chapter 4", topic_id="topic-9")

        start = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        with (
            patch.object(schedule_service, "create_block", _create_block),
            patch.object(agenda, "_find_plan_item", _item),
        ):
            await agenda.accept_placement(
                user_id="u1", entry_id="plan_item:item-1", start_at=start, minutes=45
            )

        assert captured["user_id"] == "u1"
        assert captured["data"]["title"] == "Read chapter 4"
        assert captured["data"]["startAt"] == start
        assert captured["data"]["endAt"] == start + timedelta(minutes=45)
        assert captured["data"]["topicId"] == "topic-9", "traceable to what produced the suggestion"


class TestTopicReviewsAreNotShownTwice:
    """There are two review systems, and one of them can already be materialised into a block.

    `Flashcard.nextReviewAt` schedules cards; `ReviewItem` schedules a return to a whole topic.
    `process_due_reviews` writes a `ScheduleBlock` for a `ReviewItem`, so an item holding a live block is
    already in the agenda through the block reader — reading it again as a due review would show the
    learner the same review twice.
    """

    async def _topic_reviews(self, rows):
        from types import SimpleNamespace
        from unittest.mock import patch

        class _Session:
            async def execute(self, *_a, **_k):
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: rows)
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        with patch("src.shared.database.get_session_factory", lambda: _Session):
            return await agenda._read_topic_reviews(
                user_id="u1",
                since=datetime.now(UTC),
                until=datetime.now(UTC) + timedelta(days=7),
                timezone_=LAGOS,
            )

    def _review(self, *, has_block: bool):
        from types import SimpleNamespace

        return SimpleNamespace(
            id="review-1",
            topic_id="topic-1",
            next_review_at=datetime.now(UTC) + timedelta(days=1),
            topic=SimpleNamespace(title="Recursion"),
            schedule_block=SimpleNamespace(id="block-1") if has_block else None,
        )

    async def test_an_item_without_a_block_becomes_a_day_scoped_entry(self):
        pending = await self._topic_reviews([self._review(has_block=False)])

        assert len(pending) == 1
        entry = pending[0].build(None, None, "no_room", None)
        assert entry.source == "review"
        assert entry.title == "Review: Recursion"
        assert entry.timed is False
        assert entry.links["reviewItemId"] == "review-1"

    async def test_an_item_that_already_holds_a_block_is_skipped(self):
        assert await self._topic_reviews([self._review(has_block=True)]) == []

    async def test_a_failed_read_costs_this_source_only(self):
        from unittest.mock import patch

        def _boom():
            raise RuntimeError("database gone")

        with patch("src.shared.database.get_session_factory", _boom):
            assert (
                await agenda._read_topic_reviews(
                    user_id="u1",
                    since=datetime.now(UTC),
                    until=datetime.now(UTC) + timedelta(days=1),
                    timezone_=LAGOS,
                )
                == []
            )


class TestAnAcceptedPlanItemIsNotSuggestedTwice:
    """Accepting a suggestion writes a `ScheduleBlock`, and the item stays `PENDING` — scheduled is not
    done. Without a link recorded, the next read returns the new block *and* the same item still being
    offered a time: one commitment shown twice, the second copy inviting the learner to schedule what
    they just scheduled. Migration 048 added `StudyPlanItem.scheduleBlockId` for this, the same link
    `ReviewItem` already had.
    """

    async def _plan_items(self, rows, *, holding=()):
        from unittest.mock import patch

        async def _rows(_user_id, *, until):
            return rows

        async def _holding(_item_ids):
            return set(holding)

        from src.domains.personal_learning.repository import personal_learning_repo

        with (
            patch.object(personal_learning_repo, "list_items_due_by", _rows),
            patch.object(agenda, "_plan_items_holding_a_block", _holding),
        ):
            return await agenda._read_plan_items(
                user_id="u1",
                since=datetime.now(UTC),
                until=datetime.now(UTC) + timedelta(days=7),
                timezone_=LAGOS,
            )

    def _item(self, item_id="item-1"):
        from types import SimpleNamespace

        return (
            SimpleNamespace(
                id=item_id,
                title="Read chapter 3",
                description=None,
                scheduled_date=datetime.now(UTC) + timedelta(days=1),
                estimated_minutes=30,
                topic_id="topic-1",
                plan_id="plan-1",
            ),
            SimpleNamespace(id="plan-1", title="Exam plan"),
        )

    async def test_an_item_with_no_block_is_offered_a_placement(self):
        pending = await self._plan_items([self._item()])

        assert len(pending) == 1

    async def test_an_item_that_already_holds_a_block_is_skipped(self):
        pending = await self._plan_items([self._item()], holding=("item-1",))

        assert pending == []

    async def test_only_the_accepted_item_is_skipped(self):
        pending = await self._plan_items(
            [self._item("item-1"), self._item("item-2")], holding=("item-1",)
        )

        assert len(pending) == 1

    async def test_nothing_is_queried_for_an_empty_list(self):
        assert await agenda._plan_items_holding_a_block([]) == set()

    async def test_accepting_records_the_link(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from src.domains.progress.services import schedule_service

        linked: dict[str, str] = {}

        async def _create(*, user_id, data):
            return SimpleNamespace(id="block-9", **data)

        async def _link(*, item_id, block_id):
            linked["item_id"] = item_id
            linked["block_id"] = block_id

        async def _find(**_kwargs):
            return SimpleNamespace(id="item-1", title="Read chapter 3", topic_id="topic-1")

        with (
            patch.object(schedule_service, "create_block", _create),
            patch.object(agenda, "_find_plan_item", _find),
            patch.object(agenda, "_link_plan_item_to_block", _link),
        ):
            block = await agenda.accept_placement(
                user_id="u1",
                entry_id="plan_item:item-1",
                start_at=datetime.now(UTC) + timedelta(hours=2),
                minutes=30,
            )

        assert block.id == "block-9"
        assert linked == {"item_id": "item-1", "block_id": "block-9"}

    async def test_a_review_batch_records_no_plan_item_link(self):
        """A review batch stands for a deck and a day, not a plan item."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from src.domains.progress.services import schedule_service

        calls: list[str] = []

        async def _create(*, user_id, data):
            return SimpleNamespace(id="block-9", **data)

        async def _link(*, item_id, block_id):
            calls.append(item_id)

        with (
            patch.object(schedule_service, "create_block", _create),
            patch.object(agenda, "_link_plan_item_to_block", _link),
        ):
            await agenda.accept_placement(
                user_id="u1",
                entry_id="review:deck-1:2026-08-24",
                start_at=datetime.now(UTC) + timedelta(hours=2),
                minutes=20,
            )

        assert calls == []

    async def test_a_failed_link_does_not_fail_the_acceptance(self):
        """The block exists and is the learner's answer. Losing the link costs a duplicate suggestion;
        raising here would cost them a block they cannot see."""
        from unittest.mock import patch

        def _boom():
            raise RuntimeError("no database")

        with patch("src.shared.database.get_session_factory", _boom):
            # Returns rather than raises.
            assert (
                await agenda._link_plan_item_to_block(item_id="item-1", block_id="block-9")
            ) is None


class TestTheSweepIsNotScheduled:
    def test_process_due_reviews_records_why_it_is_not_on_a_beat_schedule(self):
        """It materialises reviews into blocks, which the agenda makes unnecessary. Wiring it would put a
        second record of one commitment back, with the unlink-and-rewrite maintenance that needs."""
        import inspect

        from src.domains.progress.services import spaced_repetition_impl

        doc = inspect.getdoc(spaced_repetition_impl.process_due_reviews) or ""
        assert "beat schedule" in doc
        assert "agenda_service" in doc

    def test_the_beat_schedule_does_not_include_it(self):
        from src.workers import progress_tasks

        schedule = progress_tasks.get_beat_schedule()
        tasks = {entry.get("task") for entry in schedule.values()}
        assert "progress.process_spaced_repetition" not in tasks
