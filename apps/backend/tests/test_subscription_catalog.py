"""The product catalogue, stated as tests.

This file existed before and had not run since the SQLAlchemy migration: it imported
`src.schemas.subscription`, `src.services.subscription_service` and
`src.utils.exceptions`, none of which are modules any more, so it failed at collection
rather than failing on an assertion. It asserted a five-product catalogue with yearly Plus
in it and a 7-day trial — every one of which is now wrong — and nothing noticed, because a
collection error in one file is a line of output that looks like the other lines.

What it guards now:

- The catalogue is the only place a price, a trial length or a usage equivalent exists.
  Four repositories held nine copies of the subscription price alone, and they disagreed.
- A withdrawn product answers 410 with a code, not 422 and not 404. A learner holding a
  stale plan id gets told which product went away and what replaced it.
- The two space-scoped entries are untouched. Every commercial change in this phase is
  personal-scope by rule, and the rule is only worth stating if something checks it.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_subscription_catalog.py -v
"""

import os

# Ensure conftest autouse DB fixture does not require DATABASE_URL for this module.
os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.domains.billing import models as billing_models  # noqa: E402
from src.domains.billing.services import entitlement_service  # noqa: E402
from src.domains.billing.services import paystack_service as paystack_svc  # noqa: E402
from src.domains.billing.services import stripe_service as stripe_svc  # noqa: E402
from src.domains.personal_learning.services import trial_service  # noqa: E402
from src.shared.exceptions import DeprecatedPlanError  # noqa: E402

PERSONAL_PRODUCT_IDS = {"free", "plus_pass_5h", "plus_pass_7d", "plus_monthly"}
SPACE_PRODUCT_IDS = {"circle_plan_monthly", "plus_seat_add_on_monthly"}

#: Which window allowance each personal product's voice figure is derived from.
#:
#: Written out here rather than read back from the same helper the catalogue uses, so that a product
#: pointed at the wrong allowance fails rather than agreeing with itself. `plus_monthly` maps to the
#: Plus window and not to the monthly backstop: what a subscriber gets is a Plus-sized window, and the
#: backstop is an abuse limit they are not meant to plan around (§6.3).
_ALLOWANCE_FOR_NOTE = {
    "free": entitlement_service.WINDOW_ALLOWANCE_FREE,
    "plus_pass_5h": entitlement_service.WINDOW_ALLOWANCE_PASS_5H,
    "plus_pass_7d": entitlement_service.WINDOW_ALLOWANCE_PASS_7D,
    "plus_monthly": entitlement_service.WINDOW_ALLOWANCE_PLUS,
}

WITHDRAWN = [
    ("plus_yearly", "PLUS_YEARLY_PLAN_REMOVED"),
    ("maigie_plus_yearly", "PLUS_YEARLY_PLAN_REMOVED"),
    ("study_circle_monthly", "STUDY_CIRCLE_PLAN_REMOVED"),
    ("study_circle_yearly", "STUDY_CIRCLE_PLAN_REMOVED"),
    ("squad_monthly", "SQUAD_PLAN_REMOVED"),
    ("squad_yearly", "SQUAD_PLAN_REMOVED"),
]


def _by_id() -> dict:
    return {p.id: p for p in stripe_svc.get_active_plan_catalog().plans}


# ---------------------------------------------------------------------------
# What the catalogue contains
# ---------------------------------------------------------------------------


class TestCatalogueContents:
    def test_there_are_exactly_four_personal_products(self):
        plans = stripe_svc.get_active_plan_catalog().plans
        personal = {p.id for p in plans if p.scope == "personal"}
        assert personal == PERSONAL_PRODUCT_IDS

    def test_the_space_scoped_entries_are_untouched(self):
        """Scope guard. Every change in this phase is personal-scope by rule.

        Circle Plan and the Plus Seat add-on keep their ids, their scopes, their prices
        and their 7-day trial. If a commercial change ever reaches them, it reaches them
        here first.
        """
        cfg = get_settings()
        by_id = _by_id()
        assert {p.id for p in by_id.values() if p.scope != "personal"} == SPACE_PRODUCT_IDS

        circle = by_id["circle_plan_monthly"]
        assert circle.scope == "circle"
        assert circle.price_cents == cfg.PRICE_CENTS_CIRCLE_PLAN_MONTHLY
        assert circle.trial_days == cfg.TRIAL_DAYS_CIRCLE_PLAN == 7

        seat = by_id["plus_seat_add_on_monthly"]
        assert seat.scope == "add_on"
        assert seat.price_cents == cfg.PRICE_CENTS_PLUS_SEAT_ADD_ON_MONTHLY

    def test_withdrawn_products_are_absent(self):
        ids = set(_by_id())
        for plan_id, _code in WITHDRAWN:
            assert plan_id not in ids
        # Credit packs went with the unit they sold.
        assert not any(pid.startswith("credit_pack") for pid in ids)

    def test_no_entry_claims_unlimited(self):
        """Plus was never unlimited. It was 300 000 credits a month, and it is now a
        window allowance. The word cannot appear in copy the server owns.
        """
        for plan in _by_id().values():
            haystack = f"{plan.description} {plan.usage_note or ''}".lower()
            assert "unlimited" not in haystack


