"""`check_credit_availability` and `consume_credits` against the rolling window.

`test_credit_service.py` covers the arithmetic — the unit, and `window_state` as a pure function.
This file covers the two entry points every metered operation in the product goes through, which is
where the questions that matter live: does a refused operation charge, does a read reset anything,
does the monthly backstop bind before the window does, and does the Space path stay out of it.

Both entry points reach a database, so the repository and the resolver are patched. That is not a
compromise for speed: the behaviour under test is *which* writes happen and in what order, and a
fake repository is the only way to assert that a refusal wrote nothing at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.domains.billing.services import credit_consumption_service as meter
from src.domains.billing.services import entitlement_service
from src.shared.exceptions import SubscriptionLimitError

NOW = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)


class FakeUser:
    """Only the columns the meter reads. Mutable, so a test can assert on what was written."""

    def __init__(self, **kwargs):
        self.id = "user-1"
        self.email = "learner@example.com"
        self.name = "Learner"
        self.usage_window_started_at = None
        self.usage_window_units_used = 0
        self.usage_month_started_at = None
        self.usage_month_units_used = 0
        # Decision M's proactive sub-budget: the same units as the month above, counted a
        # second time so a background task cannot quietly spend an allowance the learner
        # was saving for a study session.
        self.usage_month_proactive_units_used = 0
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeIdentityRepo:
    """Records every write, so "a refusal writes nothing" is assertable rather than assumed."""

    def __init__(self, user: FakeUser):
        self.user = user
        self.updates: list[dict] = []

    async def find_by_id(self, _user_id):
        return self.user

    async def update(self, _user_id, data):
        self.updates.append(data)
        for column, value in data.items():
            # The repository takes camelCase column names; the model exposes snake_case attributes.
            snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in column)
            setattr(self.user, snake, value)
        return self.user


def entitlement(
    *,
    tier="free",
    window_allowance=500,
    monthly_backstop=5_000,
    voice_seconds=0,
) -> entitlement_service.Entitlement:
    """A fixed entitlement for the *unit* meter.

    `voice_seconds` defaults to zero and every test in this file leaves it there, deliberately: voice
    has its own counter (§6.3) and none of it should reach the window. `tests/test_voice_balance.py`
    covers the voice side, and `TestVoiceIsADifferentMeter` below asserts the separation from here.
    """
    return entitlement_service.Entitlement(
        tier=tier,
        source="none" if tier == "free" else "subscription",
        expires_at=None,
        pass_id=None,
        subscription_tier=None,
        is_trial=False,
        trial_days_remaining=None,
        window_allowance=window_allowance,
        monthly_backstop=monthly_backstop,
        voice_seconds_included=voice_seconds,
        voice_allowance_source_id=None if voice_seconds == 0 else "subscription:test",
    )


@pytest.fixture
def world(monkeypatch):
    """A learner, a fake repository, a fixed entitlement and a frozen clock."""
    user = FakeUser()
    repo = FakeIdentityRepo(user)
    state = {"entitlement": entitlement(), "emails": [], "now": NOW}

    monkeypatch.setattr(meter, "IdentityRepository", lambda: repo)

    async def fake_resolve(_user_id):
        return state["entitlement"]

    monkeypatch.setattr(entitlement_service, "resolve", fake_resolve)

    async def fake_notify(u, s):
        state["emails"].append((u.id, s.resets_at))

    monkeypatch.setattr(meter, "_notify_limit_reached", fake_notify)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(meter, "datetime", FrozenClock)

    return type(
        "World",
        (),
        {
            "user": user,
            "repo": repo,
            "state": state,
            "set_entitlement": staticmethod(
                lambda **kwargs: state.__setitem__("entitlement", entitlement(**kwargs))
            ),
            "advance": staticmethod(
                lambda **kwargs: state.__setitem__("now", state["now"] + timedelta(**kwargs))
            ),
        },
    )


class TestTheWindowOpensOnUse:
    @pytest.mark.asyncio
    async def test_the_first_operation_opens_the_window(self, world):
        result = await meter.consume_credits(world.user, 100, "chat_message")
        assert world.user.usage_window_started_at == NOW
        assert world.user.usage_window_units_used == 100
        assert result.window_resets_at == NOW + timedelta(hours=5)

    @pytest.mark.asyncio
    async def test_a_second_operation_adds_to_the_same_window(self, world):
        await meter.consume_credits(world.user, 100, "chat_message")
        world.advance(hours=1)
        result = await meter.consume_credits(world.user, 50, "chat_message")
        assert world.user.usage_window_units_used == 150
        # Still five hours from when the *first* operation opened it, not from now. This is the
        # property that makes a reset time worth showing a learner.
        assert result.window_resets_at == NOW + timedelta(hours=5)

    @pytest.mark.asyncio
    async def test_the_window_resets_on_the_first_operation_after_it_elapses(self, world):
        await meter.consume_credits(world.user, 480, "chat_message")
        world.advance(hours=5, minutes=1)
        result = await meter.consume_credits(world.user, 100, "chat_message")
        assert world.user.usage_window_units_used == 100
        assert result.window_resets_at == NOW + timedelta(hours=10, minutes=1)

    @pytest.mark.asyncio
    async def test_an_operation_that_arrives_after_two_idle_windows_opens_a_fresh_one(self, world):
        """Windows tumble rather than tile: a learner who returns after eleven hours starts a window
        now, not one back-dated to a boundary they were not present for. Tiling would hand them a
        window with minutes left on it."""
        await meter.consume_credits(world.user, 500, "chat_message")
        world.advance(hours=11)
        await meter.consume_credits(world.user, 10, "chat_message")
        assert world.user.usage_window_started_at == NOW + timedelta(hours=11)


class TestReadsDoNotReset:
    @pytest.mark.asyncio
    async def test_checking_availability_writes_nothing(self, world):
        """The check runs before every metered operation, including ones that go on to be refused. If
        it wrote, a refused learner's window would be reset by the act of refusing them.
        """
        await meter.consume_credits(world.user, 400, "chat_message")
        world.repo.updates.clear()
        await meter.check_credit_availability(world.user, 50)
        assert world.repo.updates == []

    @pytest.mark.asyncio
    async def test_reading_usage_writes_nothing(self, world):
        await meter.consume_credits(world.user, 400, "chat_message")
        world.repo.updates.clear()
        await meter.get_credit_usage(world.user)
        assert world.repo.updates == []

    @pytest.mark.asyncio
    async def test_reading_after_the_window_elapsed_reports_a_full_allowance_without_writing(
        self, world
    ):
        """The learner sees a full allowance because their window has elapsed, not because looking at
        it refilled it. `usageWindowStartedAt` stays where it was until an operation moves it.
        """
        await meter.consume_credits(world.user, 500, "chat_message")
        world.advance(hours=6)
        usage = await meter.get_credit_usage(world.user)
        assert usage["percentUsed"] == 0.0
        assert usage["isExhausted"] is False
        assert world.user.usage_window_started_at == NOW


class TestRefusal:
    @pytest.mark.asyncio
    async def test_an_operation_that_does_not_fit_is_refused(self, world):
        await meter.consume_credits(world.user, 450, "chat_message")
        with pytest.raises(SubscriptionLimitError):
            await meter.consume_credits(world.user, 100, "chat_message")

    @pytest.mark.asyncio
    async def test_a_refused_operation_charges_nothing(self, world):
        await meter.consume_credits(world.user, 450, "chat_message")
        world.repo.updates.clear()
        with pytest.raises(SubscriptionLimitError):
            await meter.consume_credits(world.user, 100, "chat_message")
        assert world.repo.updates == []
        assert world.user.usage_window_units_used == 450

    @pytest.mark.asyncio
    async def test_an_operation_that_exactly_fills_the_allowance_is_allowed(self, world):
        """`>` not `>=`. An operation that exactly spends what is left is affordable, and refusing it
        would make the last unit of every window unusable."""
        result = await meter.consume_credits(world.user, 500, "chat_message")
        assert result.window_units_used == 500

    @pytest.mark.asyncio
    async def test_the_refusal_carries_the_reset_time_structurally(self, world):
        """So a client can render a countdown. The sentence deliberately does not contain a time — it
        would be in the wrong timezone."""
        await meter.consume_credits(world.user, 500, "chat_message")
        with pytest.raises(SubscriptionLimitError) as excinfo:
            await meter.consume_credits(world.user, 1, "chat_message")
        assert excinfo.value.window_resets_at == (NOW + timedelta(hours=5)).isoformat()

    @pytest.mark.asyncio
    async def test_a_refusal_emails_the_learner(self, world):
        await meter.consume_credits(world.user, 500, "chat_message")
        with pytest.raises(SubscriptionLimitError):
            await meter.consume_credits(world.user, 1, "chat_message")
        assert world.state["emails"]


class TestTheWarning:
    @pytest.mark.asyncio
    async def test_no_warning_below_eighty_percent(self, world):
        result = await meter.consume_credits(world.user, 399, "chat_message")
        assert result.warning is None

    @pytest.mark.asyncio
    async def test_a_warning_at_eighty_percent(self, world):
        result = await meter.consume_credits(world.user, 400, "chat_message")
        assert result.warning is not None

    @pytest.mark.asyncio
    async def test_the_warning_comes_with_the_reset_time(self, world):
        """A warning without a time is an apology. The time is a field rather than a phrase for the
        same reason as the refusal's."""
        result = await meter.consume_credits(world.user, 450, "chat_message")
        assert result.warning
        assert result.window_resets_at == NOW + timedelta(hours=5)

    @pytest.mark.asyncio
    async def test_the_check_warns_before_the_operation_runs(self, world):
        """The warning belongs on the check too, so a client can show it on the turn that crosses the
        threshold rather than the one after."""
        await meter.consume_credits(world.user, 390, "chat_message")
        available, warning = await meter.check_credit_availability(world.user, 20)
        assert available
        assert warning is not None


