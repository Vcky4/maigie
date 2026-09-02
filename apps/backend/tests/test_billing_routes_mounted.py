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
    # History and the support tool. Both describe transactions that really happened.
    f"{PREFIX}/billing/credits/purchases",
    f"{PREFIX}/billing/admin/credits/adjust",
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

    def test_the_referral_sentinel_is_still_the_reason(self):
        from src.domains.billing.services import referral_rewards_service
        from src.shared.infrastructure.unmigrated import PrismaClientRemoved

        assert isinstance(referral_rewards_service.db, PrismaClientRemoved), (
            "referral_rewards_service has been ported — the /referrals/* endpoints "
            "should now be rewritten onto the points ledger and mounted"
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
