"""The usage unit and the rolling window: pure logic, no database.

**What this file used to test, and why none of it survived.** Three tests: that
`get_credit_limits("FREE")["hard_cap"] == 15000`, that `TOKEN_MULTIPLIER == 0.2` and that
`CREDIT_COSTS` had a `chat_message` key. All three asserted that a constant still held the value it
was typed with, which is the kind of test that passes while the thing it guards is wrong — the voice
rate was mispriced by 100× for the life of the feature and no test here noticed, because none of them
compared a figure to a cost.

What replaces them tests relationships instead of values: that a unit is small enough not to round a
cheap operation to nothing, that rounding goes up rather than down, that a window opens on use and
not on inspection, and that the free voice allowance is the number the marketing claims.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.domains.billing.services import credit_consumption_service as meter


class TestTheUnit:
    def test_a_unit_is_a_hundredth_of_a_cent(self):
        """10 000 units is a dollar. The rest of the file is arithmetic on top of this."""
        assert meter.USD_PER_UNIT == 0.0001
        assert meter.units_for_usd(1.0) == 10_000

    def test_nothing_costs_nothing(self):
        assert meter.units_for_usd(0) == 0
        assert meter.units_for_usd(-1.0) == 0

    def test_rounding_is_up_so_a_cheap_operation_is_never_free(self):
        """Down would let anything under half a unit run unmetered, and "free if small enough" is how
        an unmetered surface starts. A tenth of a unit costs one."""
        assert meter.units_for_usd(0.00001) == 1
        assert meter.units_for_usd(0.000149) == 2
        assert meter.units_for_usd(0.00015) == 2

    def test_an_exact_multiple_is_not_rounded_up_past_itself(self):
        """Rounding up must mean "up to the next whole unit", not "always add one" — otherwise every
        charge carries a silent surcharge."""
        assert meter.units_for_usd(0.0001) == 1
        assert meter.units_for_usd(0.0005) == 5

    def test_the_unit_is_coarse_enough_to_read_and_fine_enough_to_charge(self):
        """The reason the unit is $0.0001 and not $0.01 or $0.000001.

        A Flash-Lite chat turn costs about $0.0029 and course generation about $0.102. At a cent per
        unit the chat turn rounds to a single unit and the difference between the cheapest and the
        dearest operation collapses; at a millionth of a dollar the numbers stop being readable and
        buy precision the rate card does not have.
        """
        cheap = meter.units_for_usd(0.0029)
        dear = meter.units_for_usd(0.102)
        assert cheap > 1, "the cheapest real operation must cost more than one unit"
        assert dear < 10_000, "the dearest must still be a figure a person can read"
        assert dear > cheap * 10, "the unit must preserve the spread between operations"


class TestPricingAGeneration:
    def test_output_is_dearer_than_input(self):
        """The reason `units_for_tokens` takes the split rather than a total. Charging on
        `total_tokens`, as the meter used to, priced a 1 000-token answer the same as 1 000 tokens of
        prompt when the answer costs roughly six times as much."""
        prompt_heavy = meter.units_for_tokens(10_000, 0, "gemini-3.5-flash")
        answer_heavy = meter.units_for_tokens(0, 10_000, "gemini-3.5-flash")
        assert answer_heavy > prompt_heavy

    def test_the_same_generation_costs_more_on_a_dearer_model(self):
        """Self-balancing rather than a wrinkle: a Plus learner gets a larger allowance *and* a
        dearer model, and the ratio between the two is the margin."""
        on_plus = meter.units_for_tokens(8_000, 600, "gemini-3.5-flash")
        on_free = meter.units_for_tokens(8_000, 600, "gemini-3.5-flash-lite")
        assert on_plus > on_free

    def test_a_generation_that_used_nothing_costs_nothing(self):
        assert meter.units_for_tokens(0, 0, "gemini-3.5-flash") == 0


class TestTheWindow:
    @staticmethod
    def _user(**kwargs):
        class _U:
            usage_window_started_at = None
            usage_window_units_used = 0
            usage_month_started_at = None
            usage_month_units_used = 0

        user = _U()
        for key, value in kwargs.items():
            setattr(user, key, value)
        return user

    def test_a_learner_who_has_never_used_anything_has_a_full_window(self):
        """`usageWindowStartedAt` is null for a new account and the meter reads null as elapsed, so
        the first operation opens the window. That is why there is no initialisation step, and
        therefore no learner who can be missing one."""
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        state = meter.window_state(self._user(), now)
        assert state.units_used == 0
        assert state.started_at == now
        assert state.resets_at == now + timedelta(hours=meter.WINDOW_HOURS)
        assert state.rolled

    def test_an_open_window_is_reported_as_it_stands(self):
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        opened = now - timedelta(hours=2)
        state = meter.window_state(
            self._user(
                usage_window_started_at=opened,
                usage_window_units_used=140,
                # Set, because a null month rolls too and `rolled` covers both boundaries.
                usage_month_started_at=datetime(2026, 3, 1, tzinfo=UTC),
            ),
            now,
        )
        assert state.units_used == 140
        assert state.started_at == opened
        assert not state.rolled

    def test_an_elapsed_window_rolls_over_and_says_so(self):
        """`rolled` is how a caller knows the stored boundaries are stale, so a write persists the new
        window rather than only the increment."""
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        state = meter.window_state(
            self._user(
                usage_window_started_at=now - timedelta(hours=meter.WINDOW_HOURS),
                usage_window_units_used=500,
            ),
            now,
        )
        assert state.units_used == 0
        assert state.started_at == now
        assert state.rolled

    def test_the_boundary_is_inclusive_so_a_window_lasts_exactly_five_hours(self):
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        one_second_early = self._user(
            usage_window_started_at=now - timedelta(hours=meter.WINDOW_HOURS, seconds=-1),
            usage_window_units_used=500,
        )
        assert meter.window_state(one_second_early, now).units_used == 500

    def test_reading_the_window_does_not_write_it(self):
        """A learner who opens a page after six hours sees a full allowance because their window has
        elapsed, not because looking at it reset anything. If a read wrote, `resets_at` would be five
        hours after whenever they last *looked*, which nobody can predict."""
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        user = self._user(
            usage_window_started_at=now - timedelta(hours=6),
            usage_window_units_used=480,
        )
        meter.window_state(user, now)
        assert user.usage_window_started_at == now - timedelta(hours=6)
        assert user.usage_window_units_used == 480

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Postgres hands back tz-aware datetimes and SQLite does not. Without this the comparison
        raises, and it raises inside the meter on a read path — so a test database would fail
        differently from production."""
        now = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
        naive = datetime(2026, 3, 4, 11, 0)
        state = meter.window_state(
            self._user(usage_window_started_at=naive, usage_window_units_used=10), now
        )
        assert state.units_used == 10


