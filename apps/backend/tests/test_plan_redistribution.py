"""The nightly pass that repacks a drifted study plan (no DB required).

**This is the defect these tests exist for.** `_redistribute_plan` could only be reached two ways: the
learner editing the plan's schedule inputs, or the learner marking an item complete while more than two
pending items sat past due. Both require them to open the app and act. So the plans that had drifted
furthest — belonging to the learners who had stopped completing anything — were precisely the ones nothing
ever rescheduled. Rescheduling reached the active and skipped the stuck.

The sweep decides nothing new. The drift threshold and the placement arithmetic are shared with the path a
learner triggers, so what is pinned here is mostly the *restraint*: that it does not churn a plan nightly,
does not touch a plan the learner paused, does not collapse an expired one, does not move an hour the
learner accepted, and does not go quiet about having rewritten their schedule.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.personal_learning.services import study_plan_service as svc  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _plan(plan_id="plan-1", **overrides) -> SimpleNamespace:
    defaults = {
        "id": plan_id,
        "user_id": "user-1",
        "title": "Linear algebra",
        "status": "ACTIVE",
        "deadline": NOW + timedelta(days=20),
        "session_minutes": 60,
        "preferred_days": None,
        "last_redistributed_at": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _item(item_id, *, days_ago=5, status="PENDING", minutes=30, block=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        status=status,
        scheduled_date=NOW - timedelta(days=days_ago),
        estimated_minutes=minutes,
        schedule_block_id=block,
    )


class FakeRepo:
    def __init__(self, plans, items_by_plan):
        self.plans = plans
        self.items_by_plan = items_by_plan
        self.item_updates: list[tuple[str, dict]] = []
        self.plan_updates: list[tuple[str, dict]] = []
        self.drift_query: dict | None = None
        self.fail_on: set[str] = set()

    async def list_plans_with_drift(self, *, now, min_past_due, not_swept_since, limit=500):
        self.drift_query = {
            "now": now,
            "min_past_due": min_past_due,
            "not_swept_since": not_swept_since,
            "limit": limit,
        }
        # Mirrors the real query's filters, so a test cannot pass by the fake being laxer than the
        # database: active, deadline ahead, off cooldown, and more than `min_past_due` pending items
        # already past their date.
        out = []
        for plan in self.plans:
            if plan.status != "ACTIVE" or plan.deadline <= now:
                continue
            if (
                plan.last_redistributed_at is not None
                and plan.last_redistributed_at >= not_swept_since
            ):
                continue
            past_due = [
                i
                for i in self.items_by_plan.get(plan.id, [])
                if i.status == "PENDING" and i.scheduled_date < now
            ]
            if len(past_due) > min_past_due:
                out.append(plan)
        return out[:limit]

    async def get_study_plan(self, plan_id, user_id, **_kw):
        if plan_id in self.fail_on:
            raise RuntimeError("boom")
        return next((p for p in self.plans if p.id == plan_id and p.user_id == user_id), None)

    async def list_plan_items(self, plan_id, **_kw):
        return list(self.items_by_plan.get(plan_id, []))

    async def update_plan_item(self, item_id, data, *, plan_id, **_kw):
        self.item_updates.append((item_id, data))
        item = next(i for i in self.items_by_plan[plan_id] if i.id == item_id)
        if "scheduledDate" in data:
            item.scheduled_date = data["scheduledDate"]
        return item

    async def update_study_plan(self, plan_id, user_id, data, **_kw):
        self.plan_updates.append((plan_id, data))
        plan = next(p for p in self.plans if p.id == plan_id)
        if "lastRedistributedAt" in data:
            plan.last_redistributed_at = data["lastRedistributedAt"]
        return plan

    async def get_profile_by_user(self, _user_id, **_kw):
        return None


class FakeNotifications:
    def __init__(self, suppress=False):
        self.sent: list[dict] = []
        self.suppress = suppress

    async def create_notification(self, **kwargs):
        self.sent.append(kwargs)
        return None if self.suppress else SimpleNamespace(id="n1")


@pytest.fixture
def wire(monkeypatch):
    def _wire(plans, items_by_plan, *, suppress=False):
        repo = FakeRepo(plans, items_by_plan)
        notifications = FakeNotifications(suppress=suppress)
        monkeypatch.setattr(svc, "repo", repo)
        # Patched on the module rather than as an attribute of the caller: the service does a
        # function-local `from . import notification_service`, which rebinds and would ignore the latter.
        monkeypatch.setattr(
            "src.domains.personal_learning.services.notification_service.create_notification",
            notifications.create_notification,
        )
        return repo, notifications

    return _wire


class TestTheGap:
    @pytest.mark.asyncio
    async def test_a_silent_learners_plan_is_finally_redistributed(self, wire):
        """**The original defect, pinned.** Nothing the learner does triggers this; it happens to them."""
        plan = _plan()
        items = [_item("i1"), _item("i2"), _item("i3"), _item("i4")]
        repo, _ = wire([plan], {plan.id: items})

        count = await svc.redistribute_drifted_plans(now=NOW)

        assert count == 1
        assert len(repo.item_updates) == 4

    @pytest.mark.asyncio
    async def test_nothing_is_moved_into_the_past_or_onto_today(self, wire):
        """Redistribution starts tomorrow. Moving pending work onto the day already in progress would
        put it behind before the learner had seen it."""
        plan = _plan()
        items = [_item(f"i{n}") for n in range(4)]
        wire([plan], {plan.id: items})

        await svc.redistribute_drifted_plans(now=NOW)

        assert all(i.scheduled_date > NOW for i in items)

    @pytest.mark.asyncio
    async def test_nothing_is_scheduled_past_the_deadline(self, wire):
        """A deadline three days out and twenty items: the overflow packs into the last available day
        rather than spilling past the date printed above the plan."""
        plan = _plan(deadline=NOW + timedelta(days=3), session_minutes=30)
        items = [_item(f"i{n}") for n in range(20)]
        wire([plan], {plan.id: items})

        await svc.redistribute_drifted_plans(now=NOW)

        assert all(i.scheduled_date <= plan.deadline for i in items)


class TestItDoesNotChurn:
    @pytest.mark.asyncio
    async def test_the_plan_is_stamped_so_tomorrow_finds_nothing(self, wire):
        """Without this the sweep re-anchors every pending date to tomorrow, every night, and the
        learner's schedule never settles."""
        plan = _plan()
        items = [_item(f"i{n}") for n in range(4)]
        repo, _ = wire([plan], {plan.id: items})

        await svc.redistribute_drifted_plans(now=NOW)

        assert plan.last_redistributed_at is not None
        assert any("lastRedistributedAt" in data for _, data in repo.plan_updates)

    @pytest.mark.asyncio
    async def test_a_plan_repacked_this_week_is_left_alone(self, wire):
        plan = _plan(last_redistributed_at=NOW - timedelta(days=2))
        items = [_item(f"i{n}") for n in range(4)]
        repo, _ = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert repo.item_updates == []

    @pytest.mark.asyncio
    async def test_a_plan_repacked_longer_ago_than_the_cooldown_is_due_again(self, wire):
        plan = _plan(
            last_redistributed_at=NOW - timedelta(days=svc.REDISTRIBUTION_COOLDOWN_DAYS + 1)
        )
        items = [_item(f"i{n}") for n in range(4)]
        wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 1

    @pytest.mark.asyncio
    async def test_the_cooldown_window_is_what_is_asked_of_the_database(self, wire):
        plan = _plan()
        repo, _ = wire([plan], {plan.id: []})

        await svc.redistribute_drifted_plans(now=NOW)

        expected = NOW - timedelta(days=svc.REDISTRIBUTION_COOLDOWN_DAYS)
        assert repo.drift_query["not_swept_since"] == expected

    @pytest.mark.asyncio
    async def test_a_plan_that_moved_nothing_still_goes_on_cooldown(self, wire):
        """Every pending item is pinned to an accepted calendar block, so there is nothing to move.
        Stamping only on success would leave this plan reconsidered every night forever — the trap the
        weekly check-in documents for suppressed notifications."""
        plan = _plan()
        items = [_item(f"i{n}", block=f"block-{n}") for n in range(4)]
        repo, notifications = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert plan.last_redistributed_at is not None
        assert repo.item_updates == []
        # And nothing is announced, because nothing happened.
        assert notifications.sent == []