class TestTheMonthlyBackstop:
    @pytest.mark.asyncio
    async def test_the_backstop_binds_even_when_the_window_has_room(self, world):
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        world.user.usage_month_units_used = 4_990
        with pytest.raises(SubscriptionLimitError) as excinfo:
            await meter.consume_credits(world.user, 100, "chat_message")
        assert "month" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_a_monthly_refusal_offers_no_reset_time(self, world):
        """The window's reset would pass within five hours and change nothing, so promising it would be
        worse than promising nothing."""
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        world.user.usage_month_units_used = 5_000
        with pytest.raises(SubscriptionLimitError) as excinfo:
            await meter.consume_credits(world.user, 1, "chat_message")
        assert excinfo.value.window_resets_at is None

    @pytest.mark.asyncio
    async def test_the_month_accumulates_across_windows(self, world):
        await meter.consume_credits(world.user, 500, "chat_message")
        world.advance(hours=6)
        await meter.consume_credits(world.user, 500, "chat_message")
        assert world.user.usage_window_units_used == 500
        assert world.user.usage_month_units_used == 1_000

    @pytest.mark.asyncio
    async def test_an_entitlement_with_no_backstop_is_unbounded_monthly(self, world):
        """A pass is bounded by its own allowance rather than by the calendar (Decision E), so it
        reports `None` and the meter must read that as unbounded rather than as zero."""
        world.set_entitlement(window_allowance=3_000, monthly_backstop=None)
        world.user.usage_month_units_used = 9_999_999
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        result = await meter.consume_credits(world.user, 100, "chat_message")
        assert result.units_consumed == 100

    @pytest.mark.asyncio
    async def test_the_backstop_is_hidden_until_it_is_nearly_reached(self, world):
        """An abuse limit a learner never meets is better not mentioned; naming it invites planning
        around a number designed not to bind (§6.3)."""
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        world.user.usage_month_units_used = 1_000
        usage = await meter.get_credit_usage(world.user)
        assert "monthlyPercentUsed" not in usage

    @pytest.mark.asyncio
    async def test_the_backstop_is_disclosed_once_it_is_in_reach(self, world):
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        world.user.usage_month_units_used = 4_500
        usage = await meter.get_credit_usage(world.user)
        assert usage["monthlyPercentUsed"] == 90.0

    @pytest.mark.asyncio
    async def test_monthly_exhaustion_is_reported_whether_or_not_the_percentage_is(self, world):
        """The boolean is what a refusal reads to decide which remedy to describe, so it cannot be
        gated on the disclosure threshold the way the percentage is."""
        world.user.usage_month_started_at = datetime(2026, 3, 1, tzinfo=UTC)
        world.user.usage_month_units_used = 1_000
        assert (await meter.get_credit_usage(world.user))["monthlyExhausted"] is False