class TestTheMonthlyBackstop:
    @staticmethod
    def _user(**kwargs):
        return TestTheWindow._user(**kwargs)

    def test_the_month_rolls_at_the_first_of_the_month(self):
        now = datetime(2026, 4, 1, 0, 30, tzinfo=UTC)
        state = meter.window_state(
            self._user(
                usage_month_started_at=datetime(2026, 3, 1, tzinfo=UTC),
                usage_month_units_used=4_900,
            ),
            now,
        )
        assert state.month_units_used == 0
        assert state.month_started_at == datetime(2026, 4, 1, tzinfo=UTC)
        assert state.rolled

    def test_the_month_does_not_roll_mid_month(self):
        now = datetime(2026, 3, 20, 9, 0, tzinfo=UTC)
        state = meter.window_state(
            self._user(
                usage_month_started_at=datetime(2026, 3, 1, tzinfo=UTC),
                usage_month_units_used=4_900,
                usage_window_started_at=now,
            ),
            now,
        )
        assert state.month_units_used == 4_900

    def test_the_month_and_the_window_roll_independently(self):
        """They answer different questions — the window is the product, the month is an abuse
        backstop — so a window rolling must not clear the month's total."""
        now = datetime(2026, 3, 20, 9, 0, tzinfo=UTC)
        state = meter.window_state(
            self._user(
                usage_window_started_at=now - timedelta(hours=9),
                usage_window_units_used=500,
                usage_month_started_at=datetime(2026, 3, 1, tzinfo=UTC),
                usage_month_units_used=4_900,
            ),
            now,
        )
        assert state.units_used == 0
        assert state.month_units_used == 4_900