class TestWhatItRefusesToTouch:
    @pytest.mark.asyncio
    async def test_the_drift_threshold_is_the_one_the_learner_path_uses(self, wire):
        """Two definitions of "behind" would mean a plan that is drifted when the learner completes
        something and not drifted overnight."""
        plan = _plan()
        repo, _ = wire([plan], {plan.id: []})

        await svc.redistribute_drifted_plans(now=NOW)

        assert repo.drift_query["min_past_due"] == svc.MAX_TOLERATED_PAST_DUE

    @pytest.mark.asyncio
    async def test_a_plan_only_slightly_behind_is_left_alone(self, wire):
        plan = _plan()
        items = [_item("i1"), _item("i2")]
        repo, _ = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert repo.item_updates == []

    @pytest.mark.asyncio
    async def test_a_paused_plan_is_not_rescheduled(self, wire):
        """Pausing is not a statement about the deadline. A paused plan keeps its items and its dates,
        and rescheduling it would override what the learner asked for."""
        plan = _plan(status="PAUSED")
        items = [_item(f"i{n}") for n in range(4)]
        repo, _ = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert repo.item_updates == []

    @pytest.mark.asyncio
    async def test_an_expired_plan_is_not_collapsed_onto_tomorrow(self, wire):
        """`days_remaining = max(1, (deadline - now).days)`, so a deadline in the past yields a one-day
        window and every pending item piles onto tomorrow. That is a wall, not a schedule, and what to do
        with an expired plan is a question for the learner."""
        plan = _plan(deadline=NOW - timedelta(days=1))
        items = [_item(f"i{n}") for n in range(4)]
        repo, _ = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert repo.item_updates == []

    @pytest.mark.asyncio
    async def test_an_hour_the_learner_accepted_is_not_moved(self, wire):
        """`scheduleBlockId` means the learner accepted a suggested time and a real `ScheduleBlock` sits
        on that day. Moving the item without the block gives them a calendar entry on one day and a plan
        item on another — and the day they turn up is the one in their calendar."""
        plan = _plan()
        pinned = _item("pinned", block="block-1")
        loose = [_item(f"i{n}") for n in range(4)]
        repo, _ = wire([plan], {plan.id: [pinned, *loose]})
        original = pinned.scheduled_date

        await svc.redistribute_drifted_plans(now=NOW)

        assert pinned.scheduled_date == original
        assert "pinned" not in [item_id for item_id, _ in repo.item_updates]
        assert len(repo.item_updates) == 4

    @pytest.mark.asyncio
    async def test_a_skipped_item_is_not_treated_as_a_backlog(self, wire):
        """`SKIPPED` is the learner saying "not doing this". Counting it as drift would turn their
        decision into a reason to reschedule."""
        plan = _plan()
        items = [_item(f"s{n}", status="SKIPPED") for n in range(6)]
        repo, _ = wire([plan], {plan.id: items})

        assert await svc.redistribute_drifted_plans(now=NOW) == 0
        assert repo.item_updates == []

    @pytest.mark.asyncio
    async def test_a_completed_item_is_never_moved(self, wire):
        plan = _plan()
        done = _item("done", status="COMPLETED")
        items = [done, *[_item(f"i{n}") for n in range(4)]]
        repo, _ = wire([plan], {plan.id: items})
        original = done.scheduled_date

        await svc.redistribute_drifted_plans(now=NOW)

        assert done.scheduled_date == original
        assert "done" not in [item_id for item_id, _ in repo.item_updates]