class TestWhatTheLearnerIsShown:
    @pytest.mark.asyncio
    async def test_no_unit_count_is_reported(self, world):
        """Units are $0.0001 of measured COGS. A raw count would leak our cost basis into the UI and
        invite arithmetic; the marketing states checkable equivalents instead."""
        await meter.consume_credits(world.user, 250, "chat_message")
        usage = await meter.get_credit_usage(world.user)
        assert set(usage) == {
            "tier",
            "windowResetsAt",
            "percentUsed",
            "isExhausted",
            "monthlyExhausted",
        }

    @pytest.mark.asyncio
    async def test_the_percentage_is_of_the_window(self, world):
        await meter.consume_credits(world.user, 250, "chat_message")
        assert (await meter.get_credit_usage(world.user))["percentUsed"] == 50.0

    @pytest.mark.asyncio
    async def test_the_percentage_cannot_exceed_a_hundred(self, world):
        """A single operation is charged in full even when it overruns what it was checked against, so
        `used` can exceed the allowance and a raw ratio would report 104%."""
        await meter.consume_credits(world.user, 500, "chat_message")
        world.user.usage_window_units_used = 520
        assert (await meter.get_credit_usage(world.user))["percentUsed"] == 100.0

    @pytest.mark.asyncio
    async def test_the_allowance_reported_is_the_entitlement_s(self, world):
        """The denominator changes the moment a pass is activated, which is why the client is not
        allowed to hold its own copy of it."""
        world.set_entitlement(tier="plus", window_allowance=4_000, monthly_backstop=30_000)
        result = await meter.consume_credits(world.user, 100, "chat_message")
        assert result.window_allowance == 4_000