class TestCataloguePrices:
    def test_the_three_paid_prices(self):
        cfg = get_settings()
        by_id = _by_id()
        assert by_id["plus_pass_5h"].price_cents == cfg.PRICE_CENTS_PLUS_PASS_5H == 99
        assert by_id["plus_pass_7d"].price_cents == cfg.PRICE_CENTS_PLUS_PASS_7D == 249
        assert by_id["plus_monthly"].price_cents == cfg.PRICE_CENTS_PLUS_MONTHLY == 499
        assert by_id["free"].price_cents == 0

    def test_the_subscription_price_has_not_moved(self):
        """$4.99, not $5.00, and pinned here on purpose.

        A one-cent rise costs a Stripe price migration, a mandatory 7-day Google Play
        notice, and on Apple an explicit consent prompt whose non-responders are cancelled
        at renewal. Real churn for a rounding difference. Anyone who changes this number
        should have to change this test and read this comment.
        """
        assert get_settings().PRICE_CENTS_PLUS_MONTHLY == 499

    def test_there_is_no_pass_and_subscription_arbitrage(self):
        """The per-day ladder has to run one way: impulse dearest, subscription cheapest.

        If a pass were ever cheaper per day than the subscription, the subscription would
        be the wrong choice for everyone and the cheap product would cannibalise the one
        that funds the model.
        """
        by_id = _by_id()
        per_day_5h = by_id["plus_pass_5h"].price_cents / (5 / 24)
        per_day_7d = by_id["plus_pass_7d"].price_cents / 7
        per_day_month = by_id["plus_monthly"].price_cents / 30
        assert per_day_5h > per_day_7d > per_day_month


class TestCatalogueTrial:
    def test_the_trial_is_three_days_and_only_on_the_subscription(self):
        cfg = get_settings()
        by_id = _by_id()
        assert cfg.TRIAL_DAYS_MAIGIE_PLUS == 3
        assert by_id["plus_monthly"].trial_days == 3
        # A pass is bought and spent. There is nothing to trial and nothing to renew.
        assert by_id["plus_pass_5h"].trial_days == 0
        assert by_id["plus_pass_7d"].trial_days == 0
        assert by_id["free"].trial_days == 0

    def test_the_two_copies_of_the_trial_length_agree(self):
        """`config.TRIAL_DAYS_MAIGIE_PLUS` governs the Stripe subscription's trial period;
        `trial_service.TRIAL_DURATION_DAYS` governs the in-product trial. They are two
        numbers describing one promise, and a learner who saw them disagree would be right
        to distrust whichever one turned out to be shorter.

        Two further copies live in the App Store Connect and Play Console UIs and cannot be
        asserted from here. They are on the store checklist, and this test is the reason
        the checklist only has two items on it rather than four.
        """
        assert trial_service.TRIAL_DURATION_DAYS == get_settings().TRIAL_DAYS_MAIGIE_PLUS

    def test_the_trial_is_shorter_than_the_seven_day_pass(self):
        """The whole reason the trial moved from 7 days to 3. A free 7-day trial beside a
        paid 7-day pass is one product at two prices, and the paid one looks like a trick.
        """
        assert trial_service.TRIAL_DURATION_DAYS < 7