class TestItSaysSo:
    @pytest.mark.asyncio
    async def test_the_learner_is_told_their_plan_moved(self, wire):
        """They did not ask for this. A schedule rewritten overnight with no word is the system changing
        their commitments behind their back — and the phase boundaries they accepted in the wizard move
        with it, since a phase's week range is the span of its items' dates."""
        plan = _plan()
        items = [_item(f"i{n}") for n in range(4)]
        _, notifications = wire([plan], {plan.id: items})

        await svc.redistribute_drifted_plans(now=NOW)

        assert len(notifications.sent) == 1
        sent = notifications.sent[0]
        assert sent["type"] == "study_plan_redistributed"
        assert plan.title in sent["title"]
        assert "4 tasks" in sent["body"]
        assert sent["action_data"]["planId"] == plan.id

    @pytest.mark.asyncio
    async def test_a_suppressed_notification_does_not_undo_the_cooldown(self, wire):
        """A notification that reaches nobody must not reopen the cooldown. Quiet hours hold a message until
        morning and the learner's daily allowance can defer it to tomorrow; before phase 5 the allowance
        destroyed it outright and returned `None`, which is what this fake reproduces. The repack still
        happened either way, so the stamp must stand — otherwise the plan is repacked again tomorrow to
        retry a message, and the learner's dates move a second time for the sake of an announcement.
        """
        plan = _plan()
        items = [_item(f"i{n}") for n in range(4)]
        wire([plan], {plan.id: items}, suppress=True)

        assert await svc.redistribute_drifted_plans(now=NOW) == 1
        assert plan.last_redistributed_at is not None


class TestOneBadPlan:
    @pytest.mark.asyncio
    async def test_does_not_abort_the_whole_run(self, wire):
        """`run_weekly_check_ins` has no per-row guard and one bad plan ends the sweep;
        `check_declining_engagement` gets this right. This follows the latter."""
        first, second = _plan("plan-1"), _plan("plan-2")
        items = {
            "plan-1": [_item(f"a{n}") for n in range(4)],
            "plan-2": [_item(f"b{n}") for n in range(4)],
        }
        repo, _ = wire([first, second], items)
        repo.fail_on = {"plan-1"}

        assert await svc.redistribute_drifted_plans(now=NOW) == 1
        assert [item_id for item_id, _ in repo.item_updates] == ["b0", "b1", "b2", "b3"]


class TestTheQueryItself:
    """The filters that keep the sweep safe live in SQL, so they are asserted against the statement.

    A fake repository can only prove the service asks the right question. These prove the question the
    database is actually asked, which is where "never touch a paused plan" is enforced.
    """

    @pytest.mark.asyncio
    async def test_the_filters_are_in_the_statement(self):
        from src.domains.personal_learning.repository import personal_learning_repo

        captured: list = []

        class _Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        class _Session:
            async def execute(self, stmt):
                captured.append(stmt)
                return _Result()

        await personal_learning_repo.list_plans_with_drift(
            now=NOW,
            min_past_due=2,
            not_swept_since=NOW - timedelta(days=7),
            session=_Session(),
        )

        sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
        assert "'ACTIVE'" in sql
        assert "deadline >" in sql
        assert '"lastRedistributedAt" IS NULL' in sql
        assert '"lastRedistributedAt" <' in sql
        # The drift subquery: pending items already past their date, more than the threshold of them.
        assert "'PENDING'" in sql
        assert '"scheduledDate" <' in sql
        assert "HAVING count(" in sql
        assert "> 2" in sql