class TestSpacesAreOutOfScope:
    """Decision F. Both entry points take the Space branch before any window machinery is reached,
    which is what made the personal path separable — and this is the test that keeps it that way.
    """

    @pytest.mark.asyncio
    async def test_a_space_operation_does_not_touch_the_learners_window(self, world, monkeypatch):
        recorded = {}

        class FakeSpace:
            credits = 10
            credits_limit = 1_000

        async def fake_find(space_id):
            recorded["space_id"] = space_id
            return FakeSpace()

        monkeypatch.setattr(meter.space_repo, "find_space_basic", fake_find)

        available, warning = await meter.check_credit_availability(
            world.user, 100, space_id="space-1"
        )
        assert available and warning is None
        assert recorded["space_id"] == "space-1"
        assert world.repo.updates == []
        assert world.user.usage_window_started_at is None

    @pytest.mark.asyncio
    async def test_a_space_over_its_limit_is_refused_in_its_own_words(self, world, monkeypatch):
        class FakeSpace:
            credits = 990
            credits_limit = 1_000

        async def fake_find(_space_id):
            return FakeSpace()

        monkeypatch.setattr(meter.space_repo, "find_space_basic", fake_find)

        available, warning = await meter.check_credit_availability(
            world.user, 100, space_id="space-1"
        )
        assert not available
        assert warning and "Space" in warning