class TestUsageEquivalents:
    def test_every_personal_product_states_what_it_buys(self):
        by_id = _by_id()
        for plan_id in PERSONAL_PRODUCT_IDS:
            assert by_id[plan_id].usage_note, f"{plan_id} must state its usage equivalent"

    @pytest.mark.parametrize("plan_id", sorted(PERSONAL_PRODUCT_IDS))
    def test_voice_is_always_named_as_bounded(self, plan_id):
        """ "5 hours of Plus" invites the reader to assume five hours of live voice tutoring.
        Five hours of tutoring costs about $6.00 to serve against a pass that nets $0.75, so
        every note has to say that voice is bounded rather than included without limit.

        The check used to look for the word "allowance" or "taster", which was the best available
        signal while the notes carried no figures. Now that each states a minute count derived from
        the window that funds it, the figure *is* the bound and it is a stronger one than the word:
        "about 20 minutes" cannot be read as unlimited, whereas "an allowance of live voice" leaves a
        reader guessing at the size. So the assertion moves to the figure.
        """
        note = _by_id()[plan_id].usage_note.lower()
        assert "voice" in note
        assert "minutes of live voice" in note, (
            f"{plan_id} names voice without bounding it, which is how a taster reads as a service: "
            f"{note!r}"
        )

    @pytest.mark.parametrize("plan_id", sorted(PERSONAL_PRODUCT_IDS))
    def test_every_voice_figure_follows_from_the_allowance_that_funds_it(self, plan_id):
        """This replaces `test_no_note_promises_a_figure_the_meter_cannot_honour`, and the swap is the
        point of Phase 3.

        That test forbade any consumption count in the copy, because the notes had shipped carrying the
        §6.3 window figures before the window existed — for `plus_monthly`, a sentence about 19× more
        generous than the meter then running. It said in its own docstring that Phase 3 should delete it
        once the figures were true. They are now: the window exists, `Entitlement.window_allowance` is
        the allowance, and `GET /billing/usage` reports against it.

        So the guard moves from "state no number" to "state a number that follows". Each note's voice
        figure is recomputed here from the allowance and the configured rate, which is the only reason
        it is safe to put a number in front of a customer: if either moves, this fails rather than the
        copy quietly becoming false.
        """
        note = _by_id()[plan_id].usage_note
        assert stripe_svc._voice_minutes_note(_ALLOWANCE_FOR_NOTE[plan_id]) in note

    @pytest.mark.parametrize("plan_id", sorted(PERSONAL_PRODUCT_IDS))
    def test_no_note_quotes_a_chat_turn_count(self, plan_id):
        """Voice carries a figure; chat deliberately does not.

        Not for want of an allowance to quote: a free window funds about 12 chat turns on Flash-Lite
        and a Plus window about 22 on Flash, because Plus buys a dearer model as well as a larger
        allowance. Printing both would invite a comparison that understates Plus by most of what it
        sells, and there is no honest way to answer it in one sentence. Open Question 4.
        """
        note = _by_id()[plan_id].usage_note.lower()
        for unit in ("turn", "message", "credit", "token"):
            assert unit not in note, f"{plan_id} quotes a {unit} count: {note!r}"

    def test_a_voice_figure_is_rounded_down_rather_than_up(self):
        """A learner will test this with a stopwatch, so it has to be a floor.

        2.5 minutes rendering as "about 3" would overstate the free allowance by 20%, which is exactly
        the size of error that turns a reasonable limit into a complaint.
        """
        assert "2.5" in stripe_svc._voice_minutes_note(500)
        assert stripe_svc._voice_minutes_note(3_580) == stripe_svc._voice_minutes_note(3_400)


# ---------------------------------------------------------------------------
# Withdrawn products answer 410, on both rails
# ---------------------------------------------------------------------------


