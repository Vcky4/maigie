"""Live voice has its own balance, and the balance expires without a sweep.

MAIGIE_PLUS_COMMERCIAL_PLAN.md §6.3. Voice was drawn from the 5-hour usage window, where at 200
units/minute it cost 40× a Flash-Lite chat turn — so one allowance had to be priced against the voice
case and was spent almost entirely on the text case. Unbundling it is what makes the NGN ladder
affordable and what turns "about 15 minutes" from an allowance-division artefact into a promise a
counter keeps.

Two properties carry most of the weight here, and both are about *not* losing something.

**A pass's minutes must not outlive the pass.** The plan called for a sweep job to zero a stale
balance; this re-derives on read instead, so there is no interval between a pass ending and a sweep
noticing, and no job that can fail silently. The tests for it are the ones that change the source and
assert the balance follows.

**Purchased minutes must survive everything.** A learner who paid $1.49 for 30 minutes owns them
across a renewal, a lapse and a tier change. That is the one quantity here that a bug could destroy
irrecoverably, so it is asserted from several directions.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_voice_balance.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.domains.billing.services import entitlement_service as ent  # noqa: E402
from src.domains.billing.services import voice_service  # noqa: E402
from src.domains.study_voice import routes as voice_routes  # noqa: E402

LATER = datetime.now(UTC) + timedelta(days=10)
TRIAL_ENDS = datetime.now(UTC) + timedelta(days=2)


def a_user(*, remaining=0, purchased=0, source=None):
    """A `User`-shaped stand-in carrying only the three voice columns.

    A real `User` needs a session to build and thirty other columns to be meaningful; `resolve` reads
    exactly three attributes and is pure, which is the reason it is a separate function from
    `read_balance` in the first place.
    """
    return SimpleNamespace(
        voice_seconds_remaining=remaining,
        voice_seconds_purchased=purchased,
        voice_allowance_source_id=source,
    )


def subscription(period_end=LATER):
    return ent._compose(
        subscription_tier="PREMIUM_MONTHLY",
        subscription_period_end=period_end,
        active_pass=None,
        active_trial=None,
    )


def a_pass(pass_id="pass_1", product_id="plus_pass_5h"):
    return ent._compose(
        subscription_tier="FREE",
        subscription_period_end=None,
        active_pass=ent.ActivePass(pass_id=pass_id, product_id=product_id, expires_at=LATER),
        active_trial=None,
    )


# ---------------------------------------------------------------------------
# What each entitlement grants
# ---------------------------------------------------------------------------


class TestTheAllowances:
    def test_free_gets_none_and_it_is_a_capability_not_an_empty_counter(self):
        """The distinction the whole of §6.3's free-tier argument rests on.

        `available is False` is not `total_seconds == 0`; it is "this learner does not have voice". A
        subscriber at zero needs the top-up, a free learner needs telling that voice is part of Plus,
        and the same empty counter has to produce two different screens.
        """
        assert ent.FREE_ENTITLEMENT.voice_seconds_included == 0
        assert ent.FREE_ENTITLEMENT.voice_available is False
        balance = voice_service.resolve(a_user(), ent.FREE_ENTITLEMENT)
        assert balance.available is False
        assert balance.total_seconds == 0

    def test_a_subscriber_gets_an_hour(self):
        assert subscription().voice_seconds_included == 60 * 60

    def test_a_trial_gets_the_same_as_a_subscription(self):
        """A trial that withholds the one capability Free is missing is not a trial of Plus.

        It is worth naming the cost rather than passing over it: 60 minutes is $1.20 of inference given
        to someone who has paid nothing. The bound is the 3-day trial length, not a smaller allowance.
        """
        trial = ent._compose(
            subscription_tier="FREE",
            subscription_period_end=None,
            active_pass=None,
            active_trial=ent.ActiveTrial(ends_at=TRIAL_ENDS, days_remaining=2),
        )
        assert trial.voice_seconds_included == ent.VOICE_SECONDS_PLUS_MONTHLY

    @pytest.mark.parametrize(
        ("product_id", "expected_minutes"),
        [("plus_pass_5h", 10), ("plus_pass_7d", 25), ("plus_pass_term", 60)],
    )
    def test_each_pass_grants_its_own_figure(self, product_id, expected_minutes):
        assert a_pass(product_id=product_id).voice_seconds_included == expected_minutes * 60

    def test_an_unknown_pass_product_falls_to_the_smallest_grant(self):
        """Under-granting is a support ticket; over-granting is COGS on the most expensive operation
        in the product. Same defensive direction as the window allowance.
        """
        assert a_pass(product_id="plus_pass_invented").voice_seconds_included == (
            ent.VOICE_SECONDS_PASS_5H
        )

    def test_voice_is_not_a_function_of_the_window_allowance(self):
        """The structural claim, and the reason the two meters can be priced independently.

        A 7-day pass has the *same* window allowance as the subscription and fewer voice minutes. That
        is impossible if voice is derived from the window, which is how it worked before §6.3.
        """
        assert ent.WINDOW_ALLOWANCE_PASS_7D == ent.WINDOW_ALLOWANCE_PLUS
        assert ent.VOICE_SECONDS_PASS_7D < ent.VOICE_SECONDS_PLUS_MONTHLY


# ---------------------------------------------------------------------------
# Granting, and expiring without a sweep
# ---------------------------------------------------------------------------


class TestTheGrantIsDerivedOnRead:
    def test_a_first_read_grants_the_full_allowance(self):
        balance = voice_service.resolve(a_user(), subscription())
        assert balance.granted_seconds == 60 * 60
        assert balance.refreshed is True, "the stored row disagrees, so a write is owed"

    def test_a_second_read_in_the_same_period_grants_nothing_more(self):
        """Idempotence is what makes this safe to call from a pollable endpoint.

        Without it, `GET /billing/voice/balance` would refill the balance on every poll, which is an
        unlimited voice allowance reachable by pressing refresh.
        """
        ent_sub = subscription()
        user = a_user(remaining=1_800, source=ent_sub.voice_allowance_source_id)
        balance = voice_service.resolve(user, ent_sub)
        assert balance.granted_seconds == 1_800
        assert balance.refreshed is False

    def test_a_renewal_re_grants(self):
        """The source id carries the period end, so a new period is a new source."""
        old = subscription(period_end=datetime.now(UTC) + timedelta(days=1))
        new = subscription(period_end=datetime.now(UTC) + timedelta(days=31))
        assert old.voice_allowance_source_id != new.voice_allowance_source_id

        spent_out = a_user(remaining=0, source=old.voice_allowance_source_id)
        assert voice_service.resolve(spent_out, new).granted_seconds == 60 * 60

    def test_a_renewal_resets_rather_than_accumulating(self):
        """60 minutes a month that rolled over would be an unbounded balance for a dormant
        subscriber, and a bill that arrives the month they come back.
        """
        old = subscription(period_end=datetime.now(UTC) + timedelta(days=1))
        new = subscription(period_end=datetime.now(UTC) + timedelta(days=31))
        unused = a_user(remaining=60 * 60, source=old.voice_allowance_source_id)
        assert voice_service.resolve(unused, new).granted_seconds == 60 * 60

    def test_a_passs_minutes_do_not_outlive_the_pass(self):
        """The failure the plan's sweep job existed to prevent, prevented without one.

        A learner holding 8 unspent minutes from a pass that has ended reads as Free on the next
        resolve, so the granted balance is gone at the moment the entitlement changes — not whenever a
        nightly job next runs.
        """
        held = a_user(remaining=8 * 60, source="pass:pass_1")
        after = voice_service.resolve(held, ent.FREE_ENTITLEMENT)
        assert after.granted_seconds == 0
        assert after.refreshed is True, "the stale balance has to be written away, not just ignored"

    def test_a_second_pass_is_a_new_grant(self):
        first = a_pass(pass_id="pass_1")
        second = a_pass(pass_id="pass_2")
        assert first.voice_allowance_source_id != second.voice_allowance_source_id
        spent = a_user(remaining=0, source=first.voice_allowance_source_id)
        assert voice_service.resolve(spent, second).granted_seconds == ent.VOICE_SECONDS_PASS_5H

    def test_a_hand_set_tier_with_no_period_grants_once(self):
        """Production holds exactly one `PREMIUM_MONTHLY` row with no subscription behind it, so this
        is a real case rather than a hypothetical: a null period end produces a stable source id, so
        the grant happens once and does not renew. That is the right answer for a tier nobody is
        billing for — and it is worth asserting, because the alternative reading of a null (a new
        source every read) would be an unlimited allowance.
        """
        hand_set = subscription(period_end=None)
        granted = a_user(remaining=0, source=hand_set.voice_allowance_source_id)
        assert voice_service.resolve(granted, hand_set).refreshed is False


# ---------------------------------------------------------------------------
# Purchased minutes
# ---------------------------------------------------------------------------


class TestPurchasedMinutesSurvive:
    def test_a_renewal_does_not_touch_them(self):
        old = subscription(period_end=datetime.now(UTC) + timedelta(days=1))
        new = subscription(period_end=datetime.now(UTC) + timedelta(days=31))
        user = a_user(remaining=0, purchased=30 * 60, source=old.voice_allowance_source_id)
        after = voice_service.resolve(user, new)
        assert after.purchased_seconds == 30 * 60
        assert after.granted_seconds == 60 * 60

    def test_they_survive_the_plan_lapsing_entirely(self):
        """The case with money attached. A learner who bought 30 minutes and then let their
        subscription lapse still owns those minutes; confiscating them because they stopped paying for
        something else would be taking a product back after selling it.
        """
        user = a_user(remaining=45 * 60, purchased=30 * 60, source="subscription:whenever")
        after = voice_service.resolve(user, ent.FREE_ENTITLEMENT)
        assert after.granted_seconds == 0, "the granted half expires with the plan"
        assert after.purchased_seconds == 30 * 60, "the bought half does not"
        assert after.available is True, "and it is still usable, or it was not really sold"

    def test_a_pass_holders_purchase_survives_the_pass(self):
        user = a_user(remaining=10 * 60, purchased=30 * 60, source="pass:pass_1")
        assert voice_service.resolve(user, ent.FREE_ENTITLEMENT).purchased_seconds == 30 * 60


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


class TestWhatALearnerSees:
    def test_minutes_round_down(self):
        """A learner told "1 minute left" who gets 50 seconds has been misled. One told "0" who gets
        50 seconds has been under-promised, which is the safe direction for a figure they will test.
        """
        balance = voice_service.resolve(
            a_user(remaining=119, source=subscription().voice_allowance_source_id),
            subscription(),
        )
        assert balance.total_seconds == 119
        assert balance.total_minutes == 1

    def test_the_total_adds_both_balances(self):
        ent_sub = subscription()
        balance = voice_service.resolve(
            a_user(remaining=600, purchased=300, source=ent_sub.voice_allowance_source_id),
            ent_sub,
        )
        assert balance.total_seconds == 900
        assert balance.total_minutes == 15


# ---------------------------------------------------------------------------
# Spending
# ---------------------------------------------------------------------------


class TestSpending:
    """`spend` writes, so these stub the repository rather than the row.

    What is worth asserting is the *order* and the *floor*, not the SQL: granted seconds go first
    because they are the ones that expire, and the balance floors at zero rather than going negative.
    """

    @staticmethod
    def _wire(monkeypatch, user, entitlement):
        written: dict = {}

        class FakeRepo:
            async def find_by_id(self, _user_id):
                return user

            async def update(self, _user_id, values):
                written.update(values)

        async def fake_resolve(_user_id):
            return entitlement

        monkeypatch.setattr(voice_service, "IdentityRepository", FakeRepo)
        monkeypatch.setattr(voice_service.entitlement_service, "resolve", fake_resolve)
        return written

    @pytest.mark.asyncio
    async def test_granted_seconds_are_spent_before_purchased_ones(self, monkeypatch):
        """The only order that does not quietly destroy something the learner paid for.

        Spending purchased minutes first would mean a subscriber's bought 30 minutes were consumed
        while their included 60 sat unused and then expired at the renewal — the learner would have
        paid $1.49 to lose 30 minutes.
        """
        ent_sub = subscription()
        user = a_user(remaining=600, purchased=1_800, source=ent_sub.voice_allowance_source_id)
        written = self._wire(monkeypatch, user, ent_sub)

        after = await voice_service.spend("u1", 300)
        assert after.granted_seconds == 300
        assert after.purchased_seconds == 1_800
        assert written["voiceSecondsRemaining"] == 300
        assert written["voiceSecondsPurchased"] == 1_800

    @pytest.mark.asyncio
    async def test_spending_past_the_granted_balance_reaches_the_purchased_one(self, monkeypatch):
        ent_sub = subscription()
        user = a_user(remaining=120, purchased=600, source=ent_sub.voice_allowance_source_id)
        self._wire(monkeypatch, user, ent_sub)

        after = await voice_service.spend("u1", 300)
        assert after.granted_seconds == 0
        assert after.purchased_seconds == 420

    @pytest.mark.asyncio
    async def test_the_balance_floors_at_zero_rather_than_going_negative(self, monkeypatch):
        """A session can overrun by up to one flush interval, exactly as a usage window can be
        exceeded by one operation in flight. The overshoot is logged and absorbed — a negative balance
        would be silently forgiven by the next grant, which is a worse kind of wrong.
        """
        ent_sub = subscription()
        user = a_user(remaining=30, source=ent_sub.voice_allowance_source_id)
        written = self._wire(monkeypatch, user, ent_sub)

        after = await voice_service.spend("u1", 600)
        assert after.total_seconds == 0
        assert written["voiceSecondsRemaining"] == 0

    @pytest.mark.asyncio
    async def test_a_top_up_lands_in_the_balance_that_does_not_expire(self, monkeypatch):
        """The bug this test exists to prevent is silent and expensive: a top-up written to
        `voiceSecondsRemaining` would look correct until the next renewal deleted it.
        """
        ent_sub = subscription()
        user = a_user(remaining=600, source=ent_sub.voice_allowance_source_id)
        written = self._wire(monkeypatch, user, ent_sub)

        after = await voice_service.add_purchased("u1", ent.VOICE_SECONDS_TOP_UP)
        assert after.purchased_seconds == 30 * 60
        assert after.granted_seconds == 600
        assert written["voiceSecondsPurchased"] == 30 * 60

    @pytest.mark.asyncio
    async def test_a_top_up_gives_voice_back_to_a_learner_who_had_none(self, monkeypatch):
        """Decision R's whole case for this product existing. A subscriber out of minutes cannot
        activate a pass — Decision D refuses activation while Plus is active — so the top-up is the
        only thing in the catalogue that answers them.
        """
        written = self._wire(monkeypatch, a_user(), ent.FREE_ENTITLEMENT)
        after = await voice_service.add_purchased("u1", ent.VOICE_SECONDS_TOP_UP)
        assert after.available is True
        assert written["voiceSecondsPurchased"] == 30 * 60

    @pytest.mark.asyncio
    async def test_top_ups_accumulate(self, monkeypatch):
        """Buying the pack twice gives 60 minutes. Additive on purpose: a learner may buy it
        repeatedly and that is the product working. Not fulfilling the same *purchase* twice is a
        property of the purchase record, which Phase 5 owns.
        """
        ent_sub = subscription()
        user = a_user(purchased=1_800, source=ent_sub.voice_allowance_source_id)
        self._wire(monkeypatch, user, ent_sub)
        after = await voice_service.add_purchased("u1", ent.VOICE_SECONDS_TOP_UP)
        assert after.purchased_seconds == 60 * 60

    @pytest.mark.asyncio
    async def test_spending_nothing_writes_nothing(self, monkeypatch):
        ent_sub = subscription()
        user = a_user(remaining=600, source=ent_sub.voice_allowance_source_id)
        written = self._wire(monkeypatch, user, ent_sub)
        await voice_service.spend("u1", 0)
        assert "voiceSecondsRemaining" not in written

    @pytest.mark.asyncio
    async def test_a_persist_failure_never_reaches_the_caller(self, monkeypatch):
        """Same posture as `record_units`. Every failure here — a lost connection, a vanished row, a
        serialisation conflict — is a reason to under-charge, not a reason to interrupt a learner
        mid-conversation. `bridge.charge` would otherwise treat it as a reason to end the session.
        """
        ent_sub = subscription()
        user = a_user(remaining=600, source=ent_sub.voice_allowance_source_id)

        class BrokenRepo:
            async def find_by_id(self, _user_id):
                return user

            async def update(self, _user_id, _values):
                raise RuntimeError("no database")

        async def fake_resolve(_user_id):
            return ent_sub

        monkeypatch.setattr(voice_service, "IdentityRepository", BrokenRepo)
        monkeypatch.setattr(voice_service.entitlement_service, "resolve", fake_resolve)

        after = await voice_service.spend("u1", 60)
        assert after.granted_seconds == 540


class TestTheExhaustedMessageTracksWhatIsActuallyOnSale:
    """The refusal copy is derived from the catalogue, not written down.

    It shipped hardcoded as "Add 30 minutes to carry on" while `plus_voice_30` was absent from the
    catalogue, unpriced and unbuyable — so the one learner who exhausted their minutes was pointed
    at a product that did not exist. §5.4 is a list of copy that outran the code and this was the
    newest entry.

    Both directions are pinned, because the reverse drift is just as likely: Phase 5 ships the rail
    and nobody remembers to restore the offer that was supposed to sell it.
    """

    def test_it_names_the_refill_while_the_top_up_cannot_be_bought(self):
        message = voice_routes.voice_exhausted_message()

        assert "refill" in message
        assert (
            "Add 30 minutes" not in message
        ), "a learner cannot buy a top-up yet, so offering one is a dead end"

    def test_it_offers_the_top_up_once_the_top_up_is_purchasable(self, monkeypatch):
        purchasable_top_up = SimpleNamespace(id=voice_routes._VOICE_TOP_UP_ID, purchasable=True)
        monkeypatch.setattr(
            "src.domains.billing.services.stripe_service.get_active_plan_catalog",
            lambda: SimpleNamespace(plans=[purchasable_top_up]),
        )

        assert "Add 30 minutes" in voice_routes.voice_exhausted_message()

    def test_a_listed_but_unpurchasable_top_up_is_not_offered(self, monkeypatch):
        """`purchasable=False` is how the catalogue lists a product whose rail is not built —
        both passes sit in that state right now. Listing is not selling."""
        listed_only = SimpleNamespace(id=voice_routes._VOICE_TOP_UP_ID, purchasable=False)
        monkeypatch.setattr(
            "src.domains.billing.services.stripe_service.get_active_plan_catalog",
            lambda: SimpleNamespace(plans=[listed_only]),
        )

        assert "Add 30 minutes" not in voice_routes.voice_exhausted_message()

    def test_an_unreadable_catalogue_still_says_something_true(self, monkeypatch):
        """A refusal that cannot read the catalogue must not become a stack trace, and "it refills"
        is true whatever is on sale."""

        def exploding():
            raise RuntimeError("catalogue unavailable")

        monkeypatch.setattr(
            "src.domains.billing.services.stripe_service.get_active_plan_catalog",
            exploding,
        )

        assert "refill" in voice_routes.voice_exhausted_message()