class TestVoiceIsADifferentMeter:
    """The scope guard for §6.3's unbundling, in the same shape as the Decision F guards.

    Voice used to be drawn from this window, and at 200 units a minute it is 40× a Flash-Lite chat
    turn — so a learner who spent their allowance on voice hit the COGS ceiling and one who spent it on
    text did not come close. Pricing one allowance for both meant pricing for a case almost nobody
    incurred.

    The failure mode to guard against is not a wrong number but a *reconnection*: someone reading
    "voice costs 200 units" and wiring voice back into `consume_credits`, which would look entirely
    reasonable and would undo the unbundling silently.
    """

    def test_the_voice_allowance_is_not_derivable_from_the_window(self):
        from src.domains.billing.services import entitlement_service as ent

        # A 7-day pass has the same window as the subscription and fewer voice minutes. No function of
        # the window allowance can produce both.
        assert ent.WINDOW_ALLOWANCE_PASS_7D == ent.WINDOW_ALLOWANCE_PLUS
        assert ent.VOICE_SECONDS_PASS_7D != ent.VOICE_SECONDS_PLUS_MONTHLY

    def test_the_voice_rate_is_a_cost_basis_and_charges_nothing(self):
        """`GEMINI_LIVE_UNITS_PER_MINUTE` survives only to price the margin tables and to make "40× a
        chat turn" checkable. If it were still converting voice time into units to charge for it, that
        conversion would be a path back into this window — and it is the same conversion that hid a
        100× under-price for the life of the feature.
        """
        from src.domains.study_voice import billing as voice

        assert not hasattr(voice, "units_from_billable_seconds_raw")
        assert not hasattr(voice, "units_total_final_settlement")
        assert voice.chargeable_seconds_raw(60.0) == 60

    def test_the_voice_path_does_not_import_the_unit_meter(self):
        """Asserted structurally, because this is the reconnection that would be easiest to make by
        accident. `study_voice` charges through `voice_service` now; a `consume_credits` in the bridge
        or the settlement would put voice minutes back on the window.
        """
        from pathlib import Path

        voice_dir = Path(__file__).resolve().parents[1] / "src" / "domains" / "study_voice"
        for module in ("bridge.py", "settlement.py", "routes.py"):
            source = (voice_dir / module).read_text(encoding="utf-8", errors="replace")
            # Imports and calls, not mentions. Both modules discuss `consume_credits` in prose,
            # recording what they used to do and why they stopped — which is worth keeping, and is
            # exactly the sort of thing a substring check would have forbidden.
            for line in source.splitlines():
                code = line.split("#", 1)[0]
                assert "import consume_credits" not in code, f"{module} imports the unit meter"
                assert "consume_credits(" not in code, (
                    f"{module} charges through the unit meter again, which puts voice minutes back "
                    "on the 5-hour window they were unbundled from"
                )