class TestWithdrawnProductsAreRefusedSpecifically:
    @pytest.mark.parametrize(("plan_id", "expected_code"), WITHDRAWN)
    def test_stripe_plan_id_is_rejected_with_410(self, plan_id, expected_code):
        with pytest.raises(DeprecatedPlanError) as excinfo:
            stripe_svc.assert_plan_id_is_active(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code

    @pytest.mark.parametrize(("plan_id", "expected_code"), WITHDRAWN)
    def test_stripe_checkout_lookup_is_rejected_with_410(self, plan_id, expected_code):
        with pytest.raises(DeprecatedPlanError) as excinfo:
            stripe_svc.get_price_id_and_trial_days(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code

    @pytest.mark.parametrize(("plan_id", "expected_code"), WITHDRAWN)
    def test_paystack_reads_the_same_withdrawal_list(self, plan_id, expected_code):
        """The two rails share one dict. They used to hold a copy each, which is how a
        product withdrawn on Stripe could remain on sale in naira.
        """
        with pytest.raises(DeprecatedPlanError) as excinfo:
            paystack_svc._get_plan_code(plan_id)
        assert excinfo.value.status_code == 410
        assert excinfo.value.code == expected_code

    def test_the_two_rails_refuse_the_same_set(self):
        assert paystack_svc.DEPRECATED_PLAN_IDS is stripe_svc.DEPRECATED_PLAN_IDS

    def test_switching_an_existing_subscription_to_yearly_is_refused(self):
        """`assert_plan_id_is_active` guards a fresh checkout, which arrives as a plan id.
        This guards `modify_existing_subscription`, which arrives as a *price* id — a
        monthly subscriber switching to yearly would otherwise buy a withdrawn product
        through a door the plan-id check does not watch.
        """
        cfg = get_settings()
        cfg.STRIPE_PRICE_ID_YEARLY = "price_yearly_test"
        try:
            with pytest.raises(DeprecatedPlanError) as excinfo:
                stripe_svc._assert_price_id_is_active("price_yearly_test")
            assert excinfo.value.status_code == 410
            assert excinfo.value.code == "PLUS_YEARLY_PLAN_REMOVED"
        finally:
            cfg.STRIPE_PRICE_ID_YEARLY = ""

    def test_a_withdrawn_price_no_longer_writes_a_tier_nothing_grants(self):
        """A writer must not produce a tier the resolver denies.

        This assertion has been both ways round, and the middle position was the broken one. It
        first required `PREMIUM_YEARLY`, on the reasoning that withdrawing a product must not orphan
        the people paying for it — correct, and it was the finding that made Phase 2a restore
        `LEGACY_PLUS_TIERS`, because the resolver had narrowed while this had not.

        Phase 2b resolved it in the other direction on a measurement rather than an argument:
        `scripts/count_legacy_commercial_state.py` found zero users on any retired tier and zero
        subscription identifiers of any kind. There is nobody to orphan, all five products are
        withdrawn from sale so no renewal can arrive, and a paid price that maps to a tier
        `entitlement_service.PLUS_TIERS` refuses would bill a learner for nothing.

        If that count is ever non-zero, the fix is to restore the frozenset *and* this mapping
        together — they were only ever wrong apart.
        """
        cfg = get_settings()
        cfg.STRIPE_PRICE_ID_YEARLY = "price_yearly_test"
        try:
            assert stripe_svc._price_id_to_tier("price_yearly_test") == "FREE"
        finally:
            cfg.STRIPE_PRICE_ID_YEARLY = ""

    def test_the_active_monthly_price_is_the_only_one_that_grants_plus(self):
        cfg = get_settings()
        cfg.STRIPE_PRICE_ID_MONTHLY = "price_monthly_test"
        try:
            assert stripe_svc._price_id_to_tier("price_monthly_test") == "PREMIUM_MONTHLY"
        finally:
            cfg.STRIPE_PRICE_ID_MONTHLY = ""

    def test_every_tier_a_writer_can_produce_is_one_the_resolver_grants(self):
        """The gap the grandfathering defect lived in, closed by an assertion.

        Nothing previously checked that the tier strings `_price_id_to_tier` and
        `_plan_code_to_tier` can emit are a subset of what `entitlement_service` treats as Plus, so
        the two drifted silently and both sides' tests stayed green.
        """
        from src.domains.billing.services import entitlement_service

        cfg = get_settings()
        cfg.STRIPE_PRICE_ID_MONTHLY = "price_m"
        cfg.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY = "PLN_m"
        try:
            produced = {
                stripe_svc._price_id_to_tier("price_m"),
                stripe_svc._price_id_to_tier("price_unknown"),
                paystack_svc._plan_code_to_tier("PLN_m"),
                paystack_svc._plan_code_to_tier("PLN_unknown"),
            }
        finally:
            cfg.STRIPE_PRICE_ID_MONTHLY = ""
            cfg.PAYSTACK_PLAN_MAIGIE_PLUS_MONTHLY = ""

        assert produced - {"FREE"} <= entitlement_service.PLUS_TIERS

    def test_active_plan_ids_are_accepted(self):
        for plan_id in (
            "maigie_plus_monthly",
            "plus_monthly",
            "circle_plan_monthly",
            "plus_seat_add_on_monthly",
        ):
            stripe_svc.assert_plan_id_is_active(plan_id)  # must not raise


# ---------------------------------------------------------------------------
# A pass is not a subscription
# ---------------------------------------------------------------------------


class TestPassesAreNotSubscriptions:
    @pytest.mark.parametrize("plan_id", stripe_svc.PASS_PRODUCT_IDS)
    def test_a_pass_is_refused_at_the_subscription_checkout(self, plan_id):
        """Not a 410 — a pass has not been withdrawn, it is on sale. It is simply not a
        subscription, and the refusal says so rather than saying "invalid plan_id".
        """
        with pytest.raises(ValueError, match="one-time Plus pass"):
            stripe_svc.get_price_id_and_trial_days(plan_id)

    @pytest.mark.parametrize("plan_id", stripe_svc.PASS_PRODUCT_IDS)
    def test_a_pass_is_absent_from_the_subscription_plan_ids(self, plan_id):
        assert plan_id not in stripe_svc.PLAN_IDS

    @pytest.mark.parametrize("plan_id", stripe_svc.PASS_PRODUCT_IDS)
    def test_a_pass_is_in_the_catalogue_as_a_one_time_product(self, plan_id):
        """Absent from checkout, present in the catalogue. A client has to be able to price
        and display a pass before there is a rail to buy it on.
        """
        assert _by_id()[plan_id].interval == "one_time"

    @pytest.mark.parametrize("plan_id", stripe_svc.PASS_PRODUCT_IDS)
    def test_the_checkout_schema_accepts_a_pass_so_the_refusal_is_reachable(self, plan_id):
        """The refusal above is only useful if a request carrying a pass id reaches the handler.

        `CheckoutRequest.plan_id` is a `Literal`, and while the pass ids were omitted from it
        FastAPI answered `422 not a valid plan` before `get_price_id_and_trial_days` ran — so the
        carefully worded "use the pass checkout" message was unreachable and the test above passed
        while the route behaved differently. This is the same argument the comment above `PlanId`
        makes for keeping the six *withdrawn* ids in the Literal, applied to the two passes.
        """
        assert billing_models.CheckoutRequest(plan_id=plan_id).plan_id == plan_id

    @pytest.mark.parametrize("plan_id", stripe_svc.PASS_PRODUCT_IDS)
    def test_a_pass_is_not_advertised_as_purchasable_yet(self, plan_id):
        """Listed, described, priced — and not offered, until Phase 5 builds the one-time rail.

        Without this a client had no way to tell, and the honest reading of a catalogue entry is
        "you can buy this", so it would have rendered a Buy button that answers 400.
        """
        assert _by_id()[plan_id].purchasable is False

    def test_the_subscription_is_purchasable(self):
        """The flag has to distinguish, or it is decoration."""
        assert _by_id()["plus_monthly"].purchasable is True


# ---------------------------------------------------------------------------
# The trial is granted once
# ---------------------------------------------------------------------------


def _fake_user(*, tier="FREE", stripe_sub=None, paystack_sub=None):
    """Minimal user-shaped object. Attribute names are the SQLAlchemy model's snake_case
    ones, which is what `_is_first_plus_purchase` reads; the previous version of this file
    used camelCase and so exercised nothing.
    """
    return SimpleNamespace(
        id="user-test",
        tier=tier,
        stripe_subscription_id=stripe_sub,
        paystack_subscription_code=paystack_sub,
    )


class TestTrialIsGrantedOnFirstPurchaseOnly:
    def test_a_first_time_subscriber_gets_the_trial(self):
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_monthly", user=_fake_user())
        assert trial_days == 3

    def test_a_returning_paid_subscriber_does_not(self):
        user = _fake_user(tier="PREMIUM_MONTHLY", stripe_sub="sub_existing")
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_monthly", user=user)
        assert trial_days == 0

    def test_a_former_paystack_subscriber_does_not(self):
        user = _fake_user(paystack_sub="SUB_existing")
        _, trial_days = stripe_svc.get_price_id_and_trial_days("plus_monthly", user=user)
        assert trial_days == 0

    def test_circle_plan_emits_no_trial_at_the_personal_surface(self):
        """The Circle Plan trial is owned by the Circle billing service. The personal
        checkout must not hand out a second one.
        """
        _, trial_days = stripe_svc.get_price_id_and_trial_days(
            "circle_plan_monthly", user=_fake_user()
        )
        assert trial_days == 0
