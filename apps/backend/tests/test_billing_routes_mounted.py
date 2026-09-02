"""The billing router is mounted, and the endpoints that cannot work are not.

For as long as `app.py` kept the billing router commented out, the production state was:
the meter runs and there is no way to pay it. `credit_consumption_service` is imported
directly by `study_voice`, `personal_learning` and `knowledge`, so allowances were being
enforced, while every checkout, verification and webhook endpoint that could have sold more
was served by nothing. Both clients called those paths and got a 404. No trial has ever
converted to a paying subscriber, because there has never been a checkout to convert into.

This asserts the routing table rather than mocking a provider, because the defect was never
in the handlers — they were written, complete and unreachable. A test that called
`create_checkout_session` with a fake Stripe would have passed throughout.

It also pins the three absences. Each is deliberate and each has a different reason, and an
absence with a reason is only distinguishable from an oversight if something records it.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_billing_routes_mounted.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest  # noqa: E402

from src.app import create_app  # noqa: E402

PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def paths() -> set[str]:
    return {route.path for route in create_app().routes}


REACHABLE = [
    # The catalogue. Unauthenticated: a price is not private, and the pricing page needs it.
    f"{PREFIX}/billing/plans/catalog",
    # The one resolver, served. Clients read this instead of inferring entitlement from
    # `User.tier`, which is what every hardcoded `tierLabel` in the web app does today.
    f"{PREFIX}/billing/entitlement",
    # The NGN rail, mounted in Phase 2b once `paystack_service` was ported off Prisma. Nigeria is
    # the launch market, so until these existed the money path was reachable everywhere except
    # where we are launching.
    f"{PREFIX}/billing/subscriptions/paystack/initialize",
    f"{PREFIX}/billing/subscriptions/paystack/verify",
    # Stripe subscription lifecycle.
    f"{PREFIX}/billing/subscriptions/checkout",
    f"{PREFIX}/billing/subscriptions/sync-checkout",
    f"{PREFIX}/billing/subscriptions/portal",
    f"{PREFIX}/billing/subscriptions/cancel",
    # Google Play subscription verification. Android is the only shipped store.
    f"{PREFIX}/billing/subscriptions/google-play/verify",
    # History. It describes transactions that really happened.
    f"{PREFIX}/billing/credits/purchases",
    # The meter, read-only. Separate from `/entitlement` because the two change on different
    # clocks: entitlement when a learner pays, usage on every operation.
    f"{PREFIX}/billing/usage",
    # Provider callbacks.
    f"{PREFIX}/webhooks/stripe",
    f"{PREFIX}/webhooks/paystack",
    f"{PREFIX}/webhooks/google-play/rtdn",
]


class TestTheMoneyPathIsReachable:
    @pytest.mark.parametrize("path", REACHABLE)
    def test_endpoint_is_mounted(self, path, paths):
        assert path in paths


class TestWithdrawnEndpointsAreGone:
    """Removed with the products they sold, not merely unmounted."""

    @pytest.mark.parametrize(
        "path",
        [
            f"{PREFIX}/billing/credit-packs",
            f"{PREFIX}/billing/credit-packs/purchase",
            # Verified a credit-pack purchase and granted credits. Its replacement verifies
            # a Plus pass purchase and grants an inactive pass.
            f"{PREFIX}/billing/subscriptions/google-play/verify-product",
            # Nothing in the product asks a learner to watch an advertisement.
            f"{PREFIX}/billing/ads/stats",
            f"{PREFIX}/billing/ads/reward",
            # Adjusted `purchasedCreditsBalance`, which Phase 3 dropped. Usage is a window that
            # refills on its own, so there is no balance for support to top up, and pointing this
            # at the window would let support grant an allowance that expires in under five hours.
            # The replacement is a granted pass.
            f"{PREFIX}/billing/admin/credits/adjust",
        ],
    )
    def test_endpoint_is_absent(self, path, paths):
        assert path not in paths


class TestEndpointsThatCannotWorkAreNotServed:
    """A 404 says "not yet", which is true. A 500 says "broken", and invites a retry."""

    @pytest.mark.parametrize(
        "path",
        [
            # All three resolve into `referral_rewards_service`, which holds a
            # `PrismaClientRemoved` sentinel. They return when the reward they describe
            # exists — points, earned on seven distinct days of study.
            f"{PREFIX}/billing/referrals/stats",
            f"{PREFIX}/billing/referrals/claimable",
            f"{PREFIX}/billing/referrals/claim",
        ],
    )
    def test_endpoint_is_absent(self, path, paths):
        assert path not in paths

    def test_paystack_is_ported_and_holds_no_sentinel(self):
        """The inverse of the guard this replaces, and it earned its keep.

        Until Phase 2b this asserted `isinstance(paystack_service.db, PrismaClientRemoved)`, so that
        porting the service without mounting its routes would fail loudly — a conditional absence
        needs its condition asserted or it silently becomes permanent. The port happened, this test
        failed, and the routes went up. Now it asserts the opposite: the sentinel is gone, so nothing
        can quietly reintroduce a Prisma client here.
        """
        from src.domains.billing.services import paystack_service

        assert not hasattr(paystack_service, "db"), (
            "paystack_service has a `db` attribute again — it was ported to SQLAlchemy in "
            "Phase 2b and reads through `billing_repo`"
        )

    def test_the_referrals_endpoints_await_a_reward_rather_than_a_port(self):
        """The reason these three stay absent changed in Phase 3, so this guard did too.

        It used to assert `isinstance(referral_rewards_service.db, PrismaClientRemoved)` — the routes
        were absent because the service could not reach a database. Phase 3 ported the service, so that
        assertion now fails, and the honest reading is not "unmount the guard" but "the absence has a
        different cause". The three endpoints served `get_claimable_rewards`, `claim_referral_reward`
        and a stats shape carrying token totals; all three are *deleted*, because a claim against a
        daily credit limit describes a reward that no longer exists (Decision O).

        What is left is a code and a record of who referred whom, and no reward at all until the points
        ledger lands. So the guard is now on the functions rather than on the client: if any of them
        comes back, this fails and asks whether the routes should follow.
        """
        from src.domains.billing.services import referral_rewards_service

        assert not hasattr(referral_rewards_service, "db"), (
            "referral_rewards_service has a `db` attribute again — it was ported to SQLAlchemy in "
            "Phase 3"
        )
        for gone in (
            "REFERRAL_REWARDS",
            "track_referral_subscription",
            "get_claimable_rewards",
            "claim_referral_reward",
            "get_daily_limit_increase",
        ):
            assert not hasattr(referral_rewards_service, gone), (
                f"`{gone}` is back. Nothing tops up a usage window (§6.3); if a reward has been "
                f"redesigned, it grants points and redeems into passes, and the /referrals/* routes "
                f"should be rewritten onto that rather than onto this."
            )


class TestTheCatalogueIsPublished:
    def test_the_catalogue_appears_in_the_openapi_schema(self):
        """The OpenAPI tag metadata for `billing` was declared while the router was
        unmounted, so `/docs` advertised endpoints that nothing served. The generated client
        types are built from this schema; if it lies, a client typechecks cleanly against
        the lie.
        """
        schema = create_app().openapi()
        catalogue = schema["paths"][f"{PREFIX}/billing/plans/catalog"]["get"]
        ref = catalogue["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("PlanCatalogResponse")

    def test_the_published_plan_item_is_camel_case(self):
        """Clients read `trialDays` and `usageNote`. Nothing else in this domain publishes
        snake_case, and the mixed convention was only tolerable while nobody could see it.
        """
        schema = create_app().openapi()
        properties = schema["components"]["schemas"]["PlanItem"]["properties"]
        for field in ("priceCents", "trialDays", "usageNote", "scope"):
            assert field in properties
        for absent in ("price_cents", "trial_days", "usage_note"):
            assert absent not in properties