class TestOneRowPerMeteredOperation:
    """`UsageEvent` is what makes §6.5 and Decision P checkable instead of trusted.

    The window counters say how much a month cost; nothing said on what. §6.5 estimates a unit cost
    for each of 27 operations and Decision P draws the model-quality threshold at 500 of them, and
    until this table existed no query could contradict either — `record_units` advanced two aggregates
    and logged the label, and `LlmCostRecord` has no operation column and is written on the chat path
    alone.

    Recorded as an addition to Decision L rather than an unfinished half of it: L asked for cost to be
    measured rather than tabulated, which `units_for_tokens` does. Per-operation persistence is a new
    requirement that follows from Decision P needing a checkable threshold.
    """

    @staticmethod
    def _capture(monkeypatch):
        """Collect the rows `_record_usage_event` would insert, without a database."""
        added: list = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def add(self, row):
                added.append(row)

            async def commit(self):
                return None

        monkeypatch.setattr(meter, "get_session_factory", lambda: FakeSession)
        return added

    @pytest.mark.asyncio
    async def test_a_charge_writes_one_itemised_row(self, world, monkeypatch):
        added = self._capture(monkeypatch)
        await meter.record_units(
            "user-1", 780, operation="quiz_generation", model="gemini-3.5-flash"
        )

        assert len(added) == 1
        row = added[0]
        assert (row.user_id, row.operation, row.units, row.model) == (
            "user-1",
            "quiz_generation",
            780,
            "gemini-3.5-flash",
        )
        assert row.proactive is False

    @pytest.mark.asyncio
    async def test_the_proactive_tag_is_carried_per_operation(self, world, monkeypatch):
        """Decision M rule 1 tags the month aggregate; carrying it here too is what lets the proactive
        share be attributed to the *tasks* that spent it rather than only totalled.
        """
        added = self._capture(monkeypatch)
        await meter.record_units("user-1", 140, operation="home_guidance", proactive=True)
        assert added[0].proactive is True

    @pytest.mark.asyncio
    async def test_nothing_is_written_for_a_zero_charge(self, world, monkeypatch):
        """`record_units` returns early on a non-positive amount, and the row must not outlive that
        guard — otherwise the table fills with rows recording that nothing happened.
        """
        added = self._capture(monkeypatch)
        await meter.record_units("user-1", 0, operation="quiz_generation")
        assert added == []

    @pytest.mark.asyncio
    async def test_a_missing_model_is_null_rather_than_a_guess(self, world, monkeypatch):
        """A provider reply can carry usage without a model name. A null says so; a default would
        attribute the cost to a model that may not have served it.
        """
        added = self._capture(monkeypatch)
        await meter.record_units("user-1", 100, operation="note_summary")
        assert added[0].model is None

    @pytest.mark.asyncio
    async def test_a_failed_row_does_not_cost_the_learner_their_counters(self, world, monkeypatch):
        """The ordering guarantee, and the reason the insert sits outside the counter update's `try`.

        The counters are the learner's accounting; the row is our analytics. A broken insert must not
        reach the caller *or* undo the accounting — the two answer different questions and neither is a
        precondition of the other.
        """

        def explode():
            raise RuntimeError("no database")

        monkeypatch.setattr(meter, "get_session_factory", explode)
        await meter.record_units("user-1", 500, operation="lesson_body")

        # The counters landed regardless.
        assert world.user.usage_window_units_used == 500
        assert world.user.usage_month_units_used == 500

    @pytest.mark.asyncio
    async def test_a_failed_counter_update_still_itemises(self, world, monkeypatch):
        """The converse, which matters for the same reason. If a counter write fails we have
        under-charged, and the row is the only remaining evidence of what was actually served.
        """
        added = self._capture(monkeypatch)

        class BrokenRepo:
            async def find_by_id(self, _user_id):
                raise RuntimeError("no database")

        monkeypatch.setattr(meter, "IdentityRepository", BrokenRepo)
        await meter.record_units("user-1", 300, operation="course_outline")

        assert len(added) == 1
        assert added[0].units == 300

    @pytest.mark.asyncio
    async def test_the_meter_passes_the_model_through(self, monkeypatch):
        """The chokepoint has `usage.model` and the row wants it, so the argument has to survive the
        hop. Asserted because a silently dropped keyword would leave every row's model null and the
        Decision P check unanswerable — while looking like it worked.
        """
        import inspect

        from src.domains.personal_learning.services import llm_resilient

        source = inspect.getsource(llm_resilient.meter_usage)
        assert "model=usage.model" in source
        assert "model" in inspect.signature(meter.record_units).parameters