class TestTheFiguresTheMarketingQuotes:
    """The allowances are only honest if the equivalents we advertise follow from them."""

    def test_a_voice_minute_costs_about_two_hundred_units(self):
        """§6.3. Loose bounds on purpose: this catches the rate drifting an order of magnitude, which
        is what actually happened, rather than pinning a figure Google will move.

        Asserted on `units_per_minute` directly now that it is a **cost basis** rather than a charging
        rate — voice is charged in seconds against its own balance, so nothing converts a minute into
        units in order to bill it. The figure still has to be right, because the margin tables and the
        "40× a chat turn" claim both rest on it.
        """
        from src.domains.study_voice import billing as voice

        per_minute = voice.units_per_minute()
        assert 100 <= per_minute <= 400, (
            f"a voice minute costs {per_minute} units; the plan's arithmetic is ~230 and §6.3 "
            "prices it at 200"
        )

    def test_free_gets_no_voice_at_all(self):
        """§6.3, and this test previously asserted the opposite.

        It used to check that free voice was "about 2.5 minutes a window", derived by dividing the
        free *unit* window by the voice rate. That number was the artefact the plan later called out:
        2.5 minutes per window sounds small, but a 5-hour window permits 4.8 windows a day, so it was
        12 minutes daily — $0.24/day, **$7.20/month at zero revenue**, from a tier whose entire target
        COGS is $0.20. It was an unbounded grant wearing a per-window label.

        Free now gets zero, from its own counter rather than by division, and voice is the one
        capability a free learner is told plainly they do not have.
        """
        from src.domains.billing.services import entitlement_service as ent

        assert ent.VOICE_SECONDS_FREE == 0
        assert ent.FREE_ENTITLEMENT.voice_seconds_included == 0
        assert ent.FREE_ENTITLEMENT.voice_available is False

    def test_voice_is_not_drawn_from_the_unit_window(self):
        """The structural half of the same change, and the reason the note above matters.

        While voice came out of the unit window, every allowance had to be priced for a 40× cost ratio
        it almost never incurred. This asserts the separation rather than the numbers: no voice figure
        may be derivable from `WINDOW_ALLOWANCE_*`, because the moment one is, the two meters are back
        to competing.
        """
        from src.domains.billing.services import entitlement_service as ent

        assert ent.VOICE_SECONDS_PLUS_MONTHLY == 60 * 60
        assert ent.VOICE_SECONDS_PASS_5H == 10 * 60
        assert ent.VOICE_SECONDS_PASS_7D == 25 * 60
        # A pass gets fewer voice minutes than the subscription while having the *same* window
        # allowance as it. That cannot happen if voice is a function of the window.
        assert ent.WINDOW_ALLOWANCE_PASS_7D == ent.WINDOW_ALLOWANCE_PLUS
        assert ent.VOICE_SECONDS_PASS_7D < ent.VOICE_SECONDS_PLUS_MONTHLY

    def test_the_session_floor_is_a_minute_of_voice(self):
        """Shorter and it stops being a floor; longer and it charges for time the learner did not get.

        In seconds now, so it can no longer be invalidated by a price change — which it was, twice.
        """
        from src.domains.study_voice import billing as voice

        assert voice.min_session_seconds() == 60

    def test_plus_buys_meaningfully_more_than_free(self):
        """A paywall has to be worth crossing. 8× is the §6.3 ratio."""
        from src.domains.billing.services import entitlement_service as ent

        assert ent.WINDOW_ALLOWANCE_PLUS >= ent.WINDOW_ALLOWANCE_FREE * 5

    def test_the_monthly_backstop_cannot_bind_before_the_window_does(self):
        """The backstop is an abuse limit, not a product limit (§6.3). If a learner could exhaust the
        month inside a single window it would be the product limit, and a worse one — it resets on the
        first of the month rather than in five hours."""
        from src.domains.billing.services import entitlement_service as ent

        assert ent.MONTHLY_BACKSTOP_FREE > ent.WINDOW_ALLOWANCE_FREE
        assert ent.MONTHLY_BACKSTOP_PLUS > ent.WINDOW_ALLOWANCE_PLUS


class TestNothingTabulatesPricesAnyMore:
    """Decision L: cost is measured, not tabulated. This class replaces the one that guarded the
    last table, and it guards its absence instead.

    The table it tested — `ESTIMATED_OPERATION_UNITS`, three flat figures for the voice session
    note, the note merge and the study diagram — existed only because `llm_resilient` discarded the
    provider response, so those three operations could not see their own token counts. Phase 3b
    plumbed usage through and deleted it, and all three now charge the measured amount at the
    chokepoint.

    **A table is not a neutral thing to leave lying around.** The one before this priced a voice
    minute at 100 units against a real cost of about 11 400 text tokens — under by two orders of
    magnitude, for the life of the product, because nobody re-derived it. The way that does not
    happen again is for there to be nowhere to put such a number.
    """

    def test_the_estimate_table_is_gone(self):
        assert not hasattr(meter, "ESTIMATED_OPERATION_UNITS")
        assert not hasattr(meter, "CREDIT_COSTS")
        assert not hasattr(meter, "CREDIT_LIMITS")
        assert not hasattr(meter, "TOKEN_MULTIPLIER")

    def test_the_only_way_to_price_an_operation_is_from_its_tokens(self):
        """`units_for_tokens` and `units_for_usd` are the whole pricing surface. Both take a
        measurement; neither takes an operation name, so neither can grow a table."""
        import inspect

        assert "input_tokens" in inspect.signature(meter.units_for_tokens).parameters
        assert "cost_usd" in inspect.signature(meter.units_for_usd).parameters

    def test_a_measured_generation_prices_in_the_range_a_real_one_lands_in(self):
        """The sanity bound the old test applied to the table, applied to the measurement instead.

        One model call producing a page or so of text: a few hundred units, not a handful and not
        tens of thousands. Asserted against both tiers because the model is what makes the
        difference, and that ratio is the margin (§6.2).
        """
        free_turn = meter.units_for_tokens(8_000, 600, "gemini-3.1-flash-lite")
        plus_turn = meter.units_for_tokens(8_000, 600, "gemini-3.5-flash")

        assert 10 <= free_turn <= 1_000
        assert 10 <= plus_turn <= 1_000
        assert plus_turn > free_turn, "the dearer model must cost more units for the same tokens"
